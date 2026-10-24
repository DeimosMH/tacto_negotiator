"""Per-decision scripts/fast/strong routing with one shared action validator.

Completion callables accept an absolute monotonic ``deadline`` and must enforce it.
Routing uses observed data and remaining time; it never asks a model to grade itself.
"""

import json
import logging
import math
import time
from datetime import date

from ..cost import RELATIONSHIP_GOALS, complete_offer, must_haves_ok, total_cost
from ..docs import LABELS, standard_terms
from ..play import CLOSE_BEFORE_CAP_S, latest, live_threads, moves_left, standing
from .llm import ID_RE, SYSTEM, LLMAgent, parse

log = logging.getLogger("arena")
NUMERIC = set(LABELS.values()) - {"incoterm", "effective_date", "index_clause"}
INTEGER = {"payment_days", "lead_time_days", "volume_per_year", "contract_months", "ppm_commitment"}
NULLABLE = {"effective_date", "index_clause"}


def valid_value(field, value):
    """Validate before arithmetic or option snapping can hide malformed model values."""
    if value is None:
        return field in NULLABLE
    if field in NUMERIC:
        if type(value) not in (int, float) or not math.isfinite(value):
            return False
        if field in INTEGER and (type(value) is not int and not value.is_integer()):
            return False
        if field in {"payment_days", "contract_months"} and value > {"payment_days": 120, "contract_months": 36}[field]:
            return False
        return value > 0 if field in {"unit_price", "volume_per_year"} else value >= 0
    if field == "incoterm":
        return isinstance(value, str) and value in {"EXW", "FCA", "DAP"}
    if field == "effective_date":
        try:
            return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    if field == "index_clause":
        if not isinstance(value, dict) or set(value) != {"series", "material_share", "deadband_pct", "base_month"}:
            return False
        share, band = value["material_share"], value["deadband_pct"]
        try:
            month = value["base_month"]
            return (isinstance(value["series"], str) and bool(value["series"])
                    and type(share) in (int, float) and math.isfinite(share) and 0 <= share <= 1
                    and type(band) in (int, float) and math.isfinite(band) and band >= 0
                    and isinstance(month, str) and len(month) == 7
                    and date.fromisoformat(month + "-01").strftime("%Y-%m") == month)
        except ValueError:
            return False
    return False


