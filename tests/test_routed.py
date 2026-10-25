"""Local routing/validation tests; injected completions never contact a provider."""

import copy
import json

import pytest

from tacto_negotiator.agents.levers import near_close_sheet
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.cost import total_cost
from tacto_negotiator.play import closing_move
from tacto_negotiator.sim import load_cases


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
            "term_sheets": [{"unit_price": 15, "payment_days": 30}]})  # within the leverage anchor

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
    assert offered == {"unit_price": 15.0, "payment_days": 30}  # 25 % leverage anchor
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
    assert calls[0]["deadline"] == 140  # the 40 s model budget, capped by the given deadline
    assert agent.last_decision == {"case_id": case["case_id"], "route": "fast", "reason": "semantic_decision",
                                   "elapsed_s": 0, "budget_s": 40, "fallback": False}
    assert f"case={case['case_id']} route=fast" in caplog.text and "Our proposal" not in caplog.text


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
    held = agent.decide(case, threads)["message"]  # never accepted; rounds left, so no walk-away either
    assert not held.get("action") and "complete term sheet" in held["body"]
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
    move = agent.decide(case, threads)["message"]  # neither accepted nor walked: rounds are left to beat it
    assert (move.get("action") or {}).get("type") not in {"accept", "walk_away"}
    assert agent.last_decision["fallback"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "18", -1, 0])
def test_nonfinite_or_invalid_price_is_rejected_before_completion_or_arithmetic(scenario, invalid):
    case, threads, sid = scenario
    after_opening(threads, sid)
    reply = json.dumps({"supplier_id": sid, "action": "send", "term_sheets": [{"unit_price": invalid}]})
    agent = RoutedAgent(completion(sid, [], reply))
    move = agent.decide(case, threads)
    assert agent.last_decision["fallback"] and "action" not in move["message"]
    price = move["message"]["term_sheets"][0]["terms"]["unit_price"]  # a scripted counter, not the bad price
    assert type(price) is float and 0 < price < 20


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


def test_current_alternative_sheet_can_be_cited_without_becoming_the_accepted_offer(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["term_sheets"].append({"term_sheet_id": "offer:1-a", "terms": {
        "unit_price": 19, "payment_days": 14}})
    agent = RoutedAgent(None)
    assert agent.validate(case, threads, {"supplier_id": sid, "action": "send",
                                         "body": "Your offer:1-a quotes EUR 19; please improve it."})
    accepted = agent.validate(case, threads, {"supplier_id": sid, "action": "accept"})
    assert accepted["message"]["action"]["term_sheet_id"] == "offer:1"


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
                     clock=clock, delay=41 if failure == "late" else 0,
                     error=TimeoutError("synthetic timeout") if failure == "timeout" else None)
    agent = RoutedAgent(completion(sid, fast), bad, clock=clock)
    move = agent.decide(case, threads)
    assert len(strong) == 1 and fast == []
    if failure == "late":  # no time left: close on the opening
        assert move["message"]["action"]["type"] == "accept"
    else:  # time left: counter instead of accepting an untouched opening
        assert "action" not in move["message"] and move["message"]["term_sheets"]
    assert agent.last_decision["route"] == "strong" and agent.last_decision["fallback"]


@pytest.mark.parametrize("goal", ["quality_first", "secure_supply", "stabilise"])
def test_invalid_model_can_repair_a_must_have_instead_of_abandoning_the_case(scenario, goal):
    case, threads, sid = scenario
    case["buyer"]["category_goal"] = goal  # hosted P10 (secure_supply) walked away in round 1
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 60}]
    case["term_options"] = {"payment_days": [14, 30, 60]}
    threads[sid][-1]["body"] = "Please explain the new requirement."
    calls = []
    agent = RoutedAgent(completion(sid, calls, "invalid JSON"))
    move = agent.decide(case, threads)
    assert len(calls) == 1 and agent.last_decision["fallback"]
    assert not move["message"].get("action")
    assert all(s["terms"]["payment_days"] == 60 for s in move["message"]["term_sheets"])


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


