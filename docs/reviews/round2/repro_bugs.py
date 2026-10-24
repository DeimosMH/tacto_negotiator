"""Reproductions for round2/BUGS.md (bug review of commit 5dea00a, 2026-10-25).

Local only: in-memory threads on the shipped sim cases, the in-memory `Sim`, a virtual clock.
No arena, no model, no .env. Each probe prints what the current code does; the assertions describe
the CURRENT behaviour and should fail once the finding is fixed. `verify_flow_fixes` checks the
fixes claimed for docs/reviews/FLOW.md (its assertions describe the fixed behaviour).

    .venv/bin/python docs/reviews/round2/repro_bugs.py
"""

import copy
import importlib
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # docs/reviews: the FLOW helpers

from repro_flow import Clock, buyer, price_at, s01, supplier  # noqa: E402

from tacto_negotiator.agents.levers import FLOOR_QUESTION, floor_reached, named_floors  # noqa: E402
from tacto_negotiator.agents.routed import RoutedAgent  # noqa: E402
from tacto_negotiator.cost import total_cost  # noqa: E402
from tacto_negotiator.docs import standard_terms  # noqa: E402
from tacto_negotiator.play import ask_limit, standing  # noqa: E402
from tacto_negotiator.sim import Sim, load_cases  # noqa: E402

logging.disable(logging.WARNING)  # the probe tables serve no attachments


def b1_final_offer_countered_via_limit_sheet():
    """B1: validate() refuses term sheets to a final_offer thread, but a model message WITHOUT term
    sheets (a question, "we will come back to you") gets `_limit_sheet` appended: a counter below the
    final offer goes out. The bank's facts for this supplier are already used (later round)."""
    case, sid = s01()
    cited = buyer(15)
    cited["body"] = "Your latest scorecard records on-time delivery of 84.6% (sc:70270@2026Q3)."
    threads = {sid: [supplier(20, 1), cited, supplier(18, 2, status="final_offer")]}
    agent = RoutedAgent(None)
    with_sheet = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "body": "Lower please.",
                                                "term_sheets": [{"unit_price": 16, "payment_days": 30}]})
    prose = agent.validate(case, threads, {"supplier_id": sid, "action": "send",
                                           "body": "Could you confirm the delivery schedule?", "term_sheets": []})
    sheets = prose["message"]["term_sheets"]
    print(f"B1 final offer EUR 18: sheet from the model -> {with_sheet}; prose-only message -> sends {sheets}")
    assert with_sheet is None and sheets and sheets[0]["terms"]["unit_price"] < 18


def b2_fact_bank_duplicates_across_bidders():
    """B2: the fact bank tracks cited IDs per supplier, but the arena (api.md, What counts) and our own
    Sim count every ID except a rival's quote once per CASE. The second bidder gets the same forecast,
    which comes back `duplicate`, and `_new_fact` treats it as new (no limit sheet is added)."""
    sim = Sim(load_cases(), clock=Clock())
    opened = sim.open("probe", "case:S02")
    session = sim.sessions[opened["session_id"]]
    case, threads = copy.deepcopy(opened["case"]), opened["threads"]
    fact = {"part": case["parts"][0], "change_pct": 12.0, "month": "2026-08"}  # a case fact (api.md, Case file)
    case.setdefault("facts", {})["fc:X@2026-08"] = fact
    session["case"].setdefault("facts", {})["fc:X@2026-08"] = fact
    agent, results = RoutedAgent(None), {}
    for sid in case["suppliers"]:
        _, terms = standing(threads[sid])
        sheet = {f: terms.get(f, case["term_options"].get(f, [None])[0]) for f in case["negotiable"]}
        sheet["unit_price"] = round(sheet["unit_price"] * 0.9, 2)
        move = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "body": "Please consider.",
                                              "term_sheets": [sheet]})
        reply = sim.message("probe", opened["session_id"], sid, move["message"])
        threads[sid] += [{**move["message"], "from": "buyer"}, reply]
        results[sid] = [r["result"] for r in reply["claim_results"]]
    second = case["suppliers"][1]
    print(f"B2 forecast claim results per bidder: {results}; "
          f"_new_fact counts it as new for {second}: {agent._new_fact(case, {**threads, second: threads[second][:1]}, second, 'x (fc:X@2026-08)')}")
    assert results[case["suppliers"][0]][0] == "verified" and results[second][0] == "duplicate"


def b3_rival_fact_cites_a_higher_price():
    """B3 (FLOW F7, moved to facts.py and now auto-inserted): the rival is chosen by total cost but its
    unit price is cited; `FactBank.enforce` appends it even to a scripted message."""
    case = copy.deepcopy(load_cases()["case:S02"])
    a, b = case["suppliers"]
    threads = {
        a: [{"from": "supplier", "status": "open", "body": "Offer.", "term_sheets": [
            {"term_sheet_id": "offer:a", "terms": {"unit_price": 10.0, "payment_days": 14, "lead_time_days": 49}}]}],
        b: [{"from": "supplier", "status": "open", "body": "Offer.", "term_sheets": [
            {"term_sheet_id": "offer:b", "terms": {"unit_price": 10.2, "payment_days": 90, "lead_time_days": 21}}]}],
    }
    case["buyer"]["late_cost_per_day"] = case["buyer"].get("late_cost_per_day") or 500
    move = RoutedAgent(None).validate(case, threads, {
        "supplier_id": a, "action": "send", "body": "Please consider these complete packages.",
        "term_sheets": [{"unit_price": 9.0, "payment_days": 14, "lead_time_days": 49}]})
    body = move["message"]["body"]
    print(f"B3 scripted message to {a} (its own price 10.0): {body!r}")
    assert "EUR 10.2" in body


