"""Reproductions for FLOW.md (step-by-step logic review, 2026-10-24).

Local only: in-memory threads on the shipped sim cases, no arena, no model, no .env.
Each probe prints what the current code does; the assertions describe the CURRENT behaviour
and should fail once the corresponding finding is fixed.

    .venv/bin/python docs/reviews/repro_flow.py
"""

import copy

from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.cost import total_cost
from tacto_negotiator.play import ask_limit
from tacto_negotiator.sim import load_cases


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def s01():
    case = copy.deepcopy(load_cases()["case:S01"])
    return case, case["suppliers"][0]


def supplier(price, n, status="open"):
    return {"from": "supplier", "status": status, "body": "Our proposal.",
            "term_sheets": [{"term_sheet_id": f"offer:{n}", "terms": {"unit_price": price, "payment_days": 30}}]}


def buyer(price):
    return {"from": "buyer", "body": "Please consider.", "term_sheets": [{"terms": {"unit_price": price, "payment_days": 30}}]}


def price_at(case, limit):
    """Unit price whose cash cost equals `limit` on the probe's terms (C is affine in price here)."""
    c0, c1 = total_cost(case, {"unit_price": 0.0001, "payment_days": 30}), total_cost(case, {"unit_price": 1, "payment_days": 30})
    return round(0.0001 + (limit - c0) / (c1 - c0) * (1 - 0.0001), 2)


def f1_retraction_raises_our_ask():
    """F1: a supplier that moves its price UP pulls our next ask up with it."""
    case, sid = s01()
    held = [supplier(20, 1), buyer(15), supplier(19, 2)]
    retracted = [supplier(20, 1), buyer(15), supplier(21, 3)]
    a, b = price_at(case, ask_limit(case, held, sid)), price_at(case, ask_limit(case, retracted, sid))
    print(f"F1 supplier conceded to 19 -> our next ask {a}; supplier raised to 21 -> our next ask {b}")
    assert b > a


def f2_off_repeat_breaks_the_hold():
    """F2: after a large supplier concession ask_limit holds (10 %), but _off_repeat then moves 20 %."""
    case, sid = s01()
    events = [supplier(20, 1), buyer(10), supplier(19, 2), buyer(11), supplier(14, 3)]
    limit = ask_limit(case, events, sid)
    agent = RoutedAgent(None)
    move = agent.validate(case, {sid: events}, {"supplier_id": sid, "action": "send", "body": "Thank you.",
                                               "term_sheets": [{"unit_price": 11, "payment_days": 30}]})
    sent = move["message"]["term_sheets"][0]["terms"]
    print(f"F2 hold limit = EUR {price_at(case, limit)}; validated sheet = EUR {sent['unit_price']} "
          f"(cash {total_cost(case, sent):,.2f} > limit {limit:,.2f})")
    assert total_cost(case, sent) > limit + 0.01


def f3_no_time_ends_the_case():
    """F3 (fixed): a decision that misses its thread's 60 s window no longer closes the CASE. The
    late move (here the walk-away a late model fallback produces) is not sent; the thread times out
    once (api.md, Time) and the next decision gets a fresh window. Played through `play()` on the
    in-memory simulator with a virtual clock."""
    import types

    import importlib

    runner = importlib.import_module("tacto_negotiator.play")
    from tacto_negotiator.sim import Sim

    clock = Clock()

    def advance(seconds):
        clock.now += seconds

    sim = Sim(load_cases(), clock=clock)

    class Table:
        def open(self, case_id):
            opened = sim.open("probe", case_id)
            self.sid = opened["session_id"]
            return opened["case"], opened["threads"]

        def send(self, supplier_id, message):
            return sim.message("probe", self.sid, supplier_id, message)

        def close(self, supplier_id, message):
            return self.send(supplier_id, message).get("result")

        def result(self):
            return sim.sessions[self.sid]["result"]

    budgets = []

    class Agent:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            budgets.append(round(deadline - clock(), 1))
            sid = next(iter(threads))
            if len(budgets) == 1:
                clock.now = deadline + 0.1  # the decision ran past the thread's deadline
            return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}

    saved = runner.time
    runner.time = types.SimpleNamespace(time=clock, monotonic=clock, sleep=advance)
    try:
        table = Table()
        runner.play(table, "case:S01", Agent())
    finally:
        runner.time = saved
    thread = next(iter(sim.sessions[table.sid]["threads"].values()))
    print(f"F3 late first decision -> timeouts {len(thread['late'])}, decision budgets {budgets}: "
          f"the walk-away is only sent from a fresh window")
    assert len(thread["late"]) == 1 and len(budgets) == 2 and budgets[1] > 50


