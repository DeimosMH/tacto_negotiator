"""Offline audit of public scoring and already-interpreted claims against hosted feedback.

This reproduces documented checks, not the host's private language interpreter or supplier brain.
Unknown claim types stay unmodeled instead of being awarded a fabricated verdict.
"""

import argparse
import json
import math
import statistics
from pathlib import Path

from .cost import cost_breakdown, must_haves_ok, score
from .data import DataPackage


def month_number(value):
    year, month = map(int, value.split("-")[:2])
    if not 1 <= month <= 12:
        raise ValueError("invalid month")
    return year * 12 + month


class ClaimChecker:
    """Stateful numeric/relevance checks: one instance per case, shared across its suppliers."""

    def __init__(self, case, facts):
        self.case, self.facts, self.used = case, facts, set()
        months = [month_number(f["month"]) for ref, f in facts.items() if ref.startswith("idx:") and f.get("month")]
        self.as_of = max(months, default=0)

    def check(self, supplier, claims, offers=None):
        offers = offers or {}
        results = []
        for index, claim in enumerate(claims[:3]):
            refs, kind = claim.get("refs", []), claim.get("type")
            rows = [offers.get(ref) or self.facts.get(ref) for ref in refs]
            if not refs or not all(rows):
                verdict = "contradicted"
            else:
                verdict = self._check(kind, refs, rows, supplier, claim.get("asserted") or {})
            # Hosted replays: every forecast claim after the first is a duplicate, whatever the part or month.
            keys = ["fc:"] if kind == "volume_change" else [(supplier, ref) if kind == "rival_quote" else ref
                                                            for ref in refs]
            if verdict == "verified":
                if any(key in self.used for key in keys):
                    verdict = "duplicate"
                else:
                    self.used.update(keys)
            results.append({"index": index, "result": verdict})
        return results

    def _check(self, kind, refs, rows, supplier, asserted):
        expected, percent = None, False
        parts = self.case["parts"]
        if kind == "index_change":
            if len(rows) != 2 or len({r.get("series") for r in rows}) != 1:
                return "irrelevant"
            first, last = sorted(rows, key=lambda r: r.get("month", ""))
            try:
                duration = month_number(last["month"]) - month_number(first["month"])
                recent = 0 <= self.as_of - month_number(last["month"]) <= 12
                if not 0 < duration <= 12 or not recent or first["value"] <= 0:
                    return "irrelevant"
                expected = {"change_pct": (last["value"] / first["value"] - 1) * 100}
            except (KeyError, TypeError, ValueError):
                return "unmodeled"
            percent = True
        elif kind == "rival_quote":
            allowed = set(self.case.get("refs", [])) | {(self.case["buyer"].get("alternative") or {}).get("ref")}
            if len(rows) != 1 or (refs[0].startswith("quote:") and refs[0] not in allowed):
                return "irrelevant"
            if rows[0].get("part") not in (None, *parts):
                return "irrelevant"
            expected = {"unit_price": rows[0]["unit_price"]}
        elif kind in {"volume_change", "should_cost"}:
            if len(rows) != 1 or rows[0].get("part") not in parts:
                return "irrelevant"
            key = "change_pct" if kind == "volume_change" else "unit_cost"
            expected, percent = {key: rows[0][key]}, kind == "volume_change"
        elif kind in {"quality_record", "delivery_record"}:
            if any(row.get("supplier") != supplier for row in rows):
                return "irrelevant"
            key = "ppm" if kind == "quality_record" else "otd_pct"
            expected = {key: statistics.mean(row[key] for row in rows)}
            percent = kind == "delivery_record"
        elif kind == "notice_period":
            notices = [row for ref, row in zip(refs, rows, strict=True) if ref.startswith("doc:")]
            if not notices or any(row.get("supplier") != supplier for row in rows):
                return "irrelevant"
            if notices[0].get("days") is None:
                return "unmodeled"
            expected = {"days": notices[0]["days"]}
        elif kind == "price_history":  # 2-8 orders of one case part, checked against the order prices
            if (not 2 <= len(rows) <= 8 or not all(ref.startswith("po:") for ref in refs)
                    or len({row.get("part") for row in rows}) != 1 or rows[0].get("part") not in parts
                    or any(row.get("supplier") != supplier for row in rows)):
                return "irrelevant"
            prices = asserted.get("prices")
            if prices is None:
                return "verified"  # without a figure the data's own values are taken
            if not isinstance(prices, list) or len(prices) != len(rows):
                return "contradicted"
            for actual, row in zip(prices, rows, strict=True):
                expected_value = row.get("unit_price")
                if (type(actual) not in (int, float) or type(expected_value) not in (int, float)
                        or abs(actual - expected_value) > max(.01, abs(expected_value) * .005) + 1e-9):
                    return "contradicted"
            return "verified"
        elif kind in {"supplier_news", "news"}:
            return "verified" if all(row.get("supplier") == supplier for row in rows) else "irrelevant"
        else:
            return "unmodeled"
        for key, expected_value in expected.items():
            actual = asserted.get(key, expected_value)
            if type(actual) not in (int, float) or not math.isfinite(actual):
                return "contradicted"
            tolerance = .5 if percent else max(.01, abs(expected_value) * .005)
            if abs(actual - expected_value) > tolerance + 1e-9:
                return "contradicted"
        return "verified"


