"""Local routing/validation tests; injected completions never contact a provider."""

import copy
import json

import pytest

from arena_kit.agents.routed import RoutedAgent
from arena_kit.cost import total_cost
from arena_kit.sim import load_cases


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def scenario():
    case = copy.deepcopy(load_cases()["case:S01"])
    sid = case["suppliers"][0]
    threads = {sid: [{"from": "supplier", "status": "open", "body": "Our proposal.",
                     "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 20, "payment_days": 30}}]}]}
    return case, threads, sid


def completion(sid, calls, reply=None, clock=None, delay=0, error=None):
    def complete(system, user, *, deadline):
        calls.append({"deadline": deadline, "prompt": user})
        if clock:
            clock.now += delay
        if error:
            raise error
        return reply if reply is not None else json.dumps({
            "supplier_id": sid, "action": "send", "body": "Could you improve your price?",
            "term_sheets": [{"unit_price": 18, "payment_days": 30}]})

    return complete


def after_opening(threads, sid):
    threads[sid].insert(0, {"from": "buyer", "body": "A previous question."})


def test_simple_known_opening_is_a_script_not_an_acceptance(scenario):
    case, threads, sid = scenario
    calls = []
    agent = RoutedAgent(completion(sid, calls))
    move = agent.decide(case, threads)
    assert calls == [] and not move["message"].get("action")
    offered = move["message"]["term_sheets"][0]["terms"]
    assert offered == {"unit_price": 18.0, "payment_days": 30}
    assert total_cost(case, offered) < total_cost(case, threads[sid][-1]["term_sheets"][0]["terms"])
    assert agent.last_decision["route"] == "script"
    assert agent.last_decision["reason"] == "simple_opening_counter"


def test_semantic_followup_uses_fast_once_and_logs_its_budget(scenario, caplog):
    case, threads, sid = scenario
    after_opening(threads, sid)
    calls, strong_calls, clock = [], [], Clock()
    agent = RoutedAgent(completion(sid, calls), completion(sid, strong_calls), clock=clock)
    with caplog.at_level("INFO", logger="arena"):
        agent.decide_timed(case, threads, deadline=140)
    assert len(calls) == 1 and strong_calls == []
    assert calls[0]["deadline"] == 125
    assert agent.last_decision == {"route": "fast", "reason": "semantic_decision", "elapsed_s": 0,
                                   "budget_s": 25, "fallback": False}
    assert "route=fast" in caplog.text and "Our proposal" not in caplog.text


@pytest.mark.parametrize(("seconds", "route"), [(25, "strong"), (19, "fast")])
def test_complex_case_selects_one_model_by_available_time(scenario, seconds, route):
    case, threads, sid = scenario
    case["buyer"]["category_goal"] = "secure_supply"
    calls, strong_calls, clock = [], [], Clock()
    agent = RoutedAgent(completion(sid, calls), completion(sid, strong_calls), clock=clock)
    agent.decide_timed(case, threads, deadline=clock() + seconds)
    assert (len(calls), len(strong_calls)) == ((0, 1) if route == "strong" else (1, 0))
    assert agent.last_decision["route"] == route


def test_strong_is_optional_and_both_routes_share_the_same_context(scenario):
    case, threads, sid = scenario
    case["buyer"]["category_goal"] = "stabilise"
    fast, strong = [], []
    clock = Clock()
    agent = RoutedAgent(completion(sid, fast), clock=clock)
    agent.decide(case, threads)
    assert agent.last_decision["reason"] == "strong_unconfigured"
    agent.strong = completion(sid, strong)
    agent.decide(case, threads)
    assert len(fast) == len(strong) == 1
    assert fast[0]["prompt"] == strong[0]["prompt"]


@pytest.mark.parametrize("terminal", ["agreed", "final_offer"])
def test_terminal_offer_closes_without_any_model(scenario, terminal):
    case, threads, sid = scenario
    threads[sid][-1]["status"] = terminal
    calls = []
    agent = RoutedAgent(completion(sid, calls))
    move = agent.decide(case, threads)
    assert calls == [] and move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:1"}
    assert agent.last_decision["reason"] == "terminal_offers"


@pytest.mark.parametrize("timing", [{"deadline": 100}, {"deadline": 120, "wave_seconds_left": 90}])
def test_expired_budget_or_wave_reserve_never_starts_a_model(scenario, timing):
    case, threads, sid = scenario
    after_opening(threads, sid)
    calls = []
    agent = RoutedAgent(completion(sid, calls), clock=Clock())
    move = agent.decide_timed(case, threads, **timing)
    assert calls == [] and move["message"]["action"]["type"] == "accept"
    assert agent.last_decision["reason"] == "deadline_close"


def test_missing_quote_defaults_get_one_clarification_and_cannot_be_accepted(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["term_sheets"][0]["terms"].pop("payment_days")
    calls = []
    agent = RoutedAgent(completion(sid, calls, json.dumps({"supplier_id": sid, "action": "accept"})))
    ask = agent.decide(case, threads)
    assert calls == [] and "payment_days" in ask["message"]["body"]
    assert ask["message"]["term_sheets"] == []
    threads[sid].append({**ask["message"], "from": "buyer"})
    assert agent.decide(case, threads)["message"]["action"]["type"] == "walk_away"
    assert len(calls) == 1 and agent.last_decision["fallback"]


def test_quote_pdf_default_is_used_and_must_have_is_checked(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["term_sheets"][0]["terms"].pop("payment_days")
    threads[sid][-1]["attachments"] = [{"name": "quote.pdf", "text":
        "Our standard terms\nPayment terms (days net) 14"}]
    calls = []
    agent = RoutedAgent(completion(sid, calls))
    assert agent.closing_move(case, threads)["message"]["action"]["type"] == "accept"
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    assert agent.closing_move(case, threads)["message"]["action"]["type"] == "walk_away"


def test_better_alternative_blocks_model_and_fallback_acceptance(scenario):
    case, threads, sid = scenario
    after_opening(threads, sid)
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 10, "payment_days": 30}}
    calls = []
    agent = RoutedAgent(completion(sid, calls, json.dumps({"supplier_id": sid, "action": "accept"})))
    assert agent.decide(case, threads)["message"]["action"]["type"] == "walk_away"
    assert agent.last_decision["fallback"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "18", -1, 0])
def test_nonfinite_or_invalid_price_is_rejected_before_completion_or_arithmetic(scenario, invalid):
    case, threads, sid = scenario
    after_opening(threads, sid)
    reply = json.dumps({"supplier_id": sid, "action": "send", "term_sheets": [{"unit_price": invalid}]})
    agent = RoutedAgent(completion(sid, [], reply))
    assert agent.decide(case, threads)["message"]["action"]["type"] == "accept"
    assert agent.last_decision["fallback"]  # accepted known standing terms, not the bad proposed price


@pytest.mark.parametrize("decision", [
    {"action": "send", "term_sheets": [{"unit_price": 18, "payment_days": 17}]},
    {"action": "send", "term_sheets": [{"unit_price": 18, "payment_days": 14.5}]},
    {"action": "send", "term_sheets": [{"unit_price": 18, "invented_term": 1}]},
    {"action": "send", "term_sheets": [{}] * 4},
    {"action": "send", "body": "Verified at idx:invented@2026-01."},
    {"action": "accept", "term_sheet_id": "offer:stale"},
])
def test_guard_rejects_invalid_fields_options_claims_and_stale_offer_selection(scenario, decision):
    case, threads, sid = scenario
    agent = RoutedAgent(None)
    assert agent.validate(case, threads, {"supplier_id": sid, **decision}) is None


def test_closure_selects_only_a_live_known_valid_supplier(scenario):
    case, threads, sid = scenario
    case["suppliers"] += ["sup:valid", "sup:ended"]
    threads["sup:valid"] = copy.deepcopy(threads[sid])
    threads["sup:valid"][-1]["term_sheets"][0] = {
        "term_sheet_id": "offer:valid", "terms": {"unit_price": 22, "payment_days": 60}}
    threads["sup:ended"] = [{"from": "supplier", "status": "walked_away"}]
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 60}]
    agent = RoutedAgent(None)
    move = agent.closing_move(case, threads)
    assert move["supplier_id"] == "sup:valid"
    assert move["message"]["action"]["term_sheet_id"] == "offer:valid"
    assert agent.validate(case, threads, {"supplier_id": "sup:ended", "action": "accept"}) is None