def b4_late_accept_to_a_thread_with_time_is_dropped():
    """B4: play() drops ANY late action, even an accept to a thread that still has ~30 s, because another
    RFQ thread set the decision deadline. The case only closes after a third decision."""
    runner = importlib.import_module("tacto_negotiator.play")
    clock = Clock()
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

    calls = []

    class Agent:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            a = case["suppliers"][0]
            calls.append({"budget_s": round(deadline - clock(), 1)})
            if len(calls) == 1:  # round 1: counter supplier A (30 s), so the idle B sets the next deadline
                clock.now += 30
                _, terms = standing(threads[a])
                full = {**standard_terms(threads[a]), **terms}
                sheet = {f: full.get(f, case["term_options"].get(f, [None])[0]) for f in case["negotiable"]}
                sheet["unit_price"] = round(sheet["unit_price"] * 0.97, 2)
                return {"supplier_id": a, "message": {"subject": "x", "body": "Please consider.",
                                                      "term_sheets": [{"terms": sheet}]}}
            if len(calls) == 2:
                clock.now = deadline + 0.1  # past B's deadline; A still has about 30 s
            calls[-1]["a_seconds_left"] = round(sim.sessions[table.sid]["threads"][a]["last_at"] + 60 - clock(), 1)
            return {"supplier_id": a, "message": {"action": {"type": "accept",
                                                             "term_sheet_id": standing(threads[a])[0]}}}

    saved = runner.time
    runner.time = types.SimpleNamespace(time=clock, monotonic=clock, sleep=lambda s: setattr(clock, "now", clock.now + s))
    try:
        table = Table()
        result = runner.play(table, "case:S02", Agent())
    finally:
        runner.time = saved
    print(f"B4 decisions {calls}; deal={result.get('deal')}: the 2nd decision (accept A) was not sent")
    assert len(calls) == 3 and calls[1]["a_seconds_left"] > 20


def b5_named_floor_with_thousands_separator():
    """B5: `named_floors` splits "1,250.00" into 1 and 250.0, so a floor named at the standing price of a
    four-digit part is not recognised (`floor_reached` False)."""
    events = [{"from": "buyer", "body": FLOOR_QUESTION, "term_sheets": []},
              {"from": "supplier", "status": "open", "body": "EUR 1,250.00 is the lowest we can go.",
               "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 1250.0}}]}]
    print(f"B5 named floors {named_floors(events)}; floor_reached={floor_reached(events)}")
    assert not floor_reached(events)


def verify_flow_fixes():
    """FLOW fixes: assertions describe the FIXED behaviour (F5 shows the remaining gap)."""
    case, sid = s01()
    agent = RoutedAgent(None)
    # F1a: a retraction is treated like a hold (best offer so far), no longer above it
    hold = price_at(case, ask_limit(case, [supplier(20, 1), buyer(15), supplier(20, 2)], sid))
    up = price_at(case, ask_limit(case, [supplier(20, 1), buyer(15), supplier(21, 3)], sid))
    # F2: _off_repeat result clamped to the hold limit
    events = [supplier(20, 1), buyer(10), supplier(19, 2), buyer(11), supplier(14, 3)]
    move = agent.validate(case, {sid: events}, {"supplier_id": sid, "action": "send", "body": "Thank you.",
                                                "term_sheets": [{"unit_price": 11, "payment_days": 30}]})
    f2 = total_cost(case, move["message"]["term_sheets"][0]["terms"]) <= ask_limit(case, events, sid) + 0.01
    # F5: blocked with an acceptable offer standing; still allowed with all rounds left when an alternative exists
    f5_blocked = agent.validate(case, {sid: [supplier(20, 1)]}, {"supplier_id": sid, "action": "walk_away"}) is None
    alt_case = copy.deepcopy(case)
    alt_case["buyer"]["alternative"] = {"kind": "quote", "ref": None, "terms": {"unit_price": 19.0, "payment_days": 30}}
    f5_alt = agent.validate(alt_case, {sid: [supplier(20, 1)]}, {"supplier_id": sid, "action": "walk_away"})
    print(f"FLOW F1a ask after hold {hold} / after retraction {up}; F2 clamped {f2}; "
          f"F5 blocked with acceptable offer {f5_blocked}, walk with 10 rounds left and a dearer standing offer "
          f"than the alternative -> {f5_alt and f5_alt['message']}")
    assert up == hold and f2 and f5_blocked and f5_alt


if __name__ == "__main__":
    for probe in (b1_final_offer_countered_via_limit_sheet, b2_fact_bank_duplicates_across_bidders,
                  b3_rival_fact_cites_a_higher_price, b4_late_accept_to_a_thread_with_time_is_dropped,
                  b5_named_floor_with_thousands_separator, verify_flow_fixes):
        probe()
