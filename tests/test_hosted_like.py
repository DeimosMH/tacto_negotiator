"""The calibrated supplier must track recorded hosted replies better than the old scripted rule."""

import json
import statistics
from pathlib import Path

from tacto_negotiator.hosted_like import PROFILE_RANGES, build_replica_scenarios, counter

ROOT = Path(__file__).resolve().parents[1]


def replay_errors(predict):
    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    errors, hits = [], []
    for session in feedback["sessions"]:
        goal = feedback["cases"][session["case_id"]]["buyer"].get("category_goal", "routine")
        profile = {name: sum(r) / 2 for name, r in zip(
            ("a1", "cap1", "a2", "depth", "final_round", "final_gap"), PROFILE_RANGES[goal], strict=True)}
        state, pending = {}, {}
        for event in session["trace"]:
            move = event["move"]
            if event["actor"] == "buyer":
                sheets = [s.get("terms", s) for s in move.get("term_sheets") or []]
                if move.get("supplier_id") in state and sheets:
                    pending[move["supplier_id"]] = min(s["unit_price"] for s in sheets if s.get("unit_price"))
                continue
            sid, price = event["actor"], (move.get("terms") or {}).get("unit_price")
            if price is None:
                continue
            if sid in pending and sid in state:
                thread = state[sid]
                ask = pending.pop(sid)
                thread["round"] += 1
                thread["gap"] += max(0, (thread["price"] - ask) / thread["p0"] * 100)
                predicted, status = predict(profile, thread, ask)
                errors.append(abs(predicted - price) / thread["p0"] * 100)
                hits.append(status[:4] == move["status"][:4])
                thread["last"] = ask
            state.setdefault(sid, {"p0": price, "round": 0, "gap": 0.0, "last": None})["price"] = price
    return statistics.mean(errors), statistics.mean(hits), len(errors)


def calibrated(profile, thread, ask):
    return counter(profile, thread["p0"], thread["round"], thread["price"], ask, thread["gap"], thread["last"])


def old_rule(profile, thread, ask):  # sim.py: fixed 2 % step to a 90 % floor, final at round 5
    price = max(thread["p0"] * 0.9, thread["price"] - thread["p0"] * 0.02)
    if ask >= price:
        return ask, "agreed"
    return price, "final_offer" if thread["round"] >= 5 else "open"


def test_calibrated_supplier_tracks_hosted_replies_better_than_the_scripted_rule():
    error, accuracy, steps = replay_errors(calibrated)
    old_error, old_accuracy, _ = replay_errors(old_rule)
    assert steps >= 40
    assert error < old_error and accuracy >= old_accuracy
    assert error < 2.5  # % of the opening price, with per-goal midpoint profiles (no per-case fitting)


def test_replicas_use_recorded_hosted_references_and_seeded_private_profiles():
    first, again, other = build_replica_scenarios(0), build_replica_scenarios(0), build_replica_scenarios(1)
    assert len(first) == 10 and [s.profiles for s in first] == [s.profiles for s in again]
    assert [s.profiles for s in first] != [s.profiles for s in other]
    assert all(s.reference["c_open"] > s.reference["c_best"] for s in first)


