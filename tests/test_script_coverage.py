"""Quantitative script coverage and near misses, without model calls or hidden supplier state."""

import copy
import json
from unittest.mock import patch

import pytest

from tacto_negotiator.agents.routed import MAX_SCRIPT_CANDIDATES, RoutedAgent
from tacto_negotiator.cost import must_haves_ok, total_cost
from tacto_negotiator.docs import quote_terms
from tacto_negotiator.play import GAP_STEP, ask_limit, gap_step


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
    # 22.5 % routine anchor on the supplier's own cash terms: longer payment and the freight leg are
    # not asked for unpaid at the opening (suppliers price them at their own value)
    assert packages[0] == {"unit_price": 85.25, "payment_days": 14, "incoterm": "EXW"}
    # sheets dearer for us than the cheapest are dropped: the supplier would pick exactly those
    cheapest = min(total_cost(case, t) for t in packages)
    assert all(total_cost(case, t) <= cheapest * 1.003 + 0.01 for t in packages)
    assert len(packages) <= 3 and all(set(t) == set(case["negotiable"]) for t in packages)
    assert total_cost(case, packages[0]) < total_cost(case, threads["sup:A"][0]["term_sheets"][0]["terms"])
    assert not move["message"].get("action")
    ceiling = ask_limit(case, threads["sup:A"])
    assert all(total_cost(case, t) <= ceiling and t["unit_price"] <= 85.25 for t in packages)


def test_covering_more_demand_does_not_raise_the_opening_unit_price(negotiation):
    case, threads = negotiation
    case["buyer"]["horizon_years"] = 2
    case["fixed_terms"].pop("contract_months")
    case["negotiable"].append("contract_months")
    case["term_options"]["contract_months"] = [12, 24, 36]
    threads["sup:A"][0]["term_sheets"][0]["terms"]["contract_months"] = 12
    agent, calls = agent_with_calls()
    move = agent.decide(case, threads)
    assert not calls
    packages = [s["terms"] for s in move["message"]["term_sheets"]]
    assert any(t["contract_months"] >= 24 for t in packages)
    assert all(t["unit_price"] <= 85.25 for t in packages)


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


def test_quality_opening_repairs_only_the_must_have_and_trades_chargebacks_at_equal_cost(negotiation):
    """Hosted P11: three sheets that all asked a EUR 500 chargeback at one price made the supplier
    raise 47.00 to 49.50-50.00 and hold that package to a 48.50 final."""
    case, threads = negotiation
    case["negotiable"] = ["ppm_commitment", "chargeback_eur_per_complaint", "unit_price"]
    case["fixed_terms"].update(payment_days=60, incoterm="DAP")
    case["term_options"] = {"ppm_commitment": [300, 800, 1500, 2500],
                            "chargeback_eur_per_complaint": [0, 150, 300, 500]}
    case["buyer"].update(category_goal="quality_first", cost_per_defect=450,
                         must_haves=[{"field": "ppm_commitment", "op": "<=", "value": 1500}])
    threads["sup:A"] = [quote({"unit_price": 47.0, "ppm_commitment": 6400, "chargeback_eur_per_complaint": 0})]
    agent, calls = agent_with_calls()
    opening = agent.decide(case, threads)
    assert calls == [] and agent.last_decision["reason"] == "must_have_repair"
    sheets = [s["terms"] for s in opening["message"]["term_sheets"]]
    # the supplier's own chargeback, ppm moved only as far as the must-have requires, at the anchor
    assert sheets[0] == {"ppm_commitment": 1500, "chargeback_eur_per_complaint": 0, "unit_price": 38.07}
    assert all(s["chargeback_eur_per_complaint"] == 0 for s in sheets)
    # later rounds still offer the chargeback, but only at equal cost to us (a higher price)
    record_round(threads, opening, {"unit_price": 47.0, "ppm_commitment": 1500,
                                    "chargeback_eur_per_complaint": 0})
    follow = agent.decide(case, threads)
    sheets = [s["terms"] for s in follow["message"]["term_sheets"]]
    costs = [total_cost(case, s) for s in sheets]
    assert max(costs) <= min(costs) * 1.003 + 0.01
    by_chargeback = {s["chargeback_eur_per_complaint"]: s["unit_price"] for s in sheets}
    assert 0 in by_chargeback and len(by_chargeback) > 1
    assert all(price > by_chargeback[0] for charge, price in by_chargeback.items() if charge > 0)


def test_opening_still_asks_security_terms_the_supplier_did_not_offer(negotiation):
    """Contract length is security, not cash, for the supplier: the opening may still extend it."""
    case, threads = negotiation
    case["buyer"]["horizon_years"] = 2
    case["fixed_terms"].pop("contract_months")
    case["negotiable"].append("contract_months")
    case["term_options"]["contract_months"] = [12, 24, 36]
    threads["sup:A"][0]["term_sheets"][0]["terms"]["contract_months"] = 12
    agent, _ = agent_with_calls()
    sheets = [s["terms"] for s in agent.decide(case, threads)["message"]["term_sheets"]]
    assert sheets[0]["contract_months"] >= 24
    assert sheets[0]["payment_days"] == 14 and sheets[0]["incoterm"] == "EXW"


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


def test_rival_evidence_uses_only_feasible_current_quotes_once_per_bidder(negotiation):
    case, threads = negotiation
    case["suppliers"].append("sup:B")
    threads["sup:B"] = [quote({"unit_price": 90, "payment_days": 60, "incoterm": "DAP"}, number=2)]
    agent, _ = agent_with_calls()

    def rival():
        return [f.sentence for f in agent.bank.assign(case, threads, "sup:A") if f.kind == "rival_quote"]

    [text] = rival()
    assert "EUR 90" in text and "offer:2" in text
    threads["sup:A"].append({"from": "buyer", "body": text})
    assert rival() == []
    threads["sup:A"].pop()
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 90}]
    assert rival() == []


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
    # every round moves, by a share of the gap: never straight to the supplier's number. Routine
    # suppliers answer a share of our move before a final offer, so routine steps are larger.
    step = gap_step(case)
    assert step == 0.4 and gap_step({**case, "buyer": {**case["buyer"], "category_goal": "leverage"}}) == GAP_STEP
    gap = current_cost - previous_cost
    assert previous_cost + 0.5 * step * gap <= total_cost(case, proposed) <= previous_cost + step * gap + 0.01
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
    assert parsing.call_count <= 12  # constant per stage, independent of the 256 candidate packages
    assert all(must_haves_ok(case, sheet["terms"]) for sheet in move["message"]["term_sheets"])
