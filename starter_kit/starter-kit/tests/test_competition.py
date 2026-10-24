"""Offline paired competition mechanics; injected agents never call hosted models."""

import copy
import json
import threading
import time

import pytest

from arena_kit.agents.naive import NaiveAgent
from arena_kit.agents.routed import RoutedAgent
from arena_kit.competition import CompetitionSim, _floor, _match, build_scenarios, feasible_reference, run_competition
from arena_kit.cost import must_haves_ok
from arena_kit.docs import standard_terms
from arena_kit.play import closing_move


class GuardedNaive(NaiveAgent):
    def decide(self, case, threads):
        decision = super().decide(case, threads)
        return closing_move(case, threads) if decision["message"].get("action") else decision


def test_five_paired_waves_have_stable_cases_and_json_results():
    result = run_competition({"first": GuardedNaive, "second": GuardedNaive},
                             scenarios=["simple_price", "payment_freight"], seeds=(7,), waves=5, workers=4)
    assert len(result["matches"]) == 20
    paired = {}
    for row in result["matches"]:
        paired.setdefault((row["scenario"], row["seed"], row["wave"]), []).append(row)
        assert row["case_id"].startswith(f"case:R{row['wave']}-C")
        assert row["model_attempts"] == 0 and row["valid"]
        assert 0 <= row["score"] <= 1
    for pair in paired.values():
        assert len(pair) == 2
        assert pair[0]["score"] == pair[1]["score"]
        assert pair[0]["cost_eur"] == pair[1]["cost_eur"]
    assert all(len(row["wave_points"]) == 5 for row in result["aggregate"].values())
    json.dumps(result, allow_nan=False)


def test_every_reference_is_a_feasible_accepted_deal_or_outside_option():
    for scenario in build_scenarios(3):
        reference = feasible_reference(scenario)
        sim = CompetitionSim(scenario, 1)
        opened = sim.open("local", f"case:R1-{scenario.case['case_id'][5:]}")
        session_id = opened["session_id"]
        sid = reference["supplier_id"]
        if sid:
            reply = sim.message("local", session_id, sid, {"term_sheets": [{"terms": reference["terms"]}]})
            assert reply["status"] == "agreed"
            assert must_haves_ok(scenario.case, reference["terms"], scenario.profiles[sid]["standard"])
            result = sim.message("local", session_id, sid, {"action": {
                "type": "accept", "term_sheet_id": reply["term_sheets"][0]["term_sheet_id"]}})["result"]
            assert result["c_deal"] == reference["cost"]
        else:
            sid = scenario.case["suppliers"][0]
            sim.message("local", session_id, sid, {"action": {"type": "walk_away"}})
            assert sim.sessions[session_id]["result"]["realized_cost"] == reference["cost"]


def test_default_scenarios_cover_varied_profiles_and_holdouts():
    training, repeated, holdout = build_scenarios(11), build_scenarios(11), build_scenarios(11, holdout=True)
    assert training == repeated
    assert len(training) == 10 and {s.name for s in training} == {s.name for s in holdout}
    assert any(a.profiles != b.profiles for a, b in zip(training, holdout, strict=True))
    result = run_competition({"guarded": GuardedNaive}, seeds=(11,), waves=1, workers=4)
    assert len(result["matches"]) == 10
    assert all(row["error"] is None for row in result["matches"])
    assert all(row["valid"] for row in result["matches"])


def test_concessions_keep_the_floor_of_the_actual_standing_package():
    scenario = next(s for s in build_scenarios(0) if s.name == "payment_freight")
    sim = CompetitionSim(scenario, 1)
    opened = sim.open("local", f"case:R1-{scenario.case['case_id'][5:]}")
    sid, session_id = "sup:A", opened["session_id"]
    terms = {"payment_days": 60, "incoterm": "DAP"}
    terms["unit_price"] = _floor(scenario.case, scenario.profiles[sid], terms) + 0.02
    agreed = sim.message("local", session_id, sid, {"term_sheets": [{"terms": terms}]})
    assert agreed["status"] == "agreed"
    reply = sim.message("local", session_id, sid,
                        {"term_sheets": [{"terms": {**terms, "unit_price": 0.01}}]})
    quoted = reply["term_sheets"][0]["terms"]
    assert quoted["unit_price"] >= _floor(scenario.case, scenario.profiles[sid], quoted)
    assert quoted["payment_days"] == 60 and quoted["incoterm"] == "DAP"