class RoutedAgent:
    """Use a tested script, one fast completion, or an optional budget-qualified strong completion."""

    def __init__(self, fast, strong=None, package=None, *, max_decision_seconds=25,
                 strong_min_seconds=20, clock=time.monotonic):
        if not (math.isfinite(max_decision_seconds) and max_decision_seconds > 0
                and math.isfinite(strong_min_seconds) and strong_min_seconds > 0):
            raise ValueError("decision and strong-model budgets must be finite and positive")
        self.fast, self.strong, self.clock = fast, strong, clock
        self.max_decision_seconds, self.strong_min_seconds = max_decision_seconds, strong_min_seconds
        self.context = LLMAgent(None, package)
        self.last_decision = {}

    def _known(self, case, events):
        sheet, terms = standing(events)
        std = standard_terms(events)
        resolved = {**std, **case.get("fixed_terms", {}), **terms}
        required = set(case["negotiable"]) | {rule["field"] for rule in case["buyer"].get("must_haves", [])}
        missing = sorted(f for f in required if f not in resolved or not valid_value(f, resolved[f]))
        for message in reversed(events):
            if message.get("from") == "buyer":
                continue
            quotes = [att for att in message.get("attachments") or []
                      if att.get("name", "").lower().startswith("quote")
                      and att.get("name", "").lower().endswith(".pdf")]
            if quotes:
                if any(att.get("read_failed") for att in quotes):
                    missing.append("supplier quote defaults")
                break  # a newer readable quote supersedes older document failures
        return sheet, terms, std, missing

    def validate(self, case, threads, decision):
        """One gate for scripts, model proposals and fallback. Return a wire move or None."""
        try:
            if not isinstance(decision, dict):
                return None
            if set(decision) - {"supplier_id", "action", "subject", "body", "term_sheets"}:
                return None
            sid, action = decision.get("supplier_id"), decision.get("action")
            if sid not in case["suppliers"] or sid not in live_threads(threads):
                return None
            if action == "walk_away":
                return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}
            events = threads[sid]
            sheet, terms, std, missing = self._known(case, events)
            if action == "accept":
                if not isinstance(sheet, str) or not sheet or missing or set(terms) - set(case["negotiable"]):
                    return None
                if any(not valid_value(f, v) for f, v in {**std, **case.get("fixed_terms", {}), **terms}.items()):
                    return None
                for field, value in terms.items():
                    options = case.get("term_options", {}).get(field)
                    if options and value not in options and value != std.get(field):
                        return None
                if not must_haves_ok(case, terms, std):
                    return None
                cost = total_cost(case, terms, std)
                alt = case["buyer"].get("alternative") or {}
                if alt.get("kind") not in (None, "none"):
                    other = alt.get("terms", {})
                    if not valid_value("unit_price", other.get("unit_price")):
                        return None
                    if any(not valid_value(f, v) for f, v in other.items()):
                        return None
                    other_cost = total_cost(case, other)
                    if not math.isfinite(other_cost) or cost >= other_cost:
                        return None
                if not math.isfinite(cost):
                    return None
                return {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": sheet}}}
            if action != "send" or moves_left(case, events) <= 0:
                return None
            body, subject = decision.get("body", ""), decision.get("subject", f"Re: {case['title']}")
            if not isinstance(body, str) or len(body) > 8000 or not isinstance(subject, str) or len(subject) > 300:
                return None
            offers = {standing(ev)[0] for ev in threads.values()}
            if any(ref not in offers and not self.context.fact(case, ref) for ref in ID_RE.findall(body)):
                return None
            sheets = decision.get("term_sheets", [])
            if not isinstance(sheets, list) or len(sheets) > 3:
                return None
            clean = []
            for entry in sheets:
                proposed = entry.get("terms", entry) if isinstance(entry, dict) else None
                if not isinstance(proposed, dict) or set(proposed) - set(case["negotiable"]):
                    return None
                if "terms" in entry and set(entry) != {"terms"}:
                    return None
                if any(not valid_value(f, v) for f, v in proposed.items()):
                    return None
                for field, value in proposed.items():
                    options = case.get("term_options", {}).get(field)
                    if options and value not in options and value != std.get(field):
                        return None
                # Full outgoing terms bind; proposing missing fields differs from accepting missing defaults.
                complete = complete_offer(case, {**std, **terms, **proposed})
                if set(complete) != set(case["negotiable"]) or any(not valid_value(f, v) for f, v in complete.items()):
                    return None
                if not must_haves_ok(case, complete) or not math.isfinite(total_cost(case, complete)):
                    return None
                if complete not in clean:
                    clean.append(complete)
            move = {"supplier_id": sid, "message": {
                "subject": subject, "body": body, "term_sheets": [{"terms": t} for t in clean]}}
            return move if len(json.dumps({"message": move["message"]}).encode()) <= 64_000 else None
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            return None

    def _closing(self, case, threads):
        accepted = []
        for sid in live_threads(threads):
            move = self.validate(case, threads, {"supplier_id": sid, "action": "accept"})
            if move:
                accepted.append((total_cost(case, standing(threads[sid])[1], standard_terms(threads[sid])), sid, move))
        if accepted:
            return min(accepted, key=lambda row: (row[0], row[1]))[2]
        for sid in live_threads(threads):
            if move := self.validate(case, threads, {"supplier_id": sid, "action": "walk_away"}):
                return move
        raise ValueError("no live supplier belongs to this case")

    def _record(self, case, route, reason, start, deadline, fallback=False):
        self.last_decision = {"case_id": case["case_id"], "route": route, "reason": reason,
                              "elapsed_s": max(0, self.clock() - start),
                              "budget_s": max(0, deadline - start), "fallback": fallback}
        log.info("buyer case=%s route=%s reason=%s elapsed=%.3fs budget=%.3fs fallback=%s",
                 case["case_id"], route, reason,
                 self.last_decision["elapsed_s"], self.last_decision["budget_s"], fallback)

    def closing_move(self, case, threads):
        start = self.clock()
        move = self._closing(case, threads)
        if self.last_decision.get("case_id") == case["case_id"] and self.last_decision.get("fallback"):
            self.last_decision["closure"] = "deadline_close"
            log.info("buyer case=%s closure=deadline_close attempted_route=%s fallback=True",
                     case["case_id"], self.last_decision["route"])
        else:
            self._record(case, "script", "deadline_close", start, start)
        return move

    def _script(self, case, threads):
        live = live_threads(threads)
        if all(latest(threads[s]).get("status") in {"agreed", "final_offer"}
               or moves_left(case, threads[s]) <= 0 for s in live):
            return "terminal_offers", self._closing(case, threads)
        for sid in live:
            _, _, _, missing = self._known(case, threads[sid])
            already_asked = any(m.get("from") == "buyer" and m.get("body", "").startswith("Please confirm these terms:")
                                for m in threads[sid])
            if missing and not already_asked and moves_left(case, threads[sid]) > 1:
                d = {"supplier_id": sid, "action": "send",
                     "body": f"Please confirm these terms: {', '.join(missing)}. Please provide a complete term sheet."}
                if move := self.validate(case, threads, d):
                    return "missing_quote_terms", move
        if (len(live) == 1 and set(case["negotiable"]) <= {"unit_price", "payment_days"}
                and case["buyer"].get("category_goal", "routine") in {"routine", "leverage"}):
            sid = live[0]
            _, terms, std, missing = self._known(case, threads[sid])
            if not missing and not any(m.get("from") == "buyer" for m in threads[sid]):
                proposal = {**std, **terms, "unit_price": round(terms["unit_price"] * 0.9, 2)}
                d = {"supplier_id": sid, "action": "send", "body": "Thank you. Please consider this price proposal.",
                     "term_sheets": [complete_offer(case, proposal)]}
                move = self.validate(case, threads, d)
                if move and total_cost(case, move["message"]["term_sheets"][0]["terms"]) < total_cost(case, terms, std):
                    return "simple_opening_counter", move
        return None

    def decide(self, case, threads):
        return self.decide_timed(case, threads, deadline=self.clock() + self.max_decision_seconds)

    def decide_timed(self, case, threads, *, deadline, wave_seconds_left=None):
        start = self.clock()
        if not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        deadline = min(deadline, start + self.max_decision_seconds)
        if deadline <= start or (wave_seconds_left is not None and wave_seconds_left <= CLOSE_BEFORE_CAP_S):
            move = self._closing(case, threads)
            self._record(case, "script", "deadline_close", start, deadline)
            return move
        route, reason = "fast", "semantic_decision"
        try:
            if scripted := self._script(case, threads):
                reason, move = scripted
                self._record(case, "script", reason, start, deadline)
                return move
            complex_ = (case["buyer"].get("category_goal") in RELATIONSHIP_GOALS
                        or len(case["negotiable"]) >= 4
                        or any(r.get("result") == "contradicted" for sid in live_threads(threads)
                               for r in latest(threads[sid]).get("claim_results", [])))
            if case["case_id"] != self.context.case_id:
                self.context.brief = self.context.prepare(case)
                self.context.case_id = case["case_id"]
            prompt = self.context.render(case, threads)
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("decision budget exhausted during preparation")
            if complex_ and self.strong is not None and remaining >= self.strong_min_seconds:
                route, reason = "strong", "complex_tradeoffs"
            elif complex_:
                reason = "complex_fast_budget" if self.strong is not None else "strong_unconfigured"
            complete = self.strong if route == "strong" else self.fast
            reply = complete(SYSTEM, prompt, deadline=deadline)
            if self.clock() >= deadline:
                raise TimeoutError("late completion discarded")
            if not isinstance(reply, str) or len(reply) > 64_000:
                raise ValueError("invalid completion size or type")
            move = self.validate(case, threads, parse(reply))
            if move is None:
                raise ValueError("model action rejected")
            self._record(case, route, reason, start, deadline)
            return move
        except Exception as error:
            # Providers enforce cancellation; this also discards late results from injected completions.
            move = self._closing(case, threads)
            self._record(case, route, type(error).__name__, start, deadline, fallback=True)
            return move