@pytest.mark.parametrize("failure", ["timeout", "late", "malformed"])
def test_failure_falls_back_without_trying_a_second_provider(scenario, failure):
    case, threads, sid = scenario
    case["buyer"]["category_goal"] = "secure_supply"
    fast, strong, clock = [], [], Clock()
    bad = completion(sid, strong, "not JSON" if failure == "malformed" else None,
                     clock=clock, delay=26 if failure == "late" else 0,
                     error=TimeoutError("synthetic timeout") if failure == "timeout" else None)
    agent = RoutedAgent(completion(sid, fast), bad, clock=clock)
    move = agent.decide(case, threads)
    assert len(strong) == 1 and fast == []
    assert move["message"]["action"]["type"] == "accept"
    assert agent.last_decision["route"] == "strong" and agent.last_decision["fallback"]


def test_invalid_supplier_standard_cannot_bypass_term_bounds(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["term_sheets"][0]["terms"].pop("payment_days")
    threads[sid][-1]["attachments"] = [{"name": "quote.pdf", "text":
        "Our standard terms\nPayment terms (days net) 900"}]
    assert RoutedAgent(None).closing_move(case, threads)["message"]["action"]["type"] == "walk_away"


def test_must_have_needs_evidence_even_when_it_is_not_negotiable(scenario):
    case, threads, _ = scenario
    case["fixed_terms"].pop("lead_time_days")
    case["buyer"]["must_haves"] = [{"field": "lead_time_days", "op": "<=", "value": 42}]
    assert RoutedAgent(None).closing_move(case, threads)["message"]["action"]["type"] == "walk_away"
