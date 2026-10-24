"""Quantitative script coverage and near misses, without model calls or hidden supplier state."""

import copy
import json
from unittest.mock import patch

import pytest

from tacto_negotiator.agents.routed import MAX_SCRIPT_CANDIDATES, RoutedAgent
from tacto_negotiator.cost import must_haves_ok, total_cost
from tacto_negotiator.docs import quote_terms


@pytest.fixture
def negotiation():
    case = {
        "case_id": "case:structured", "title": "Annual supply", "type": "renewal", "parts": ["part:A"],
        "suppliers": ["sup:A"], "negotiable": ["unit_price", "payment_days", "incoterm"],
        "fixed_terms": {"volume_per_year": 1000, "contract_months": 12, "lead_time_days": 14,
                        "tooling_eur": 0, "index_clause": None},
        "term_options": {"payment_days": [14, 30, 60], "incoterm": ["EXW", "FCA", "DAP"]},
        "buyer": {"need_per_year": 1000, "horizon_years": 1, "current_unit_price": 100,
                  "fallback_unit_price": 150, "interest_rate": 0.1, "reference_payment_days": 60,
                  "need_lead_time_days": 30, "freight_eur_per_unit": {"EXW": 10, "FCA": 5, "DAP": 0},
                  "late_cost_per_day": 1000, "cost_per_defect": 10, "category_goal": "routine",
                  "must_haves": [], "alternative": {"kind": "none"}},
        "round_limit": 10,
    }
    threads = {"sup:A": [quote({"unit_price": 110, "payment_days": 14, "incoterm": "EXW"})]}
    return case, threads


def quote(terms, number=0, status="open", **extras):
    return {"from": "supplier", "body": "Our offer.", "status": status,
            "term_sheets": [{"term_sheet_id": f"offer:{number}", "terms": terms}], **extras}


def agent_with_calls():
    calls = []

    def complete(system, user, *, deadline):
        calls.append(user)
        return json.dumps({"supplier_id": "sup:A", "action": "send", "body": "Please clarify the conditions."})

    return RoutedAgent(complete, complete), calls


def record_round(threads, move, supplier_terms, number=1, **extras):
    sid = move["supplier_id"]
    threads[sid].append({**copy.deepcopy(move["message"]), "from": "buyer"})
    threads[sid].append(quote(copy.deepcopy(supplier_terms), number, **extras))


def test_full_term_opening_offers_cheaper_packages_and_a_familiar_tradeoff(negotiation):
    case, threads = negotiation
    agent, calls = agent_with_calls()
    move = agent.decide(case, threads)
    packages = [sheet["terms"] for sheet in move["message"]["term_sheets"]]
    assert calls == [] and agent.last_decision["reason"] == "enumerated_opening"
    assert packages[0] == {"unit_price": 109.26, "payment_days": 60, "incoterm": "DAP"}
    baseline = next(t for t in packages if t["payment_days"] == 14 and t["incoterm"] == "EXW")
    assert baseline["unit_price"] < packages[0]["unit_price"]
    assert len(packages) <= 3 and all(set(t) == set(case["negotiable"]) for t in packages)
    assert total_cost(case, packages[0]) < total_cost(case, threads["sup:A"][0]["term_sheets"][0]["terms"])
    assert not move["message"].get("action")
    ceiling = total_cost(case, threads["sup:A"][0]["term_sheets"][0]["terms"]) * 0.9
    assert all(ceiling - 11 <= total_cost(case, t) <= ceiling for t in packages)


