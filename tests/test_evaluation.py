"""The offline audit must reproduce every recorded hosted cost, score and claim verdict."""

import json
from pathlib import Path

from tacto_negotiator.data import DataPackage
from tacto_negotiator.evaluation import ClaimChecker, audit_feedback

ROOT = Path(__file__).resolve().parents[1]


def test_audit_matches_recorded_hosted_feedback():
    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    report = audit_feedback(feedback, DataPackage(ROOT / "data").facts)
    assert report["sessions"] == 32 and report["claim_checks"] > 30
    assert report["mismatches"] == [] and report["unmodeled_claims"] == []


def test_forecast_claims_count_once_per_case_and_rival_quotes_once_per_supplier():
    facts = {"fc:A@2026-09": {"part": "part:A", "change_pct": 6.0},
             "fc:B@2026-09": {"part": "part:B", "change_pct": 9.0},
             "quote:1": {"part": "part:A", "unit_price": 5.0}}
    case = {"parts": ["part:A", "part:B"], "refs": ["quote:1"], "buyer": {}}
    checker = ClaimChecker(case, facts)
    claims = [{"type": "volume_change", "refs": ["fc:A@2026-09"]},
              {"type": "volume_change", "refs": ["fc:B@2026-09"], "asserted": {"change_pct": 9.0}},
              {"type": "rival_quote", "refs": ["quote:1"]}]
    assert [r["result"] for r in checker.check("sup:X", claims)] == ["verified", "duplicate", "verified"]
    assert [r["result"] for r in checker.check("sup:Y", claims[2:])] == ["verified"]
    assert [r["result"] for r in checker.check("sup:Y", claims[2:])] == ["duplicate"]


def test_supplier_estimates_reproduce_hosted_health_charges():
    from tacto_negotiator.cost import decision_cost, total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    package = DataPackage(ROOT / "data")
    checked = 0
    for session in feedback["sessions"]:
        result, case = session["result"], dict(feedback["cases"][session["case_id"]])
        if not result["deal"] or case["buyer"].get("category_goal") not in ("stabilise", "secure_supply"):
            continue
        case["_supplier_estimates"] = package.supplier_estimates(case)
        sid, standard = result["supplier_id"], session["threads"][result["supplier_id"]]["standard"]
        health = decision_cost(case, result["terms"], standard, sid) - total_cost(case, result["terms"], standard)
        assert abs(health - result["cost_breakdown"].get("relationship", 0)) < 400, session["session_id"]
        checked += 1
    assert checked >= 8


def test_counter_price_moves_above_the_health_floor_when_the_limit_allows():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import cheapest_price, total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid, std = "sup:70510", {"ppm_commitment": 250, "chargeback_eur_per_complaint": 0.0}
    sheet = {"unit_price": 373.88, "payment_days": 90, "index_clause": None}
    floor = cheapest_price(case, sheet, std, sid)
    limit = total_cost(case, {**sheet, "unit_price": floor + 4}, std)  # hosted P02 repeated the floor
    price = RoutedAgent._price_within(case, sheet, std, sid, limit)
    assert floor + 3.99 <= price <= floor + 4.01
    assert total_cost(case, {**sheet, "unit_price": price}, std) <= limit
    below = total_cost(case, {**sheet, "unit_price": floor - 5}, std)
    assert RoutedAgent._price_within(case, sheet, std, sid, below) == floor  # never below the health floor