def f4_final_offer_can_be_countered():
    """F4: the shared validator lets a model counter a supplier's final_offer."""
    case, sid = s01()
    threads = {sid: [supplier(20, 1), buyer(15), supplier(18, 2, status="final_offer")]}
    move = RoutedAgent(None).validate(case, threads, {"supplier_id": sid, "action": "send", "body": "Lower please.",
                                                      "term_sheets": [{"unit_price": 16, "payment_days": 30}]})
    print(f"F4 counter to a final_offer passes validation: {bool(move)}")
    assert move and move["message"].get("term_sheets")


def f5_walk_away_is_ungated():
    """F5: an acceptable offer with all 10 moves left; the model's walk_away still passes validation."""
    case, sid = s01()
    threads = {sid: [supplier(20, 1)]}
    move = RoutedAgent(None).validate(case, threads, {"supplier_id": sid, "action": "walk_away"})
    print(f"F5 walk_away validated while an acceptable offer stands: {move['message']}")
    assert move["message"]["action"]["type"] == "walk_away"


def f6_latch_without_model_call():
    """F6: a model route that never called the model still latches the case to the model route."""
    case, sid = s01()
    threads = {sid: [supplier(20, 1), buyer(15), supplier(19, 2)]}
    case["negotiable"] = ["unit_price", "payment_days"]
    clock, calls = Clock(), []
    agent = RoutedAgent(lambda *a, **k: calls.append(1), clock=clock)
    before = agent._semantic_blocker(case, threads)
    threads[sid][-1]["body"] = "Our price holds unless volume rises."  # one conditional word
    agent.decide_timed(case, threads, deadline=clock.now + 10)  # < MIN_MODEL_SECONDS: no model call
    threads[sid][-1]["body"] = "Our proposal."  # the wording is gone
    after = agent._semantic_blocker(case, threads)
    print(f"F6 scripts blocked before={before}, model calls={len(calls)}, blocked after={after}")
    assert not before and not calls and after


def f8_must_have_checked_without_supplier_defaults():
    """F8: validate() checks must-haves against the buyer's plan, not the supplier's quote defaults,
    so it sends a package the closing check (acceptable_offer) can never accept."""
    case, sid = s01()
    case["fixed_terms"].pop("lead_time_days", None)
    case["buyer"]["must_haves"] = [{"field": "lead_time_days", "op": "<=", "value": 42}]
    first = supplier(20, 1)
    first["attachments"] = [{"name": "quote.pdf", "text": "Our standard terms\nLead time (days) 56"}]
    threads = {sid: [first]}
    agent = RoutedAgent(None)
    move = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "body": "Please consider.",
                                          "term_sheets": [{"unit_price": 15, "payment_days": 30}]})
    accept = agent.validate(case, threads, {"supplier_id": sid, "action": "accept"})
    print(f"F8 sheet that keeps the quote's 56-day lead time validated: {bool(move)}; "
          f"its acceptance would be allowed: {bool(accept)}")
    assert move and not accept


def f7_rival_claim_can_cite_a_higher_price():
    """F7: the rival claim picks the rival by total cost but quotes its unit price."""
    case = copy.deepcopy(load_cases()["case:S02"])
    a, b = case["suppliers"]
    threads = {
        a: [{"from": "supplier", "status": "open", "body": "Offer.", "term_sheets": [
            {"term_sheet_id": "offer:a", "terms": {"unit_price": 10.0, "payment_days": 14, "lead_time_days": 49}}]}],
        b: [{"from": "supplier", "status": "open", "body": "Offer.", "term_sheets": [
            {"term_sheet_id": "offer:b", "terms": {"unit_price": 10.2, "payment_days": 90, "lead_time_days": 21}}]}],
    }
    case["buyer"]["late_cost_per_day"] = case["buyer"].get("late_cost_per_day") or 500
    text = RoutedAgent(None)._rival_evidence(case, threads, a)
    print(f"F7 message to {a} (its own price 10.0): {text!r}")
    assert "10.2" in text


if __name__ == "__main__":
    for probe in (f1_retraction_raises_our_ask, f2_off_repeat_breaks_the_hold, f3_no_time_ends_the_case,
                  f4_final_offer_can_be_countered, f5_walk_away_is_ungated, f6_latch_without_model_call,
                  f7_rival_claim_can_cite_a_higher_price, f8_must_have_checked_without_supplier_defaults):
        probe()