def audit_feedback(feedback, facts):
    """Compare calculations with independent hosted results; retain every mismatch for inspection."""
    cost_checks, score_checks, claim_checks = 0, 0, 0
    health_sessions, mismatches, unmodeled = [], [], []
    for session in feedback["sessions"]:
        sid, case_id = session["session_id"], session["case_id"]
        case, result = feedback["cases"][case_id], session["result"]
        if result["deal"]:
            ours = cost_breakdown(case, result["terms"])
            for field, expected in result["cost_breakdown"].items():
                if field == "relationship":
                    if expected:
                        health_sessions.append({"session_id": sid, "case_id": case_id, "observed_eur": expected})
                    continue
                cost_checks += 1
                if abs(ours[field] - expected) > .011:
                    mismatches.append({"session_id": sid, "field": field, "local": ours[field], "hosted": expected})
            priced = round(sum(ours.values()) + result["cost_breakdown"].get("relationship", 0), 2)
            if abs(priced - result["c_deal"]) > .011:
                mismatches.append({"session_id": sid, "field": "total_with_observed_health",
                                   "local": priced, "hosted": result["c_deal"]})
            evaluated_cost = result["c_deal"]
            if not must_haves_ok(case, result["terms"]):
                evaluated_cost = None
        else:
            evaluated_cost = result.get("c_alt")
        predicted_score = score(result["c_open"], evaluated_cost, result["c_best"])
        score_checks += 1
        if abs(predicted_score - result["score"]) > 1e-9:
            mismatches.append({"session_id": sid, "field": "score", "local": predicted_score,
                               "hosted": result["score"]})
        checker, offers, pending = ClaimChecker(case, facts), {}, {}
        for event in session["trace"]:
            move = event["move"]
            if event["actor"] == "buyer":
                supplier = move.get("supplier_id")
                claims = move.get("interpreted", {}).get("claims", [])
                pending[supplier] = checker.check(supplier, claims, offers)
                continue
            supplier = event["actor"]
            if move.get("offer_id"):
                offers[move["offer_id"]] = move["terms"]
            for alternative in move.get("alternatives", []):
                offers[alternative["offer_id"]] = alternative["terms"]
            computed = pending.pop(supplier, [])
            actual = move.get("claim_results", [])
            for index, row in enumerate(computed):
                if row["result"] == "unmodeled":
                    unmodeled.append({"session_id": sid, "round": event["round"], "claim": index})
                    continue
                claim_checks += 1
                observed = actual[index]["result"] if index < len(actual) else "missing"
                if row["result"] != observed:
                    mismatches.append({"session_id": sid, "field": "claim", "round": event["round"],
                                       "local": row["result"], "hosted": observed})
    return {"sessions": len(feedback["sessions"]), "component_checks": cost_checks, "score_checks": score_checks,
            "claim_checks": claim_checks, "unmodeled_claims": unmodeled, "mismatches": mismatches,
            "observed_health_required": health_sessions,
            "limitations": ["Supplier health is an observed result component, not a prediction of private costs.",
                            "C_open and C_best are recorded references; unseen final references remain unknown.",
                            "Claims are already interpreted by the host; free-text interpretation is not cloned.",
                            "Matching a claim verdict does not predict the supplier's concession."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedback", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit_feedback(json.loads(args.feedback.read_text()), DataPackage(args.data).facts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("sessions", "component_checks", "score_checks", "claim_checks",
                                                  "mismatches", "unmodeled_claims")}))
    return bool(report["mismatches"])


if __name__ == "__main__":
    raise SystemExit(main())