def test_concession_schedule_moves_the_price_not_the_flat_health_cost():
    """Hosted P02 v6: 20 % of a gap in C including supplier health moved the ask 361.51 -> 366.94
    against a 373.88 counter; the schedule now moves 20 % of the price gap."""
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.play import ask_limit, known_offer

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid = "sup:70510"
    quote = [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\nIncoterm DAP"}]

    def sheet(price, pay):
        return {"unit_price": price, "payment_days": pay, "index_clause": None}

    events = [{"from": "supplier", "status": "open", "attachments": quote,
               "term_sheets": [{"term_sheet_id": "offer:0", "terms": sheet(370.96, 30)}]},
              {"from": "buyer", "term_sheets": [{"terms": sheet(361.51, 90)}]},
              {"from": "supplier", "status": "open", "attachments": quote,
               "term_sheets": [{"term_sheet_id": "offer:1", "terms": sheet(373.88, 90)}]}]
    limit = ask_limit(case, events, sid)
    std = known_offer(case, events)[2]
    price = RoutedAgent._price_within(case, {**sheet(373.88, 90)}, std, sid, limit)
    assert 363.5 <= price <= 364.5  # 361.51 + 0.2 x 12.37


def test_opening_offers_the_suppliers_lead_time_at_equal_cost():
    """Hosted P10: one 42-day sheet drew a 33.8 final offer (0.555); with the supplier's 56 days at an
    equal-cost price it answered 31.6 (0.671). Scripts and model sheets both carry that variant."""
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import decision_cost
    from tacto_negotiator.play import known_offer

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P10"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid = "sup:70410"
    opening = {"unit_price": 32.2, "lead_time_days": 54, "volume_per_year": 19890, "contract_months": 12}
    quote = [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\nIncoterm DAP"}]
    threads = {sid: [{"from": "supplier", "status": "open", "attachments": quote,
                      "term_sheets": [{"term_sheet_id": "offer:0", "terms": opening}]}]}
    agent = RoutedAgent(None)
    scripted = agent._structured_bargain(case, threads)[1]["message"]["term_sheets"]
    model = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "term_sheets": [
        {"unit_price": 26.3, "volume_per_year": 23400, "contract_months": 36, "lead_time_days": 42}]})
    for sheets in (scripted, model["message"]["term_sheets"]):
        terms = [sheet["terms"] for sheet in sheets]
        slow = [t for t in terms if t["lead_time_days"] == 56]
        assert slow and all(t["volume_per_year"] >= 22230 and t["contract_months"] >= 24 for t in terms)
        fast = [t for t in terms if t["lead_time_days"] == 42]
        assert slow[0]["unit_price"] < min(t["unit_price"] for t in fast)
        std = known_offer(case, threads[sid])[2]
        assert decision_cost(case, slow[0], std, sid) <= min(decision_cost(case, t, std, sid) for t in fast) * 1.03


def test_an_equal_cost_sheet_at_an_implausible_price_is_not_sent():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P18"])
    sid = case["suppliers"][0]
    opening = {"from": "sales@70740", "status": "open", "body": "Our quote.",
               "term_sheets": [{"term_sheet_id": "offer:0", "terms": {
                   "unit_price": 6.1, "payment_days": 30, "index_clause": None, "contract_months": 12}}],
               "attachments": [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30"}]}
    ours = {"from": "buyer", "body": "Our package.", "term_sheets": [{"terms": {
        "unit_price": 4.57, "payment_days": 90, "index_clause": None, "contract_months": 24}}]}
    counter = {**opening, "term_sheets": [{"term_sheet_id": "offer:1", "terms": {
        "unit_price": 5.4, "payment_days": 90, "index_clause": None, "contract_months": 24}}]}
    threads = {sid: [opening, ours, counter]}
    long = {"unit_price": 4.75, "payment_days": 90, "index_clause": None, "contract_months": 24}
    short = {**long, "contract_months": 12}  # hosted P18 v8: equal cost only because demand goes uncovered
    low, high = 0.5, 4.75
    for _ in range(40):
        mid = (low + high) / 2
        fits = total_cost(case, {**short, "unit_price": mid}) <= total_cost(case, long)
        low, high = (mid, high) if fits else (low, mid)
    short["unit_price"] = round(low, 2)
    move = RoutedAgent(None).validate(case, threads, {"supplier_id": sid, "action": "send", "body": "Packages.",
                                                      "term_sheets": [long, short]})
    prices = [s["terms"]["unit_price"] for s in move["message"]["term_sheets"]]
    assert short["unit_price"] < 0.9 * long["unit_price"] and min(prices) >= 0.9 * max(prices)
