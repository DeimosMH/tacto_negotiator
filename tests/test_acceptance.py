"""Shared acceptance safety and quote round trips, using local supplier documents only."""

import copy

import pytest

from tacto_negotiator.agents.llm import LLMAgent
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.cost import complete_offer
from tacto_negotiator.docs import quote_terms, read_document, standard_terms
from tacto_negotiator.play import closing_move, fetch_documents
from tacto_negotiator.sim import Sim, load_cases


@pytest.mark.parametrize("evidence", ["missing", "failed", "malformed", "constraint", "alternative", "valid"])
def test_guarded_entry_points_agree_on_acceptance(evidence):
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    sid = case["suppliers"][0]
    attachment = {"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 60"}
    events = [{"from": "supplier", "status": "open", "attachments": [attachment],
               "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 18}}]}]
    if evidence == "missing":
        events[0]["attachments"] = []
    elif evidence == "failed":
        attachment["read_failed"] = True
    elif evidence == "malformed":
        attachment["text"] += "\nIndex clause subject to revision"
    elif evidence == "constraint":
        attachment["text"] = "Our standard terms\nPayment terms (days net) 14"
    elif evidence == "alternative":
        case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 1, "payment_days": 60}}
    threads = {sid: events}
    decision = {"supplier_id": sid, "action": "accept"}
    routed = RoutedAgent(None)
    assert bool(routed.validate(case, threads, decision)) == (evidence == "valid")
    assert bool(LLMAgent(None).guard(case, threads, decision)) == (evidence == "valid")
    for closer in (closing_move, routed.closing_move):
        action = closer(case, threads)["message"]["action"]
        assert action["type"] == ("accept" if evidence == "valid" else "walk_away")


def test_legal_supplier_standard_survives_both_offer_builders():
    case = copy.deepcopy(load_cases()["case:S01"])
    case["term_options"]["payment_days"] = [14, 30]
    sid = case["suppliers"][0]
    threads = {sid: [{"from": "supplier", "status": "open", "term_sheets": [
        {"term_sheet_id": "offer:1", "terms": {"unit_price": 20}}], "attachments": [
        {"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 90"}]}]}
    proposed = {"unit_price": 18, "payment_days": 90}
    decision = {"supplier_id": sid, "action": "send", "term_sheets": [proposed]}
    for guard in (RoutedAgent(None).validate, LLMAgent(None).guard):
        move = guard(case, threads, decision)
        assert move["message"]["term_sheets"] == [{"terms": proposed}]


@pytest.mark.parametrize("text", ["unknown", "{'series': 'S1'}", "S1, 35% material, deadband 3 %, base 2026-09 extra"])
def test_unrecognized_clause_is_not_a_null_clause(text):
    with pytest.raises(ValueError, match="index clause"):
        quote_terms("Our standard terms\nIndex clause " + text)


def test_indexed_supplier_document_round_trip_and_safe_closure():
    case = copy.deepcopy(load_cases()["case:S01"])
    clause = {"series": "S1", "material_share": 0.355, "deadband_pct": 2.5, "base_month": "2026-09"}
    case["negotiable"].append("index_clause")
    del case["fixed_terms"]["index_clause"]
    case["term_options"]["index_clause"] = [clause, None]
    case["buyer"]["must_haves"] = [{"field": "index_clause", "op": "==", "value": None}]
    sim = Sim({case["case_id"]: case}, no_clock=True)
    opened = sim.open("local", case["case_id"])
    sid = case["suppliers"][0]

    class Documents:
        def attachment(self, attachment_id):
            return sim.attachment("local", opened["session_id"], attachment_id)[0]

    events = opened["threads"][sid]
    for message in events:
        fetch_documents(Documents(), message)
    assert standard_terms(events)["index_clause"] == clause
    assert complete_offer(case, {"unit_price": 18, "index_clause": None})["index_clause"] is None
    move = RoutedAgent(None).closing_move(case, opened["threads"])
    assert move["message"]["action"]["type"] == "walk_away"
    result = sim.message("local", opened["session_id"], sid, move["message"])["result"]
    assert not result["deal"]
    # Explicit none remains distinct from a failed parse.
    from tacto_negotiator.sim import pdf

    text = read_document("quote.pdf", pdf("Quote", [("Our standard terms", {"index_clause": None})]))
    assert quote_terms(text)["standard"]["index_clause"] is None