def test_explicit_outgoing_package_repairs_a_negotiable_must_have(negotiation):
    case, threads = negotiation
    case["negotiable"].append("lead_time_days")
    case["fixed_terms"].pop("lead_time_days")
    case["term_options"]["lead_time_days"] = [14, 28, 42]
    case["buyer"]["must_haves"] = [{"field": "lead_time_days", "op": "<=", "value": 14}]
    threads["sup:A"][0]["term_sheets"][0]["terms"]["lead_time_days"] = 42
    agent, calls = agent_with_calls()
    move = agent.decide(case, threads)
    assert calls == [] and agent.last_decision["reason"] == "must_have_repair"
    assert all(must_haves_ok(case, sheet["terms"]) and sheet["terms"]["lead_time_days"] == 14
               for sheet in move["message"]["term_sheets"])
    assert agent.validate(case, threads, {"supplier_id": "sup:A", "action": "accept"}) is None


def test_rfq_uses_full_cost_and_services_untried_suppliers(negotiation):
    case, threads = negotiation
    case["suppliers"].append("sup:B")
    case["term_options"]["incoterm"] = ["EXW"]
    threads["sup:A"][0]["term_sheets"][0]["terms"]["unit_price"] = 100
    better = {"unit_price": 105, "payment_days": 14, "incoterm": "DAP"}
    threads["sup:B"] = [quote(better, attachments=[{"name": "quote.pdf", "text":
        "Our standard terms\nIncoterm DAP"}])]
    agent, calls = agent_with_calls()
    first = agent.decide(case, threads)
    assert calls == [] and first["supplier_id"] == "sup:B"  # higher unit price, cheaper delivered package
    assert agent.last_decision["reason"] == "rfq_package_comparison"
    record_round(threads, first, better)
    second = agent.decide(case, threads)
    assert calls == [] and second["supplier_id"] == "sup:A"


def test_followup_concedes_in_full_cost_and_closes_agreement(negotiation):
    case, threads = negotiation
    agent, calls = agent_with_calls()
    opening = agent.decide(case, threads)
    reply = {"unit_price": 108, "payment_days": 14, "incoterm": "EXW"}
    record_round(threads, opening, reply)
    followup = agent.decide(case, threads)
    assert calls == [] and agent.last_decision["reason"] == "bounded_concession"
    proposed = followup["message"]["term_sheets"][0]["terms"]
    previous_cost = min(total_cost(case, sheet["terms"]) for sheet in opening["message"]["term_sheets"])
    current_cost = total_cost(case, reply)
    assert previous_cost <= total_cost(case, proposed) <= (previous_cost + current_cost) / 2 < current_cost
    assert proposed["unit_price"] > reply["unit_price"]  # a premium pays for DAP/longer payment, not worse total C
    record_round(threads, followup, proposed, number=2, status="agreed")
    assert agent.decide(case, threads)["message"]["action"]["type"] == "accept"
    assert calls == []


def test_outside_option_caps_proposals_and_plateau_closes_without_a_bad_deal(negotiation):
    case, threads = negotiation
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {
        "unit_price": 50, "payment_days": 60, "incoterm": "DAP"}}
    alternative = total_cost(case, case["buyer"]["alternative"]["terms"])
    initial = copy.deepcopy(threads["sup:A"][0]["term_sheets"][0]["terms"])
    agent, calls = agent_with_calls()
    first = agent.decide(case, threads)
    assert all(total_cost(case, sheet["terms"]) < alternative for sheet in first["message"]["term_sheets"])
    record_round(threads, first, initial)
    second = agent.decide(case, threads)
    record_round(threads, second, initial, number=2)
    close = agent.decide(case, threads)
    assert calls == [] and agent.last_decision["reason"] == "quote_plateau"
    assert close["message"]["action"]["type"] == "walk_away"


def test_plateau_accepts_only_after_two_structured_attempts(negotiation):
    case, threads = negotiation
    initial = copy.deepcopy(threads["sup:A"][0]["term_sheets"][0]["terms"])
    agent, calls = agent_with_calls()
    for number in (1, 2):
        move = agent.decide(case, threads)
        assert not move["message"].get("action")
        record_round(threads, move, initial, number)
    assert agent.decide(case, threads)["message"]["action"]["type"] == "accept"
    assert calls == [] and agent.last_decision["reason"] == "quote_plateau"