def test_index_clause_is_worth_the_rise_it_passes_on_and_ranks_like_hosted_p02(monkeypatch):
    from tacto_negotiator.agents import routed
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.compete import unavailable
    from tacto_negotiator.competition import _match
    from tacto_negotiator.data import DataPackage
    from tacto_negotiator.hosted_like import DATA, clause_reserve, clause_value, index_rises

    package = DataPackage(DATA)
    replicas = {s.name: s for s in build_replica_scenarios(0)}
    copper, steel = replicas["P02"].case, replicas["P08"].case
    rises = {**index_rises(copper, package), **index_rises(steel, package)}
    assert round(rises["CU-NOTATION"], 3) == 0.477 and round(rises["AS-14404"], 3) == 0.059
    wide = copper["term_options"]["index_clause"][-1]  # 5 % deadband: hosted 371.95 -> 349.05, 6.2 % of 370.96
    assert abs(clause_value(wide, rises) - 0.062) < 0.005
    assert all(clause_value(c, rises) < 0.02 for c in steel["term_options"]["index_clause"])
    opening = {"index_clause": None}
    assert clause_reserve(copper, opening, rises, 0.05) == 0.05  # no clause: the floor stays at the opening
    assert clause_reserve(steel, opening, rises, 0.18) < 0.005

    def run(name, ratio):
        monkeypatch.setattr(routed, "CLAUSE_RISE_RATIO", ratio)
        return _match("scripts", lambda: RoutedAgent(unavailable, package=package), replicas[name], 1, 5, 25)["score"]

    # hosted P02: 0.68-0.74 leading with the clause sheet, 0.39-0.50 without; P08 clause sheets moved nothing
    with_clause, without = run("P02", routed.CLAUSE_RISE_RATIO), run("P02", 1e9)
    assert with_clause > 0.65 and with_clause > without + 0.1
    assert run("P08", routed.CLAUSE_RISE_RATIO) == run("P08", 1e9)


def test_rfq_rounds_stay_scripted_and_idle_bidders_are_served_while_another_replies():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.compete import unavailable
    from tacto_negotiator.competition import _match

    rfq = next(s for s in build_replica_scenarios(0) if s.name == "P05")  # three bidders, 15-40 s replies
    row = _match("scripts", lambda: RoutedAgent(unavailable), rfq, 1, 5, 25)
    # Bounded RFQ concessions are priced with each bidder's standard terms: no model (here failing) call.
    assert row["model_attempts"] == 0 and row["routes"] == {"script": len(row["decisions"])}
    concurrent = [d for d in row["decisions"] if d.get("concurrent")]
    assert concurrent and all(d["sent_action"]["action"] == "send" for d in concurrent)
    assert row["thread_timeouts"] == 0 and row["score"] > 0.84


def test_replica_checks_claims_against_the_whole_data_package():
    from tacto_negotiator.hosted_like import HostedLikeSim

    scenario = next(s for s in build_replica_scenarios(0) if s.name == "P08")
    sim = HostedLikeSim(scenario, 1)
    session = sim.open("team", scenario.case["case_id"])
    checker = sim.sessions[session["session_id"]]["checker"]
    claim = {"type": "index_change", "refs": ["idx:AS-14404@2026-06", "idx:AS-14404@2026-09"]}
    assert checker.check("sup:70710", [claim]) == [{"index": 0, "result": "verified"}]


def test_out_of_sample_bench_fixes_hold():
    """The never-hosted practice cases (bench `oos`): the faults the bench found stay fixed."""
    import json as _json
    from pathlib import Path as _Path

    from tacto_negotiator.competition import run_competition
    from tacto_negotiator.compete import make_factories
    from tacto_negotiator.data import DataPackage as _Package
    from tacto_negotiator.hosted_like import build_oos_scenarios

    root = _Path(__file__).resolve().parent.parent
    package = _Package(root / "data")
    cases = {c["case_id"]: c for c in _json.loads((root / "tests/fixtures/practice_cases.json").read_text())["cases"]}
    # motor cable is priced per 100 m in the case and in should-cost: no division by the price unit
    assert 290 < package.supplier_estimates(cases["case:P14"])["sup:70560"]["full_cost"] < 310
    assert {s.name for s in build_oos_scenarios(0)} == {"P06", "P09", "P12", "P13", "P14", "P15", "P16", "P17", "P19"}
    result = run_competition(make_factories(["scripts"], package=package), ["P13", "P14", "P06"], [0], waves=1,
                             workers=3, bench="oos", timing="final")
    scores = {m["scenario"]: m["score"] for m in result["matches"]}
    assert scores["P14"] > 0.8 and scores["P13"] > 0.7 and scores["P06"] > 0  # clause sheet, anchor floor, no zero
    opening = next(m for m in result["matches"] if m["scenario"] == "P13")["decisions"][0]
    assert all(t["unit_price"] >= 12.2 for t in opening["sent_action"]["term_sheets"])  # 17.44 x (1 - 0.25 - 0.05)
