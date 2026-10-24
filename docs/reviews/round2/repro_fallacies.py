"""Reproductions for round2/FALLACIES.md (logic and assumption review at 5dea00a, 2026-10-25).

Local only: in-memory threads on the shipped sim cases, no arena, no model, no .env.
Each probe prints what the current code does; the assertions describe the CURRENT behaviour
and should fail once the corresponding finding is fixed.

    .venv/bin/python docs/reviews/round2/repro_fallacies.py
"""

import copy

from tacto_negotiator.agents.facts import FactBank
from tacto_negotiator.agents.levers import FLOOR_QUESTION
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.cost import total_cost
from tacto_negotiator.hosted_like import PROFILE_RANGES, counter
from tacto_negotiator.play import ask_limit
from tacto_negotiator.sim import load_cases


def s01():
    case = copy.deepcopy(load_cases()["case:S01"])
    return case, case["suppliers"][0]


def supplier(price, n, status="open", body="Our proposal.", extra=()):
    sheets = [{"term_sheet_id": f"offer:{n}", "terms": {"unit_price": price, "payment_days": 30}}]
    sheets += [{"term_sheet_id": f"offer:{n}{chr(98 + i)}", "terms": t} for i, t in enumerate(extra)]
    return {"from": "supplier", "status": status, "body": body, "term_sheets": sheets}


def buyer(price, body="Please consider."):
    return {"from": "buyer", "body": body, "term_sheets": [{"terms": {"unit_price": price, "payment_days": 30}}]}


def price_at(case, limit):
    """Unit price whose cash cost equals `limit` on the probe's terms (C is affine in price here)."""
    c0, c1 = total_cost(case, {"unit_price": 0.0001, "payment_days": 30}), total_cost(case, {"unit_price": 1, "payment_days": 30})
    return round(0.0001 + (limit - c0) / (c1 - c0) * (1 - 0.0001), 2)


def a1_reciprocity_still_inverted():
    """A1: after the F1a fix a retraction no longer raises our ask above a hold, but the more the
    supplier concedes, the LESS we move (our step is a share of the remaining gap)."""
    case, sid = s01()
    moves = {}
    for label, reply in (("retracts to 21", 21), ("holds at 20", 20), ("concedes to 19", 19), ("concedes to 17", 17)):
        nxt = price_at(case, ask_limit(case, [supplier(20, 1), buyer(15), supplier(reply, 2)], sid))
        moves[label] = round(nxt - 15, 2)
    print("A1 our next move from 15 after the supplier", moves)
    assert moves["holds at 20"] == moves["retracts to 21"] > moves["concedes to 19"] > moves["concedes to 17"]


def a2_near_close_probes_below_named_floor():
    """A2: a supplier names a floor equal to its standing price; the 'named_floor' accept is turned into
    a near-close ask 0.75 % BELOW that floor - the probing the docstring and prompt forbid."""
    case, sid = s01()
    threads = {sid: [supplier(20, 1), buyer(15, f"Please consider. {FLOOR_QUESTION}"),
                     supplier(18, 2, body="The lowest price we can offer for these terms is EUR 18.00 per unit.")]}
    reason, move = RoutedAgent(None)._script(case, threads)
    sent = move["message"].get("term_sheets", [{}])[0].get("terms", {})
    print(f"A2 supplier named floor 18.00 at its standing price -> route {reason!r}, sheet price {sent.get('unit_price')}")
    assert reason == "near_close" and sent["unit_price"] < 18


def a2b_floor_at_sentence_end_is_not_read():
    """A2b: `levers._PRICE` refuses a number followed by '.', so the commonest phrasing of a floor
    ("... is 18.00.") is never read as a named floor; the floor rule then depends on wording."""
    from tacto_negotiator.agents.levers import named_floors
    events = [buyer(15, FLOOR_QUESTION)]
    a = named_floors(events + [{"from": "supplier", "body": "The lowest we can go is 18.00."}])
    b = named_floors(events + [{"from": "supplier", "body": "The lowest we can go is 18.00 per unit."}])
    print(f"A2b floors read: sentence-final {a}, mid-sentence {b}")
    assert a == [] and b == [18.0]


def a3_mirror_bypasses_final_offer_guard():
    """A3: validate() refuses a term sheet to a final_offer thread (F4) - unless reprice=False, which is
    how _mirror_alternative sends one. In a single-supplier case the whole deal rides on that send."""
    case, sid = s01()
    alt = {"unit_price": 17.6, "payment_days": 30}
    threads = {sid: [supplier(20, 1), buyer(15), supplier(18, 2, status="final_offer", extra=[alt])]}
    agent = RoutedAgent(None)
    guarded = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "body": "x", "term_sheets": [alt]})
    reason, move = agent._script(case, threads)
    print(f"A3 same sheet to the sole supplier's final offer: guarded path -> {bool(guarded)}; "
          f"script -> {reason!r} with {len(move['message'].get('term_sheets', []))} sheet(s)")
    assert guarded is None and reason == "mirror_alternative" and move["message"]["term_sheets"]


