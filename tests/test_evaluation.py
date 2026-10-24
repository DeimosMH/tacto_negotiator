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
    from tacto_negotiator.cost import cheapest_price, decision_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid, std = "sup:70510", {"ppm_commitment": 250, "chargeback_eur_per_complaint": 0.0}
    sheet = {"unit_price": 373.88, "payment_days": 90, "index_clause": None}
    floor = cheapest_price(case, sheet, std, sid)
    limit = decision_cost(case, {**sheet, "unit_price": floor + 4}, std, sid)  # hosted P02 repeated the floor
    price = RoutedAgent._price_within(case, sheet, std, sid, limit)
    assert floor + 3.9 <= price <= floor + 4.01 and decision_cost(case, {**sheet, "unit_price": price}, std, sid) <= limit