@pytest.mark.parametrize("wording", ["Subject to approval.", "Available unless demand changes.", "Pending approval."])
def test_conditional_wording_stays_with_the_model(negotiation, wording):
    case, threads = negotiation
    threads["sup:A"][0]["body"] = wording
    agent, calls = agent_with_calls()
    agent.decide(case, threads)
    assert len(calls) == 1 and agent.last_decision["route"] == "fast"


@pytest.mark.parametrize("blocker", ["contradiction", "relationship", "prose_history", "too_many", "no_options"])
def test_structured_near_misses_do_not_inflate_script_coverage(negotiation, blocker):
    case, threads = negotiation
    if blocker == "contradiction":
        threads["sup:A"][0]["claim_results"] = [{"result": "contradicted"}]
    elif blocker == "relationship":
        case["buyer"]["category_goal"] = "secure_supply"
    elif blocker == "prose_history":
        threads["sup:A"].insert(0, {"from": "buyer", "body": "Can you explain the exception?"})
    elif blocker == "too_many":
        case["term_options"]["payment_days"] = list(range(121))
        assert 121 * len(case["term_options"]["incoterm"]) > MAX_SCRIPT_CANDIDATES
    else:
        case["term_options"].pop("incoterm")
    agent, calls = agent_with_calls()
    agent.decide(case, threads)
    assert len(calls) == 1 and agent.last_decision["route"] in {"fast", "strong"}
    assert not agent.last_decision["fallback"]


def test_missing_quote_defaults_are_clarified_before_enumerating(negotiation):
    case, threads = negotiation
    threads["sup:A"][0]["term_sheets"][0]["terms"].pop("incoterm")
    agent, calls = agent_with_calls()
    move = agent.decide(case, threads)
    assert calls == [] and agent.last_decision["reason"] == "missing_quote_terms"
    assert "incoterm" in move["message"]["body"] and not move["message"]["term_sheets"]


def test_supplier_standard_packages_keep_the_cost_ceiling(negotiation):
    case, threads = negotiation
    case["term_options"]["incoterm"] = ["EXW"]
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {
        "unit_price": 30, "incoterm": "DAP", "payment_days": 60}}
    threads["sup:A"] = [quote({"unit_price": 100, "payment_days": 14, "incoterm": "DAP"},
        attachments=[{"name": "quote.pdf", "text": "Our standard terms\nIncoterm DAP"}])]
    agent, calls = agent_with_calls()
    move = agent.decide(case, threads)
    packages = [sheet["terms"] for sheet in move["message"]["term_sheets"]]
    assert not calls and not agent.last_decision["fallback"]
    assert any(t["incoterm"] == "DAP" for t in packages)
    assert all(total_cost(case, t) < 30000 for t in packages)


def test_bounded_search_parses_the_quote_once_per_decision_stage(negotiation):
    case, threads = negotiation
    case["negotiable"].remove("incoterm")
    case["fixed_terms"]["incoterm"] = "DAP"
    case["negotiable"].append("contract_months")
    case["fixed_terms"].pop("contract_months")
    case["term_options"] = {"payment_days": list(range(14, 30)), "contract_months": list(range(1, 17))}
    threads["sup:A"] = [quote({"unit_price": 100}, attachments=[{"name": "quote.pdf", "text":
        "Our standard terms\nPayment terms (days net) 14\nContract term (months) 12"}])]
    agent, calls = agent_with_calls()
    with patch("tacto_negotiator.docs.quote_terms", wraps=quote_terms) as parsing:
        move = agent.decide(case, threads)
    assert not calls and move["message"]["term_sheets"]
    assert parsing.call_count <= 5  # independent of the 256 candidate packages
    assert all(must_haves_ok(case, sheet["terms"]) for sheet in move["message"]["term_sheets"])
