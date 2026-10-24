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