def test_unreadable_latest_quote_blocks_acceptance_and_gets_one_clarification(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["attachments"] = [{"name": "quote.pdf", "read_failed": True}]
    calls = []
    agent = RoutedAgent(completion(sid, calls, json.dumps({"supplier_id": sid, "action": "accept"})))
    assert agent.validate(case, threads, {"supplier_id": sid, "action": "accept"}) is None
    ask = agent.decide(case, threads)
    assert calls == [] and "supplier quote defaults" in ask["message"]["body"]
    threads[sid].append({**ask["message"], "from": "buyer"})
    held = agent.decide(case, threads)["message"]  # never accepted; rounds left, so no walk-away either
    assert not held.get("action")
    assert len(calls) == 1


def test_a_new_readable_quote_clears_an_older_document_failure(scenario):
    case, threads, sid = scenario
    earlier = copy.deepcopy(threads[sid][-1])
    threads[sid][-1]["attachments"] = [{"name": "quote.pdf", "read_failed": True}]
    newer = {**earlier, "attachments": [{"name": "quote.pdf", "text":
        "Our standard terms\nPayment terms (days net) 30"}]}
    threads[sid].append(newer)
    assert RoutedAgent(None).closing_move(case, threads)["message"]["action"]["type"] == "accept"


def test_latest_quote_failure_is_not_hidden_by_an_older_readable_quote(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["attachments"] = [{"name": "quote.pdf", "text":
        "Our standard terms\nPayment terms (days net) 30"}]
    newer = copy.deepcopy(threads[sid][-1])
    newer["attachments"] = [{"name": "quote.pdf", "read_failed": True}]
    threads[sid].append(newer)
    assert RoutedAgent(None).closing_move(case, threads)["message"]["action"]["type"] == "walk_away"


def test_post_deadline_closure_preserves_the_failed_model_attempt(scenario, caplog):
    case, threads, sid = scenario
    after_opening(threads, sid)
    clock = Clock()
    agent = RoutedAgent(completion(sid, [], clock=clock, delay=10), clock=clock)
    with caplog.at_level("INFO", logger="arena"):
        agent.decide_timed(case, threads, deadline=clock() + 10)
        failed = dict(agent.last_decision)
        agent.closing_move(case, threads)
    assert failed["route"] == "fast" and failed["fallback"] and failed["reason"] == "TimeoutError"
    assert agent.last_decision == {**failed, "closure": "deadline_close"}
    assert f"case={case['case_id']} closure=deadline_close attempted_route=fast" in caplog.text
    another = {**case, "case_id": "case:new"}
    agent.closing_move(another, threads)
    assert agent.last_decision["case_id"] == "case:new" and not agent.last_decision["fallback"]


def test_wasted_claim_ids_are_stripped_before_sending(scenario):
    case, threads, sid = scenario
    case["refs"] = ["quote:case"]
    threads[sid].insert(0, {"from": "buyer", "body": "Forecast fc:A@2026-09 rose 6%."})
    body = RoutedAgent._strip_wasted_claims(
        case, threads, "Again fc:A@2026-09, also fc:B@2026-09 (fc:B@2026-09); rival quote:other; ours quote:case.")
    assert "fc:" not in body and "quote:other" not in body and "quote:case" in body


def test_a_second_window_of_a_cited_index_is_stripped_as_duplicate(scenario):
    case, threads, sid = scenario
    threads[sid].insert(0, {"from": "buyer", "body": "X fell 3% (idx:X@2025-08, idx:X@2026-08)."})
    body = RoutedAgent._strip_wasted_claims(case, threads, "X fell (idx:X@2025-11, idx:X@2026-04); Y (idx:Y@2026-08).")
    assert "idx:X" not in body and "idx:Y@2026-08" in body


def grounded(scenario, body, sheets=None, supplier_says="Our proposal."):
    case, threads, sid = scenario
    threads[sid][0]["body"] = supplier_says
    return RoutedAgent(completion(sid, []))._grounded_body(
        case, threads, sid, body, sheets or [{"unit_price": 15, "payment_days": 30, "contract_months": 12}])


def test_supplier_roles_need_a_source_and_are_named_once(scenario):
    ask = "Could your pricing committee review it? Our engineering team will assess the variant."
    assert grounded(scenario, ask) == "Our engineering team will assess the variant."
    said = "Changes need our pricing committee."
    assert grounded(scenario, ask, supplier_says=said) == ask
    case, threads, sid = scenario
    threads[sid].append({"from": "buyer", "body": "Could your pricing committee review it?"})
    assert grounded(scenario, ask, supplier_says=said) == "Our engineering team will assess the variant."
    # A cited claim stays even when it mentions a role: the arena checks it.
    claim = "Your forecast fc:A@2026-09 records 4.2 %, as your owner expected."
    assert grounded(scenario, claim) == claim


def test_id_less_percentages_are_dropped_but_claims_and_deadbands_stay(scenario):
    body = ("As the change exceeds 2 %, please review. Copper is roughly a fifth of the cost.\n"
            "Our scorecard sc:A@2026Q3 shows 85.0 %. The same scorecard shows 302 ppm. One sheet has a 5% deadband.")
    clause = {"series": "AS", "material_share": 0.4, "deadband_pct": 5.0, "base_month": "2026-08"}
    out = grounded(scenario, body, [{"unit_price": 15, "payment_days": 30, "index_clause": clause}])
    assert out == ("Our scorecard sc:A@2026Q3 shows 85.0 %. The same scorecard shows 302 ppm. "
                   "One sheet has a 5% deadband.")
    assert grounded(scenario, "Your requested 9.5 % is high.", supplier_says="We need 9.5 % more.") == \
        "Your requested 9.5 % is high."


def test_prose_term_values_must_match_an_attached_sheet_or_the_supplier(scenario):
    body = ("Dear Ms A,\n\nWe commit 6650 pcs per year over 12- or 36-month terms. One option has 75-day payment.\n"
            "Your 45 days are noted.\n\nKind regards")
    out = grounded(scenario, body, supplier_says="We can do 45 days.")
    assert out == ("Dear Ms A,\n\nWe commit 6650 pcs per year over 12-month terms.\nYour 45 days are noted.\n\n"
                   "Kind regards")


def test_validate_sends_the_grounded_body(scenario):
    case, threads, sid = scenario
    move = RoutedAgent(completion(sid, [])).validate(case, threads, {
        "supplier_id": sid, "action": "send", "term_sheets": [{"unit_price": 15, "payment_days": 30}],
        "body": "Could you improve? As this exceeds 2 %, please ask your management for an authorized review."})
    body = move["message"]["body"]  # the fact bank may append the round's assigned fact
    assert body.startswith("Could you improve?") and "management" not in body and "2 %" not in body
def test_model_round_gets_its_assigned_fact_and_validate_enforces_it(scenario):
    case, threads, sid = scenario
    after_opening(threads, sid)
    part = case["parts"][0]
    case["facts"] = {"fc:P@2026-09": {"part": part, "change_pct": 12.0},
                     f"cost:{part[5:]}": {"part": part, "unit_cost": 12.5, "price_unit": 1}}
    calls = []
    reply = json.dumps({"supplier_id": sid, "action": "send", "term_sheets": [{"unit_price": 15, "payment_days": 30}],
                        "body": "Our forecast fc:P@2026-09 rose 12%. Could you improve?\n\nKind regards"})
    agent = RoutedAgent(completion(sid, calls, reply))
    move = agent.decide(case, threads)
    assert "USE THIS FACT NOW" in calls[0]["prompt"] and "cost:" in calls[0]["prompt"].split("USE THIS FACT NOW")[1]
    body = move["message"]["body"]
    # one new fact after round 1: the assigned should-cost goes in, the forecast waits for a later round
    assert "fc:P" not in body and f"EUR 12.5 per unit (cost:{part[5:]})" in body
    assert body.endswith("Kind regards") and "Could you improve?" in body


def test_a_message_without_a_new_fact_or_a_sheet_moves_a_step(scenario):
    case, threads, sid = scenario
    case["facts"] = {"fc:P@2026-09": {"part": case["parts"][0], "change_pct": 12.0}}
    threads[sid] += [{"from": "buyer", "body": "Forecast (fc:P@2026-09) rose 12%.",
                      "term_sheets": [{"terms": {"unit_price": 15, "payment_days": 30}}]},
                     {"from": "supplier", "status": "open", "body": "Counter.",
                      "term_sheets": [{"term_sheet_id": "offer:2", "terms": {"unit_price": 19, "payment_days": 30}}]}]
    move = RoutedAgent(None).validate(case, threads, {"supplier_id": sid, "action": "send",
                                                      "body": "When can you deliver?"})
    [sheet] = move["message"]["term_sheets"]
    assert 15 < sheet["terms"]["unit_price"] < 19  # a real step instead of an empty round


def test_parse_takes_the_first_complete_object_despite_trailing_braces():
    from tacto_negotiator.agents.llm import parse

    assert parse('```json\n{"action": "send", "body": "terms {a}"}\n``` note {x}') == {
        "action": "send", "body": "terms {a}"}
    assert parse('{"a": 1} and {"b": 2}') == {"a": 1}


def test_model_accepting_an_open_offer_above_our_ask_counters_instead(scenario):
    case, threads, sid = scenario
    threads[sid] += [{"from": "buyer", "body": "Our package.", "term_sheets": [
                         {"terms": {"unit_price": 16, "payment_days": 30}}]},
                     {"from": "supplier", "status": "open", "body": "Counter, subject to approval.", "term_sheets": [
                         {"term_sheet_id": "offer:2", "terms": {"unit_price": 19, "payment_days": 30}}]}]
    agent = RoutedAgent(completion(sid, [], json.dumps({"supplier_id": sid, "action": "accept"})))
    move = agent.decide(case, threads)  # hosted P02 accepted 373.88 in round 2 against our 361.51 ask
    assert agent.last_decision["fallback"] and "action" not in move["message"]
    assert move["message"]["term_sheets"][0]["terms"]["unit_price"] < 19
    threads[sid][-1]["status"] = "final_offer"  # a final offer may be accepted
    assert RoutedAgent(completion(sid, [], json.dumps({"supplier_id": sid, "action": "accept"}))).decide(
        case, threads)["message"]["action"]["type"] == "accept"


def test_an_unreadable_agreement_keeps_the_earlier_quote_defaults(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["attachments"] = [{"name": "quote.pdf", "text":
        "Our standard terms\nPayment terms (days net) 30"}]
    agreed = copy.deepcopy(threads[sid][-1])
    agreed.update(status="agreed", attachments=[{"name": "quote.pdf", "read_failed": True}])
    threads[sid].append(agreed)  # sandbox D01 walked away from exactly this
    assert RoutedAgent(None).closing_move(case, threads)["message"]["action"]["type"] == "accept"


def test_a_stalled_supplier_lets_the_model_accept_an_open_offer(scenario):
    case, threads, sid = scenario
    for price in (19, 19):  # we moved, the supplier did not: hosted P02 walked after more counters
        threads[sid] += [{"from": "buyer", "body": "Our package.", "term_sheets": [
                             {"terms": {"unit_price": 16, "payment_days": 30}}]},
                         {"from": "supplier", "status": "open", "body": "Counter, subject to approval.",
                          "term_sheets": [{"term_sheet_id": f"offer:{len(threads[sid])}",
                                           "terms": {"unit_price": price, "payment_days": 30}}]}]
    agent = RoutedAgent(completion(sid, [], json.dumps({"supplier_id": sid, "action": "accept"})))
    near = agent.decide(case, threads)  # one near-close ask 0.75 % below the open offer, on its terms
    assert agent.last_decision["reason"] == "near_close"
    assert near["message"]["term_sheets"][0]["terms"]["unit_price"] == 18.86
    threads[sid] += [{"from": "buyer", **near["message"]},
                     {"from": "supplier", "status": "open", "body": "We hold.", "term_sheets": [
                         {"term_sheet_id": "offer:held", "terms": {"unit_price": 19, "payment_days": 30}}]}]
    assert agent.decide(case, threads)["message"]["action"]["type"] == "accept"


def test_a_final_offer_is_never_answered_with_a_near_close(scenario):
    case, threads, sid = scenario
    threads[sid] += [{"from": "buyer", "body": "Our package.", "term_sheets": [
                         {"terms": {"unit_price": 16, "payment_days": 30}}]},
                     {"from": "supplier", "status": "final_offer", "body": "Final.", "term_sheets": [
                         {"term_sheet_id": "offer:final", "terms": {"unit_price": 19, "payment_days": 30}}]}]
    assert near_close_sheet(case, threads[sid], sid) is None
    move = RoutedAgent(None)._before_accept(case, threads, "x", closing_move(case, threads))[1]
    assert move["message"]["action"]["type"] == "accept"


def _final_with_alternative(threads, sid, alternative):
    threads[sid] += [{"from": "buyer", "body": "Our package.", "term_sheets": [
                         {"terms": {"unit_price": 16, "payment_days": 30}}]},
                     {"from": "supplier", "status": "final_offer", "body": "This is our final offer.",
                      "term_sheets": [{"term_sheet_id": "offer:2", "terms": {"unit_price": 19, "payment_days": 30}},
                                      {"term_sheet_id": "offer:2-a", "terms": alternative}]}]


def test_a_cheaper_alternative_in_a_final_offer_is_mirrored_unchanged_then_accepted(scenario):
    case, threads, sid = scenario
    _final_with_alternative(threads, sid, {"unit_price": 18.5, "payment_days": 30})
    agent = RoutedAgent(None)
    move = agent.decide(case, threads)  # hosted P18 accepted 5.40 beside its own 5.30 alternative
    assert agent.last_decision["reason"] == "mirror_alternative"
    assert move["message"]["term_sheets"] == [{"terms": {"unit_price": 18.5, "payment_days": 30}}]
    threads[sid] += [{**move["message"], "from": "buyer"},
                     {"from": "supplier", "status": "agreed", "body": "We accept.", "term_sheets": [
                         {"term_sheet_id": "offer:3", "terms": {"unit_price": 18.5, "payment_days": 30}},
                         {"term_sheet_id": "offer:3-a", "terms": {"unit_price": 18.5, "payment_days": 30}}]}]
    assert agent.decide(case, threads)["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:3"}


@pytest.mark.parametrize("alternative", [{"unit_price": 19.2, "payment_days": 30},
                                         {"unit_price": 18.99, "payment_days": 30},
                                         {"unit_price": 18.5, "payment_days": 31}])
def test_a_dearer_marginal_or_invalid_alternative_leaves_the_final_offer_accepted(scenario, alternative):
    case, threads, sid = scenario
    _final_with_alternative(threads, sid, alternative)
    move = RoutedAgent(None).decide(case, threads)
    assert move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:2"}


def test_an_alternative_is_mirrored_once_and_never_on_the_last_move(scenario):
    case, threads, sid = scenario
    _final_with_alternative(threads, sid, {"unit_price": 18.5, "payment_days": 30})
    threads[sid].insert(-1, {"from": "buyer", "body": "Your alternative.", "term_sheets": [
        {"terms": {"unit_price": 18.5, "payment_days": 30}}]})
    assert RoutedAgent(None).decide(case, threads)["message"]["action"]["term_sheet_id"] == "offer:2"
    threads[sid].pop(-2)
    case["round_limit"] = 2
    assert RoutedAgent(None).decide(case, threads)["message"]["action"]["term_sheet_id"] == "offer:2"


def test_a_budget_too_short_for_the_model_counters_by_script(scenario):
    case, threads, sid = scenario
    after_opening(threads, sid)  # a semantic round that would normally go to the model
    calls, clock = [], Clock()
    agent = RoutedAgent(completion(sid, calls), clock=clock)
    move = agent.decide_timed(case, threads, deadline=clock() + 10)  # hosted P05 v11: 10.5 s, then a timeout
    assert calls == [] and agent.last_decision["fallback"] and "action" not in move["message"]


def test_no_counter_sheets_to_a_final_offer_and_no_walk_while_a_deal_stands(scenario):
    case, threads, sid = scenario
    threads[sid][-1]["status"] = "final_offer"
    agent = RoutedAgent(None)
    send = {"supplier_id": sid, "action": "send", "body": "Lower?", "term_sheets": [{"unit_price": 19}]}
    assert agent.validate(case, threads, send) is None  # hosted v5: three suppliers walked (FLOW.md F4)
    assert agent.validate(case, threads, {"supplier_id": sid, "action": "walk_away"}) is None  # F5
    assert agent.validate(case, threads, {"supplier_id": sid, "action": "accept"})


def test_a_supplier_raising_its_price_does_not_raise_our_next_ask(scenario):
    from tacto_negotiator.play import ask_limit

    case, threads, sid = scenario
    ours = {"from": "buyer", "body": "Package.", "term_sheets": [{"terms": {"unit_price": 15, "payment_days": 30}}]}
    raised = {"from": "supplier", "status": "open", "body": "Higher.",
              "term_sheets": [{"term_sheet_id": "offer:2", "terms": {"unit_price": 21, "payment_days": 30}}]}
    held = {**raised, "term_sheets": [{"term_sheet_id": "offer:2", "terms": {"unit_price": 20, "payment_days": 30}}]}
    assert ask_limit(case, threads[sid] + [ours, raised], sid) == ask_limit(case, threads[sid] + [ours, held], sid)


def test_a_failed_model_never_walks_out_of_an_rfq_with_rounds_left():
    """Out-of-sample P06: quotes without a tooling term (already asked for), no alternative, and no time for
    the model. The fallback's closing move found no acceptable offer and walked away: 0 for the case."""
    import json as _json
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    case = next(c for c in _json.loads((root / "tests/fixtures/practice_cases.json").read_text())["cases"]
                if c["case_id"] == "case:P06")

    def failing(system, user, *, deadline):
        raise TimeoutError("no time")

    def thread(sid, price):
        quote = {"from": sid, "status": "open", "body": "Our quote.", "attachments": [],
                 "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0",
                                  "terms": {"unit_price": price, "payment_days": 45, "lead_time_days": 49}}]}
        asked = {"from": "buyer", "term_sheets": [],
                 "body": "Please confirm these terms: tooling_eur. Please provide a complete term sheet."}
        again = {**quote, "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-1",
                                           "terms": {"unit_price": price, "payment_days": 45, "lead_time_days": 49}}]}
        return [quote, asked, again]

    threads = {"sup:70120": thread("sup:70120", 232.68), "sup:70150": thread("sup:70150", 247.44)}
    agent = RoutedAgent(failing)
    move = agent.decide_timed(case, threads, deadline=agent.clock() + 30)
    assert agent.last_decision["fallback"]
    assert (move["message"].get("action") or {}).get("type") != "walk_away"