def a4_walk_away_with_rounds_left():
    """A4: with an alternative declared, a model walk-away passes validation in round 1 with 10 moves
    left whenever the opening quote is above the alternative - before any concession was asked for."""
    case = copy.deepcopy(load_cases()["case:S03"])
    sid = case["suppliers"][0]
    terms = {"unit_price": 300.0, "payment_days": 30, "contract_months": 12, "incoterm": "DAP"}
    threads = {sid: [{"from": "supplier", "status": "open", "body": "Quote.",
                      "term_sheets": [{"term_sheet_id": "offer:1", "terms": terms}]}]}
    move = RoutedAgent(None).validate(case, threads, {"supplier_id": sid, "action": "walk_away"})
    print(f"A4 walk_away in round 1 (opening 300 vs alternative 245.59, 10 moves left): {move and move['message']}")
    assert move and move["message"]["action"]["type"] == "walk_away"


def a5_shrug_demotes_every_kind_in_the_message():
    """A5: one 'does not change our calculation' in reply to a round-1 e-mail with three claims marks
    all three claim kinds as shrugged; later facts of those kinds become fillers."""
    threads = {"sup:1": [
        {"from": "supplier", "body": "Quote."},
        {"from": "buyer", "body": "PPI fell (idx:PPI-X@2025-08, idx:PPI-X@2026-08). Should-cost is EUR 4 (cost:123). "
                                  "Forecast up 8% (fc:123@2026)."},
        {"from": "supplier", "body": "Thank you. The index you cite does not change our calculation."}]}
    kinds = FactBank.shrugged(threads, "sup:1")
    print(f"A5 kinds marked shrugged after one shrug sentence about the index: {sorted(kinds)}")
    assert kinds == {"idx", "cost", "fc"}


def a6_rival_claim_by_cost_cites_higher_price():
    """A6 (= FLOW F7, moved to facts.py): the rival is chosen by total cost but its unit price is cited."""
    case = copy.deepcopy(load_cases()["case:S02"])
    a, b = case["suppliers"]
    case["buyer"]["late_cost_per_day"] = case["buyer"].get("late_cost_per_day") or 500
    threads = {
        a: [{"from": "supplier", "status": "open", "body": "Offer.", "term_sheets": [
            {"term_sheet_id": "offer:a", "terms": {"unit_price": 10.0, "payment_days": 14, "lead_time_days": 49}}]}],
        b: [{"from": "supplier", "status": "open", "body": "Offer.", "term_sheets": [
            {"term_sheet_id": "offer:b", "terms": {"unit_price": 10.2, "payment_days": 90, "lead_time_days": 21}}]}],
    }
    facts = FactBank(None)._rival_offers(case, threads, a)
    print(f"A6 strong fact for {a} (own price 10.0): {[f.sentence for f in facts]}")
    assert facts and "10.2" in facts[0].sentence and facts[0].strong


def a7_replica_rewards_tiny_moves():
    """A7: in the replica a supplier's concession is a share of the REMAINING gap, gated only by any
    movement from us. A 0.01 move draws a larger concession than a 40 %-of-gap move - the opposite of
    the P07 model ('answers each of our moves with about 0.4 of it') that sets GAP_STEP_BY_GOAL."""
    lo = {k: (a + b) / 2 for k, (a, b) in zip(("a1", "cap1", "a2", "depth"), PROFILE_RANGES["routine"][:4], strict=True)}
    profile = {**lo, "final_round": 9, "final_gap": 99}
    p0, previous, last = 10.0, 9.9, 7.0
    out = {}
    for label, ask in (("tiny move to 7.01", 7.01), ("0.40 x gap", last + 0.4 * (previous - last))):
        price, status = counter(profile, p0, 2, previous, ask, 0.0, last)
        out[label] = round(previous - price, 3)
    print(f"A7 replica routine supplier concession in round 2 (from {previous}): {out}")
    assert out["tiny move to 7.01"] > out["0.40 x gap"]


if __name__ == "__main__":
    for probe in (a1_reciprocity_still_inverted, a2_near_close_probes_below_named_floor, a2b_floor_at_sentence_end_is_not_read,
                  a3_mirror_bypasses_final_offer_guard, a4_walk_away_with_rounds_left,
                  a5_shrug_demotes_every_kind_in_the_message, a6_rival_claim_by_cost_cites_higher_price,
                  a7_replica_rewards_tiny_moves):
        probe()