def test_agents_only_receive_public_data_and_cannot_mutate_private_state():
    seen = []

    class Inspect:
        def decide(self, case, threads):
            text = json.dumps({"case": case, "threads": threads})
            assert '"profiles"' not in text and '"floor"' not in text and '"reference"' not in text
            assert '"full_cost"' not in text
            assert all(s.name not in text for s in build_scenarios(1))
            sid = next(iter(threads))
            sheet = threads[sid][-1]["term_sheets"][0]["term_sheet_id"]
            seen.append(case["buyer"]["need_per_year"])
            case["buyer"]["need_per_year"] = 999_999
            threads[sid][-1]["term_sheets"][0]["terms"]["unit_price"] = 0.01
            return {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": sheet}}}

    report = run_competition({"inspect": Inspect}, scenarios=["simple_price"], seeds=(1,), waves=1)
    row = report["matches"][0]
    assert seen == [1000] and row["cost_eur"] > 30_000
    assert row["valid"] and row["error"] is None
    assert row["decisions"][0]["sent_action"]["action"] == "accept"
    assert row["terms"]["unit_price"] > 1


def test_parallel_workers_really_overlap():
    barrier = threading.Barrier(2, timeout=3)

    class WaitForPeer:
        def decide(self, case, threads):
            barrier.wait()
            return closing_move(case, threads)

    result = run_competition({"a": WaitForPeer, "b": WaitForPeer}, scenarios=["simple_price"],
                             seeds=(0,), waves=1, workers=2)
    assert result["peak_active_matches"] == 2
    assert all(row["error"] is None for row in result["matches"])


def test_real_deadline_route_attempts_and_fallback_are_recorded():
    observed = []

    def complete(system, user, *, deadline):
        observed.append(deadline - time.monotonic())
        raise TimeoutError("synthetic provider timeout")

    result = run_competition({"routed": lambda: RoutedAgent(complete)}, scenarios=["contradicted_claims"],
                             seeds=(0,), waves=1, decision_seconds=0.1)
    row = result["matches"][0]
    assert observed and all(0 < value <= 0.1 for value in observed)
    assert row["model_attempts"] == len(observed)
    assert row["fallbacks"] >= 1 and row["routes"]["fast"] >= 1


def test_supplier_delay_is_virtual_and_other_threads_can_timeout():
    scenario = next(s for s in build_scenarios(0) if s.name == "slow_reply")
    scenario = copy.deepcopy(scenario)
    for profile in scenario.profiles.values():
        profile["reply_delay_s"] = 181
    row = _match("guarded", GuardedNaive, scenario, 1, 1, 25)
    assert row["supplier_delay_s"] >= 181
    assert row["virtual_elapsed_s"] >= 181
    assert row["wall_s"] < 5 and row["thread_timeouts"] >= 6


def test_errors_are_isolated_and_do_not_leak_exception_messages():
    def broken():
        raise ValueError("synthetic-sensitive-text")

    result = run_competition({"broken": broken, "good": GuardedNaive}, scenarios=["simple_price"],
                             seeds=(0,), waves=1, workers=2)
    assert result["aggregate"]["broken"]["errors"] == 1
    assert result["aggregate"]["good"]["errors"] == 0
    assert "synthetic-sensitive-text" not in json.dumps(result)


def test_failed_decision_latency_is_not_dropped_from_the_report():
    class FailsSlowly:
        def decide(self, case, threads):
            time.sleep(0.02)
            raise TimeoutError("synthetic failure")

    result = run_competition({"slow_failure": FailsSlowly}, scenarios=["simple_price"], seeds=(0,), waves=1,
                             decision_seconds=0.01)
    row = result["matches"][0]
    assert row["decision_wall_s"] >= 0.02 and not row["valid"]
    assert row["decisions"][0]["elapsed_over_budget"]
    assert result["aggregate"]["slow_failure"]["decision_p95_s"] >= 0.02


def test_accepting_a_must_have_violation_is_not_a_valid_win():
    class AcceptFirst:
        def decide(self, case, threads):
            sid = next(iter(threads))
            sheet = threads[sid][-1]["term_sheets"][0]["term_sheet_id"]
            return {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": sheet}}}

    report = run_competition({"invalid": AcceptFirst}, scenarios=["leadtime_repair"], seeds=(0,), waves=1)
    row = report["matches"][0]
    assert row["deal"] and not row["valid"] and row["score"] == 0 and row["regret_eur"] is None


def test_missing_defaults_are_absent_until_the_supplier_clarifies():
    seen = []

    class Clarify:
        def decide(self, case, threads):
            sid = next(iter(threads))
            known = standard_terms(threads[sid])
            seen.append(known)
            if len(seen) == 1:
                assert "payment_days" not in known
                return {"supplier_id": sid, "message": {"body": "Please confirm your standard payment days."}}
            assert known["payment_days"] == 14
            return closing_move(case, threads)

    report = run_competition({"clarify": Clarify}, scenarios=["incomplete_defaults"], seeds=(0,), waves=1)
    assert len(seen) == 2 and report["matches"][0]["error"] is None


@pytest.mark.parametrize("options", [{"waves": 0}, {"workers": 0}, {"seeds": ()},
                                    {"scenarios": ["missing"]}, {"decision_seconds": float("nan")}])
def test_invalid_competition_configuration(options):
    with pytest.raises(ValueError):
        run_competition({"guarded": GuardedNaive}, **options)
