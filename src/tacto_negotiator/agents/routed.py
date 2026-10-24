"""Per-decision scripts/fast/strong routing with one shared action validator.

Completion callables accept an absolute monotonic ``deadline`` and must enforce it.
Routing uses observed data and remaining time; it never asks a model to grade itself.
"""

import itertools
import json
import logging
import math
import re
import time

from ..cost import (
    RELATIONSHIP_GOALS,
    cheapest_price,
    complete_offer,
    decision_cost,
    must_haves_ok,
    total_cost,
    valid_value,
)
from ..play import (
    ANCHOR_BY_GOAL,
    CLOSE_BEFORE_CAP_S,
    acceptable_offer,
    ask_limit,
    closing_move,
    known_offer,
    latest,
    live_threads,
    moves_left,
    opening_price_cap,
    standing,
)
from .llm import ID_RE, SYSTEM, LLMAgent, parse

log = logging.getLogger("arena")
MAX_SCRIPT_CANDIDATES = 256
# shortcut: conservative phrase matching; use structured side-condition extraction for broader prose coverage.
CONDITIONAL_WORDING = ("subject to", "provided that", "only if", "pending approval", "conditional on", "unless",
                       "please clarify", "please explain", "capacity constraint", "of our cost", "whole range",
                       "faster payment", "alternatively", "no-go")


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
            if action == "accept":
                accepted = acceptable_offer(case, events, sid)
                return {"supplier_id": sid, "message": {"action": {
                    "type": "accept", "term_sheet_id": accepted[0]}}} if accepted else None
            _, terms, std, _ = known_offer(case, events)
            if action != "send" or moves_left(case, events) <= 0:
                return None
            body, subject = decision.get("body", ""), decision.get("subject", f"Re: {case['title']}")
            if not isinstance(body, str) or len(body) > 8000 or not isinstance(subject, str) or len(subject) > 300:
                return None
            offers = {sheet.get("term_sheet_id") for supplier in live_threads(threads)
                      for sheet in latest(threads[supplier]).get("term_sheets", [])}
            if any(ref not in offers and not self.context.fact(case, ref) for ref in ID_RE.findall(body)):
                return None
            body = self._strip_wasted_claims(case, threads, body)
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
                complete = complete_offer(case, {**std, **terms, **proposed}, std)
                if set(complete) != set(case["negotiable"]) or any(not valid_value(f, v) for f, v in complete.items()):
                    return None
                if not must_haves_ok(case, complete) or not math.isfinite(decision_cost(case, complete, std, sid)):
                    return None
                if complete not in clean:
                    clean.append(complete)
            limit = ask_limit(case, events, sid)  # the shared concession schedule binds scripts and models
            if any(decision_cost(case, t, std, sid) > limit * 1.005 + 0.01 for t in clean):
                return None
            if clean and not any(e.get("from") == "buyer" and e.get("term_sheets") for e in events):
                cap = opening_price_cap(case, events, sid)  # per package: its own health floor may be higher
                if any(t["unit_price"] > max(cap, cheapest_price(case, t, std, sid) or 0) + 0.02 for t in clean):
                    return None
            if clean:  # the supplier picks the sheet dearest for us (hosted P03): keep equal-cost sheets only
                cheapest = min(decision_cost(case, t, std, sid) for t in clean)
                clean = [t for t in clean if decision_cost(case, t, std, sid) <= cheapest * 1.003 + 0.01]
            move = {"supplier_id": sid, "message": {
                "subject": subject, "body": body, "term_sheets": [{"terms": t} for t in clean]}}
            return move if len(json.dumps({"message": move["message"]}).encode()) <= 64_000 else None
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            return None

    @staticmethod
    def _strip_wasted_claims(case, threads, body):
        """Remove IDs whose check cannot help: hosted replays wasted 18 of 38 claims on already-cited
        IDs, any second forecast (counted once per case), and quotes outside the case."""
        cited = {ref for events in threads.values() for message in events if message.get("from") == "buyer"
                 for ref in ID_RE.findall(message.get("body", ""))}
        quotes = set(case.get("refs", [])) | {(case["buyer"].get("alternative") or {}).get("ref")}
        forecast = any(ref.startswith("fc:") for ref in cited)
        kept = set()

        def keep(match):
            nonlocal forecast
            ref = match.group(0)
            if ref in kept:
                return ref
            if ref in cited or (ref.startswith("quote:") and ref not in quotes) or (ref.startswith("fc:") and forecast):
                return ""
            forecast = forecast or ref.startswith("fc:")
            kept.add(ref)
            return ref

        return re.sub(r"\s*\(\s*[,;\s]*\)", "", ID_RE.sub(keep, body)).strip()

    def _record(self, case, route, reason, start, deadline, fallback=False):
        self.last_decision = {"case_id": case["case_id"], "route": route, "reason": reason,
                              "elapsed_s": max(0, self.clock() - start),
                              "budget_s": max(0, deadline - start), "fallback": fallback}
        log.info("buyer case=%s route=%s reason=%s elapsed=%.3fs budget=%.3fs fallback=%s",
                 case["case_id"], route, reason,
                 self.last_decision["elapsed_s"], self.last_decision["budget_s"], fallback)

    def _prepare(self, case):
        """Attach supplier-health estimates once per case (only for relationship goals with data)."""
        package = getattr(self.context, "package", None)
        if "_supplier_estimates" not in case and package is not None and hasattr(package, "supplier_estimates"):
            case["_supplier_estimates"] = package.supplier_estimates(case)

    def closing_move(self, case, threads):
        self._prepare(case)
        start = self.clock()
        move = closing_move(case, threads)
        if self.last_decision.get("case_id") == case["case_id"] and self.last_decision.get("fallback"):
            self.last_decision["closure"] = "deadline_close"
            log.info("buyer case=%s closure=deadline_close attempted_route=%s fallback=True",
                     case["case_id"], self.last_decision["route"])
        else:
            self._record(case, "script", "deadline_close", start, start)
        return move

    def _semantic_blocker(self, case, threads):
        if self.context.case_id == case["case_id"]:
            return True  # Preserve the model's negotiation context after a semantic exchange.
        if case["buyer"].get("category_goal", "routine") not in {"routine", "leverage", "quality_first"}:
            return True
        for sid in live_threads(threads):
            reply = latest(threads[sid])
            if any(r.get("result") == "contradicted" for r in reply.get("claim_results", [])):
                return True
            if any(word in reply.get("body", "").lower() for word in CONDITIONAL_WORDING):
                return True
        return False

    def _plateau(self, case, events):
        quotes = []
        for index, event in enumerate(events):
            if event.get("from") != "buyer" and event.get("term_sheets"):
                _, terms, std, missing = known_offer(case, events[:index + 1])
                quotes.append(None if missing else {**std, **case.get("fixed_terms", {}), **terms})
        return len(quotes) >= 3 and quotes[-1] is not None and quotes[-1] == quotes[-2] == quotes[-3]

    def _packages(self, case, options, target_cost, alternative, reference_price, *, price_ceiling=math.inf,
                  sid=None, standard=None):
        """Price each trade under the same full-cost ceiling, including the outside option."""
        fields = [field for field in case["negotiable"] if field != "unit_price"]
        rows = []
        ceiling = min(target_cost, alternative - 0.01)
        for values in itertools.product(*options):
            proposal = dict(zip(fields, values, strict=True))
            # Routine/leverage C is affine in price for fixed terms; the reference price limits rounding error.
            base = total_cost(case, {**proposal, "unit_price": 0})
            reference_cost = total_cost(case, {**proposal, "unit_price": reference_price})
            slope = (reference_cost - base) / reference_price
            if not math.isfinite(slope) or slope <= 0:
                continue
            proposal["unit_price"] = min(price_ceiling, math.floor((ceiling - base) / slope * 100) / 100)
            if proposal["unit_price"] <= 0:
                continue
            floor = cheapest_price(case, proposal, standard, sid)  # never squeeze into the health penalty
            if floor and proposal["unit_price"] < floor:
                proposal["unit_price"] = floor
            cost = decision_cost(case, proposal, standard, sid)
            if cost > ceiling:
                proposal["unit_price"] = round(proposal["unit_price"] - 0.01, 2)
                cost = decision_cost(case, proposal, standard, sid)
            if proposal["unit_price"] <= 0 or not math.isfinite(cost) or cost > ceiling or cost >= alternative:
                continue
            if must_haves_ok(case, proposal):
                rows.append((cost, proposal))
        return sorted(rows, key=lambda row: decision_cost(case, {**row[1], "unit_price": reference_price},
                                                          standard, sid))

    def _forecast_evidence(self, case, threads):
        """One forecast argument per case: hosted replays mark any later `fc:` claim duplicate, even for
        another part or month, so extra forecasts would only spend claim slots."""
        facts = {**getattr(self.context.package, "facts", {}), **case.get("facts", {})}
        if any(ref.startswith("fc:") for events in threads.values() for message in events
               if message.get("from") == "buyer" for ref in ID_RE.findall(message.get("body", ""))):
            return ""
        candidates = []
        for part in case["parts"]:
            refs = sorted(ref for ref, fact in facts.items() if ref.startswith("fc:") and fact.get("part") == part)
            if not refs:
                continue
            ref = refs[-1]
            change, volume = facts[ref].get("change_pct"), facts[ref].get("volume_per_year") or 1
            if type(change) in (int, float) and math.isfinite(change) and change > 0:
                candidates.append((change * volume, part, change, ref))
        if not candidates:
            return ""
        _, part, change, ref = max(candidates)
        return (f"The forecast for {part} increased by {change:g}% ({ref}); "
                "please reflect this additional business in your price.")

    def _rival_evidence(self, case, threads, sid):
        current = acceptable_offer(case, threads[sid], sid)
        cited = {ref for message in threads[sid] if message.get("from") == "buyer"
                 for ref in ID_RE.findall(message.get("body", ""))}
        rivals = [(accepted[1], other, accepted[0]) for other in live_threads(threads) if other != sid
                  if (accepted := acceptable_offer(case, threads[other], other)) and accepted[0] not in cited
                  and (not current or accepted[1] < current[1])]
        if not rivals:
            return ""
        _, other, ref = min(rivals)
        price = standing(threads[other])[1]["unit_price"]
        return f"A valid competing offer is EUR {price:g} per unit ({ref}); please improve your complete package."

    def _structured_bargain(self, case, threads):
        """Use public quotes/options only; do not estimate a hidden floor or train on simulator rules."""
        records, combinations = [], 0
        alternative = math.inf
        alt = case["buyer"].get("alternative") or {}
        if alt.get("kind") not in (None, "none"):
            other = alt.get("terms", {})
            if not valid_value("unit_price", other.get("unit_price")) or any(
                not valid_value(field, value) for field, value in other.items()
            ):
                return None
            alternative = total_cost(case, other)
            if not math.isfinite(alternative):
                return None
        for sid in live_threads(threads):
            events = threads[sid]
            _, terms, std, missing = known_offer(case, events)
            if missing:
                return None
            resolved = {**std, **case.get("fixed_terms", {}), **terms}
            current = {field: resolved[field] for field in case["negotiable"]}
            sent = [event for event in events if event.get("from") == "buyer" and event.get("term_sheets")]
            if any(event.get("from") == "buyer" and not event.get("term_sheets")
                   and not event.get("body", "").startswith("Please confirm these terms:") for event in events):
                return None  # a prose-only exchange may carry unresolved semantics
            terminal = latest(events).get("status") in {"agreed", "final_offer"} or moves_left(case, events) <= 0
            record = {"sid": sid, "current": current, "std": std, "sent": sent, "terminal": terminal,
                      "plateau": len(sent) >= 2 and self._plateau(case, events)}
            records.append(record)
            if terminal or record["plateau"]:
                continue
            options = []
            for field in case["negotiable"]:
                if field == "unit_price":
                    continue
                declared = case.get("term_options", {}).get(field)
                if not isinstance(declared, list) or not declared:
                    return None
                choices = []
                for value in [*declared, *([std[field]] if field in std else [])]:
                    if not valid_value(field, value):
                        return None
                    if value not in choices:
                        choices.append(value)
                options.append(choices)
            combinations += math.prod(map(len, options))
            if combinations > MAX_SCRIPT_CANDIDATES:
                return None
            record["options"] = options
        tried = all(record["sent"] or record["terminal"] for record in records)
        if tried and any(latest(threads[record["sid"]]).get("status") == "agreed"
                         and self.validate(case, threads, {"supplier_id": record["sid"], "action": "accept"})
                         for record in records):
            return "agreed_package", closing_move(case, threads)
        if all(record["terminal"] or record["plateau"] for record in records):
            return "quote_plateau", closing_move(case, threads)
        proposals = []
        for record in records:
            sid, current, sent = record["sid"], record["current"], record["sent"]
            if record["terminal"] or record["plateau"]:
                continue
            current_cost = decision_cost(case, current, record["std"], sid)
            target_cost = ask_limit(case, threads[sid], sid)
            if sent:
                prior = [sheet["terms"] for sheet in sent[-1]["term_sheets"]]
                if any(set(sheet) != set(case["negotiable"]) or any(not valid_value(f, v) for f, v in sheet.items())
                       for sheet in prior):
                    return None
                previous_cost = min(decision_cost(case, sheet, record["std"], sid) for sheet in prior)
                small_gap = current_cost <= previous_cost * 1.002
                if tried and (small_gap or moves_left(case, threads[sid]) <= 1):
                    if self.validate(case, threads, {"supplier_id": sid, "action": "accept"}):
                        return "small_cost_gap" if small_gap else "round_reserve", closing_move(case, threads)
            anchor = ANCHOR_BY_GOAL.get(case["buyer"].get("category_goal"), 0.15)
            rows = self._packages(case, record["options"], target_cost, alternative, current["unit_price"],
                                  price_ceiling=math.inf if sent else max(
                                      round(current["unit_price"] * (1 - anchor), 2),
                                      opening_price_cap(case, threads[sid], sid)),
                                  sid=sid, standard=record["std"])
            if not rows:
                continue
            chosen = [rows[0][1]]
            baseline = next((terms for _, terms in rows if all(
                terms[field] == current[field] for field in case["negotiable"] if field != "unit_price"
            )), None)
            if baseline and baseline not in chosen:
                chosen.append(baseline)
            for _, terms in rows:
                if len(chosen) == 3:
                    break
                if terms not in chosen:
                    chosen.append(terms)
            rival = self._rival_evidence(case, threads, sid)
            move = self.validate(case, threads, {"supplier_id": sid, "action": "send",
                "body": ("Please consider these complete packages for the stated requirement. "
                         + self._forecast_evidence(case, threads) + " " + rival).strip(),
                "term_sheets": chosen})
            if move:
                reason = "bounded_concession" if sent else "enumerated_opening"
                if not must_haves_ok(case, current, record["std"]):
                    reason = "must_have_repair"
                elif len(records) > 1:
                    reason = "rfq_package_comparison"
                proposals.append((len(sent), rows[0][0], sid, reason, move))
        if proposals:
            best = min(proposals, key=lambda row: row[:3])
            return best[3], best[4]
        return None

    def _script(self, case, threads):
        live = live_threads(threads)
        if all(latest(threads[s]).get("status") in {"agreed", "final_offer"}
               or moves_left(case, threads[s]) <= 0 for s in live):
            return "terminal_offers", closing_move(case, threads)
        for sid in live:
            _, _, _, missing = known_offer(case, threads[sid])
            already_asked = any(m.get("from") == "buyer" and m.get("body", "").startswith("Please confirm these terms:")
                                for m in threads[sid])
            if missing and not already_asked and moves_left(case, threads[sid]) > 1:
                d = {"supplier_id": sid, "action": "send",
                     "body": f"Please confirm these terms: {', '.join(missing)}. Please provide a complete term sheet."}
                if move := self.validate(case, threads, d):
                    return "missing_quote_terms", move
        if self._semantic_blocker(case, threads):
            return None
        if (len(live) == 1 and set(case["negotiable"]) <= {"unit_price", "payment_days"}
                and case["buyer"].get("category_goal", "routine") in {"routine", "leverage"}
                and (case["buyer"].get("alternative") or {}).get("kind") in (None, "none")):
            sid = live[0]
            _, terms, std, missing = known_offer(case, threads[sid])
            if not missing and not any(m.get("from") == "buyer" for m in threads[sid]):
                anchor = ANCHOR_BY_GOAL.get(case["buyer"].get("category_goal"), 0.15)
                proposal = {**std, **terms, "unit_price": round(terms["unit_price"] * (1 - anchor), 2)}
                d = {"supplier_id": sid, "action": "send", "body": "Thank you. Please consider this price proposal.",
                     "term_sheets": [complete_offer(case, proposal, std)]}
                move = self.validate(case, threads, d)
                if move and (decision_cost(case, move["message"]["term_sheets"][0]["terms"], std, sid)
                             < decision_cost(case, terms, std, sid)):
                    return "simple_opening_counter", move
        return self._structured_bargain(case, threads)

    def decide(self, case, threads):
        return self.decide_timed(case, threads, deadline=self.clock() + self.max_decision_seconds)

    def decide_timed(self, case, threads, *, deadline, wave_seconds_left=None):
        self._prepare(case)
        start = self.clock()
        if not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        # The model budget may expire long before the thread's own deadline; scripts still fit there.
        thread_deadline, deadline = deadline, min(deadline, start + self.max_decision_seconds)
        if deadline <= start or (wave_seconds_left is not None and wave_seconds_left <= CLOSE_BEFORE_CAP_S):
            move = closing_move(case, threads)
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
            move = closing_move(case, threads)
            action = move["message"].get("action", {}).get("type")
            # Accepting an open_offer opening after a failed model call scored 0 in hosted P01.
            # Accepting an offer the supplier still treats as open, with moves left, after a model failure
            # (hosted P01 accepted 135.7 for 0.41 after a round-2 timeout; another run reached 0.937).
            open_offer = action == "accept" and latest(threads[move["supplier_id"]]).get("status") == "open"
            # Walking away with moves left forfeits the case (hosted P10 walked in round 1): counter instead.
            if (self.clock() < thread_deadline - 1 and (open_offer or action == "walk_away")
                    and any(moves_left(case, threads[sid]) > 1 for sid in live_threads(threads))):
                repair = self._structured_bargain(case, threads)
                if repair and not repair[1]["message"].get("action"):
                    move = repair[1]
            self._record(case, route, type(error).__name__, start, deadline, fallback=True)
            self.last_decision["error"] = str(error)[:300]
            return move
