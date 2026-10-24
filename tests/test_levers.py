"""Order path, floor question and near-close helpers (agents/levers.py)."""

from types import SimpleNamespace

from tacto_negotiator.agents.levers import (
    FLOOR_QUESTION,
    floor_question_due,
    floor_reached,
    order_path,
    order_path_sentence,
)
from tacto_negotiator.evaluation import ClaimChecker

CASE = {"case_id": "case:T", "parts": ["part:1"], "suppliers": ["sup:1"], "negotiable": ["unit_price"],
        "buyer": {}, "round_limit": 10}


def _orders(prices, supplier="sup:1"):
    """One order per month ending 2026-09, oldest first."""
    out = {}
    for i, price in enumerate(prices):
        month = 9 - (len(prices) - 1 - i)
        year = 2026 + (month - 1) // 12
        month = (month - 1) % 12 + 1
        out[f"po:{supplier}-{i}"] = {"supplier": supplier, "part": "part:1", "unit_price": price,
                                     "date": f"{year}-{month:02d}-15"}
    return out


def test_a_rising_path_starts_at_the_last_low_and_ignores_a_blip():
    facts = _orders([5.30, 5.21, 5.17, 5.17, 5.24, 5.24, 5.10, 5.24, 5.43, 5.43, 5.45, 5.45, 5.45, 5.45])
    path = order_path(SimpleNamespace(facts=facts), CASE, "sup:1")
    assert path["prices"] == [5.17, 5.24, 5.43, 5.45]
    assert path["refs"][-1] == "po:sup:1-13"  # today's level by its newest order
    sentence = order_path_sentence(SimpleNamespace(facts=facts), CASE, {"sup:1": [{"from": "supplier"}]}, "sup:1")
    assert sentence.startswith("Our orders with you for part:1 rose from EUR 5.17 (po:sup:1-2)")
    claim = {"type": "price_history", "refs": path["refs"], "asserted": {"prices": path["prices"]}}
    assert ClaimChecker(CASE, facts).check("sup:1", [claim])[0]["result"] == "verified"


def test_flat_or_small_rises_and_later_rounds_are_not_cited():
    package = SimpleNamespace(facts=_orders([5.00] * 6 + [5.10] * 6))  # +2 %
    assert order_path(package, CASE, "sup:1") is None
    rising = SimpleNamespace(facts=_orders([5.00] * 6 + [5.40] * 6))
    assert order_path_sentence(rising, CASE, {"sup:1": [{"from": "buyer", "body": "x"}]}, "sup:1") == ""
    assert order_path(rising, CASE, "sup:2") is None


def test_a_price_flat_all_year_cites_the_step_of_its_last_rise():
    facts = _orders([28.94] * 4 + [29.89] * 20)  # rose 3.3 % twenty months ago, flat since
    path = order_path(SimpleNamespace(facts=facts), CASE, "sup:1")
    assert path["prices"] == [28.94, 29.89] and path["refs"] == ["po:sup:1-3", "po:sup:1-4"]
    claim = {"type": "price_history", "refs": path["refs"], "asserted": {"prices": path["prices"]}}
    assert ClaimChecker(CASE, facts).check("sup:1", [claim])[0]["result"] == "verified"
    assert order_path(SimpleNamespace(facts=_orders([30.50] * 4 + [29.89] * 20)), CASE, "sup:1") is None  # a fall
    assert order_path(SimpleNamespace(facts=_orders([29.50] * 4 + [29.89] * 20)), CASE, "sup:1") is None  # +1.3 %
    assert order_path(SimpleNamespace(facts=_orders([29.89] * 24)), CASE, "sup:1") is None  # never moved


def test_price_history_claims_check_supplier_part_and_prices():
    facts = _orders([5.0, 5.4])
    refs = list(facts)
    checker = ClaimChecker(CASE, facts)
    assert checker.check("sup:1", [{"type": "price_history", "refs": refs,
                                    "asserted": {"prices": [5.0, 5.5]}}])[0]["result"] == "contradicted"
    assert checker.check("sup:2", [{"type": "price_history", "refs": refs}])[0]["result"] == "irrelevant"
    assert checker.check("sup:1", [{"type": "price_history", "refs": refs}])[0]["result"] == "verified"
    assert checker.check("sup:1", [{"type": "price_history", "refs": refs}])[0]["result"] == "duplicate"


def _thread(*prices, final=False, body="Our terms."):
    events = [{"from": "supplier", "status": "open", "body": body,
               "term_sheets": [{"term_sheet_id": "o0", "terms": {"unit_price": prices[0]}}]}]
    for i, price in enumerate(prices[1:], 1):
        events += [{"from": "buyer", "body": "Our package.", "term_sheets": [{"terms": {"unit_price": 40}}]},
                   {"from": "supplier", "status": "final_offer" if final else "open", "body": body,
                    "term_sheets": [{"term_sheet_id": f"o{i}", "terms": {"unit_price": price}}]}]
    return events


def test_the_floor_question_goes_once_to_a_holding_open_supplier():
    assert floor_question_due(CASE, {"sup:1": _thread(47.0, 45.5, 45.45)}, "sup:1")
    assert not floor_question_due(CASE, {"sup:1": _thread(47.0, 45.5)}, "sup:1")  # still moving
    assert not floor_question_due(CASE, {"sup:1": _thread(47.0, 45.5, 45.5, final=True)}, "sup:1")
    asked = _thread(47.0, 45.5, 45.45)
    asked[-2]["body"] += " " + FLOOR_QUESTION
    assert not floor_question_due(CASE, {"sup:1": asked + _thread(45.45)}, "sup:1")


def test_a_named_floor_at_the_standing_price_stops_probing():
    events = _thread(47.0, 45.0)
    events[-2]["body"] += " " + FLOOR_QUESTION
    events[-1]["body"] = ("Our terms: a price of €45.00 per unit. We cannot drop the price any further than "
                          "45.00 EUR, especially since we are giving you the 0 EUR chargeback you wanted.")
    assert floor_reached(events)
    events[-1]["body"] = "We can drop the price to 44.50 EUR per unit, which is the lowest we can go."
    assert not floor_reached(events)  # a floor below its standing price leaves room
    assert not floor_reached(_thread(47.0, 45.0, body="The lowest we can go is 45.00 EUR."))  # never asked


def test_a_named_floor_with_a_thousands_separator_is_recognised():
    from tacto_negotiator.agents.levers import FLOOR_QUESTION, floor_reached

    for body in ("EUR 1,250.00 is the lowest we can go.", "The lowest price we can offer is EUR 1,250.00."):
        events = [{"from": "buyer", "body": FLOOR_QUESTION},
                  {"from": "supplier", "status": "open", "body": body,
                   "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 1250.0}}]}]
        assert floor_reached(events), body  # round 2 review B5 and the sentence-final price
