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
    HOLD_STEP,
    acceptable_offer,
    ask_limit,
    closing_move,
    known_offer,
    latest,
    live_threads,
    moves_left,
    opening_price_cap,
    price_within,
    standing,
)
from .llm import ID_RE, SYSTEM, LLMAgent, parse

log = logging.getLogger("arena")
MAX_SCRIPT_CANDIDATES = 256
# shortcut: conservative phrase matching; use structured side-condition extraction for broader prose coverage.
CONDITIONAL_WORDING = ("subject to", "provided that", "only if", "pending approval", "conditional on", "unless",
                       "please clarify", "please explain", "capacity constraint", "of our cost", "whole range",
                       "faster payment", "alternatively", "no-go")
# Terms that move cash or effort to the supplier (`cost.supplier_value`): it prices each at its own
# value, often far above ours. Contract length, volume and index clauses are security instead.
SUPPLIER_CASH_TERMS = {"payment_days", "chargeback_eur_per_complaint", "ppm_commitment", "lead_time_days",
                       "tooling_eur", "incoterm"}


class RoutedAgent:
    """Use a tested script, one fast completion, or an optional budget-qualified strong completion."""

    # Opus at high effort timed out at 25 s on first messages; play() still caps every decision at
    # the supplier's 60 s message limit minus a send margin.
    def __init__(self, fast, strong=None, package=None, *, max_decision_seconds=40,
                 strong_min_seconds=20, clock=time.monotonic):
        if not (math.isfinite(max_decision_seconds) and max_decision_seconds > 0
                and math.isfinite(strong_min_seconds) and strong_min_seconds > 0):
            raise ValueError("decision and strong-model budgets must be finite and positive")
        self.fast, self.strong, self.clock = fast, strong, clock
        self.max_decision_seconds, self.strong_min_seconds = max_decision_seconds, strong_min_seconds
        self.context = LLMAgent(None, package)
        self.last_decision = {}
        self.unavailable = set()  # suppliers whose reply clock runs out before a message could arrive

    def validate(self, case, threads, decision, *, reprice=True):
        """One gate for scripts, model proposals and fallback. Return a wire move or None.
        reprice=False keeps a sheet's price (a supplier's own quoted package sent back unchanged)."""
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
            if action != "send" or moves_left(case, events) <= 0 or sid in self.unavailable:
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
            # The shared concession schedule binds scripts and models: a sheet above the ask limit (or an
            # opening above the anchored price) is repriced to it, keeping the chosen terms. Rejecting
            # them sent 8 of 10 hosted v4 openings to the fallback.
            limit = ask_limit(case, events, sid) if reprice else math.inf
            opening = reprice and not any(e.get("from") == "buyer" and e.get("term_sheets") for e in events)
            cap = opening_price_cap(case, events, sid) if opening else math.inf
            conformed = []
            for t in clean:
                ceiling = max(cap, cheapest_price(case, t, std, sid) or 0) if opening else math.inf
                if total_cost(case, t, std) > limit * 1.005 + 0.01 or t["unit_price"] > ceiling + 0.02:
                    t = {**t, "unit_price": self._price_within(case, t, std, sid, limit, ceiling)}
                if t not in conformed:
                    conformed.append(t)
            clean = conformed
            if clean:  # the supplier picks the sheet dearest for us (hosted P03): keep equal-cost sheets only
                cheapest = min(decision_cost(case, t, std, sid) for t in clean)
                clean = [t for t in clean if decision_cost(case, t, std, sid) <= cheapest * 1.003 + 0.01]
                if reprice:  # a mirrored supplier package goes back alone
                    clean += self._toward_sheets(case, events, sid, clean, 3 - len(clean), limit)
                # Equal cost to us is not credibility to the supplier: hosted P18 v8 got a 2.78 sheet (a 12-month
                # term priced against uncovered demand) next to 4.57 and countered 6.00 instead of 5.40.
                top = max(t["unit_price"] for t in clean)
                clean = [t for t in clean if t["unit_price"] >= 0.9 * top] or clean[:1]
            move = {"supplier_id": sid, "message": {
                "subject": subject, "body": body, "term_sheets": [{"terms": t} for t in clean]}}
            return move if len(json.dumps({"message": move["message"]}).encode()) <= 64_000 else None
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            return None

    def _toward_sheets(self, case, events, sid, sheets, room, limit=math.inf):
        """Up to `room` equal-cost variants of our cheapest sheet, each moving one term to the option
        nearest the supplier's own quote. The supplier answers the sheet it prefers, so a trade it values
        above our cost gets priced; equal cost keeps any accepted sheet as good for us. Hosted P10 models
        sent one 42-day sheet and got a 33.8 final offer (0.555); sheets including the supplier's 56 days
        drew 31.6 (0.671) although the 14 late days cost EUR 33,600. A variant whose equal-cost price
        falls below its health floor is offered at the floor if that costs at most one held step
        (`HOLD_STEP` of the gap to the supplier's standing offer) more and the ask limit allows it."""
        if room <= 0 or not sheets:
            return []
        _, terms, std, missing = known_offer(case, events)
        if missing:
            return []
        position = {**std, **case.get("fixed_terms", {}), **terms}
        base = min(sheets, key=lambda t: decision_cost(case, t, std, sid))
        cost, cash = decision_cost(case, base, std, sid), total_cost(case, base, std)
        held = cost + HOLD_STEP * max(0.0, decision_cost(case, terms, std, sid) - cost)
        out = []
        for field in case["negotiable"]:
            target = self._toward(case, field, position.get(field), std)
            if len(out) >= room or field == "unit_price" or target is None or base[field] == target:
                continue
            variant = {**base, field: target}
            if not must_haves_ok(case, variant, std):
                continue
            variant["unit_price"] = price_within(case, variant, std, sid, cash)
            equal = (decision_cost(case, variant, std, sid) <= cost * 1.003 + 0.01
                     and total_cost(case, variant, std) <= cash + 0.01)
            at_floor = variant["unit_price"] <= (cheapest_price(case, variant, std, sid) or 0) + 0.01
            near = (at_floor and decision_cost(case, variant, std, sid) <= held
                    and total_cost(case, variant, std) <= limit)
            if (equal or near) and variant not in sheets + out:
                out.append(variant)
        return out

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

    @staticmethod
    def _price_within(case, sheet, std, sid, limit, cap=math.inf):
        return price_within(case, sheet, std, sid, limit, cap)

    @staticmethod
    def _premature(case, threads, sid):
        """An open offer, with moves left, still above our last ask (both top teams accepted only final
        offers and agreements; hosted P01/P02 accepted open offers 3 % above our ask in round 2)."""
        events = threads[sid]
        if latest(events).get("status") != "open" or moves_left(case, events) <= 3:
            return False
        counters = [standing(events[:i + 1])[1].get("unit_price") for i, e in enumerate(events)
                    if e.get("from") != "buyer" and e.get("term_sheets")]
        if len(counters) >= 3 and counters[-1] == counters[-2]:
            return False  # a stalled supplier: further counters only risk a walk-away (hosted P02 v4/v5)
        accepted = acceptable_offer(case, events, sid)
        sent = [e for e in events if e.get("from") == "buyer" and e.get("term_sheets")]
        if not accepted or not sent:
            return bool(accepted)
        _, _, std, _ = known_offer(case, events)
        ours = min(decision_cost(case, s.get("terms", s), std, sid) for s in sent[-1]["term_sheets"])
        return accepted[1] > ours * 1.005

    def _limit_counter(self, case, threads, sid):
        """One sheet on the supplier's standing terms at our current ask limit (never below the
        health floor): the fallback counter when no enumerated package applies."""
        events = threads[sid]
        _, terms, std, missing = known_offer(case, events)
        limit = ask_limit(case, events, sid)
        if missing or not math.isfinite(limit):
            return None
        resolved = {**std, **case.get("fixed_terms", {}), **terms}
        sheet = {f: resolved[f] for f in case["negotiable"]}
        price = self._price_within(case, sheet, std, sid, limit)
        if price >= sheet["unit_price"]:
            return None
        return self.validate(case, threads, {"supplier_id": sid, "action": "send",
                                             "body": "Thank you. Please consider this complete package.",
                                             "term_sheets": [{**sheet, "unit_price": price}]})

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

    @staticmethod
    def _toward(case, field, position, standard=None):
        """The option of `field` nearest the supplier's own position (its quoted value), or None. Its
        own standard value is legal even outside the options."""
        options = case.get("term_options", {}).get(field) or []
        if position in options or (standard and field in standard and position == standard[field]):
            return position
        numeric = [o for o in options if type(o) in (int, float)]
        if type(position) in (int, float) and numeric:
            return min(numeric, key=lambda o: (abs(o - position), -o))
        return None

    def _distance(self, case, proposal, position, standard=None):
        """How far a package's terms sit from the supplier's position, in option steps."""
        total = 0
        for field, value in proposal.items():
            options = case.get("term_options", {}).get(field) or []
            target = self._toward(case, field, (position or {}).get(field), standard)
            if field == "unit_price" or target is None or value == target:
                continue
            total += abs(options.index(value) - options.index(target)) if value in options and target in options else 1
        return total

    def _packages(self, case, options, target_cost, alternative, reference_price, *, price_ceiling=math.inf,
                  sid=None, standard=None, position=None):
        """Price each trade under the same cash-cost ceiling (`ask_limit`), including the outside option.

        With the supplier's `position`, an anchored opening price binds as a cost among packages that
        commit the same quantity: the cheapest at the anchor sets it and a fixed-cost trade carries the
        price of equal cost. The same price on every sheet made a slower lead time look dear to us, so
        it was never offered (hosted P10: 33.8 at 42 days scored 0.555, the supplier's 31.6 at 56 days
        0.671 although 14 late days cost EUR 33,600). Equal-cost packages rank by closeness to the
        supplier's own terms, which cost it least to grant."""
        fields = [field for field in case["negotiable"] if field != "unit_price"]
        candidates = []
        ceiling = min(target_cost, alternative - 0.01)
        for values in itertools.product(*options):
            proposal = dict(zip(fields, values, strict=True))
            # Routine/leverage C is affine in price for fixed terms; the reference price limits rounding error.
            base = total_cost(case, {**proposal, "unit_price": 0})
            reference_cost = total_cost(case, {**proposal, "unit_price": reference_price})
            slope = (reference_cost - base) / reference_price
            if not math.isfinite(slope) or slope <= 0:
                continue
            floor = cheapest_price(case, {**proposal, "unit_price": reference_price}, standard, sid)
            candidates.append((proposal, base, slope, floor or 0.0))
        anchors = {}  # per committed quantity (C's slope in price): only fixed-cost trades shift the price
        if math.isfinite(price_ceiling) and position is not None:
            for _, base, slope, floor in candidates:
                key = round(slope, 4)
                anchors[key] = min(anchors.get(key, math.inf), base + slope * max(price_ceiling, floor))
        rows = []
        for proposal, base, slope, floor in candidates:
            cap = (anchors[round(slope, 4)] - base) / slope if anchors else price_ceiling
            price = min(cap, (ceiling - base) / slope)
            proposal["unit_price"] = math.floor(price * 100) / 100
            if proposal["unit_price"] <= 0:
                continue
            if floor and proposal["unit_price"] < floor:  # never squeeze into the health penalty
                proposal["unit_price"] = floor
            cash = total_cost(case, proposal, standard)
            if cash > ceiling:
                proposal["unit_price"] = round(proposal["unit_price"] - 0.01, 2)
                cash = total_cost(case, proposal, standard)
            cost = decision_cost(case, proposal, standard, sid)
            if proposal["unit_price"] <= 0 or not math.isfinite(cost) or cash > ceiling or cost >= alternative:
                continue
            if must_haves_ok(case, proposal):
                rows.append((cost, proposal))
        return sorted(rows, key=lambda row: (
            round(decision_cost(case, {**row[1], "unit_price": reference_price}, standard, sid), 2),
            self._distance(case, row[1], position, standard)))

    def _opening_sheets(self, case, record, rows, alternative, cap):
        """Open on the supplier's own non-price terms - only must-have fields moved, to the nearest
        allowed value - at the anchored price, plus trades at exactly equal cost to us.

        At one capped price the package cheapest for us always carried every buyer-favourable term the
        supplier had not offered. Suppliers price such terms at their own value, not ours: hosted P11
        answered a EUR 500 chargeback (worth EUR 0.68/unit to us) by raising 47.00 to 49.50-50.00 and
        held that package to a 48.50 final (0.23; runs that dropped it closed at 46.00 for 0.63).
        Unrequested terms stay available later as equal-cost trades. Relationship goals keep the
        health-floor pricing of every package: there the floor, not the anchor, sets the price."""
        if case["buyer"].get("category_goal") in RELATIONSHIP_GOALS:
            return None
        current, sid, std = record["current"], record["sid"], record["std"]
        fields = [field for field in case["negotiable"] if field != "unit_price"]
        required = {rule["field"] for rule in case["buyer"].get("must_haves", [])}

        def numeric(value):
            return isinstance(value, (int, float)) and not isinstance(value, bool)

        def distance(terms):
            total = 0.0
            for field in fields:
                ours, theirs = terms[field], current[field]
                if ours == theirs or field not in SUPPLIER_CASH_TERMS:
                    continue
                if field not in required:
                    return None
                total += (abs(ours - theirs) / max(abs(theirs), 1)) if numeric(ours) and numeric(theirs) else 1.0
            return total

        candidates = [(gap, index) for index, (_, terms) in enumerate(rows) if (gap := distance(terms)) is not None]
        if not candidates:
            return None
        base = rows[min(candidates)[1]][1]
        base_cost = decision_cost(case, base, std, sid)
        equal = self._packages(case, record["options"], base_cost, alternative, current["unit_price"],
                               price_ceiling=cap, sid=sid, standard=std)
        chosen = [base]
        for cost, terms in equal:
            if len(chosen) == 3:
                break
            same_terms = all(terms[field] == base[field] for field in fields)
            if not same_terms and cost >= base_cost * 0.997 - 0.01:
                chosen.append(terms)
        return chosen

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
            cap = math.inf if sent else max(round(current["unit_price"] * (1 - anchor), 2),
                                            opening_price_cap(case, threads[sid], sid))
            rows = self._packages(case, record["options"], target_cost, alternative, current["unit_price"],
                                  price_ceiling=cap, sid=sid, standard=record["std"], position=current)
            if not rows:
                continue
            opening = None
            if not sent and case["buyer"].get("category_goal") not in RELATIONSHIP_GOALS:
                # the supplier's own terms open at the anchored price itself, not at an equal-cost one
                plain = self._packages(case, record["options"], target_cost, alternative, current["unit_price"],
                                       price_ceiling=cap, sid=sid, standard=record["std"])
                opening = self._opening_sheets(case, record, plain, alternative, cap) if plain else None
            if opening:
                chosen = opening
            else:
                chosen = [rows[0][1]]
                baseline = next((terms for _, terms in rows if all(
                    terms[field] == current[field] for field in case["negotiable"] if field != "unit_price"
                )), None)
                if baseline and baseline not in chosen:
                    chosen.append(baseline)
                # One-field moves toward the supplier's own terms at equal cost (validate keeps only those):
                # the supplier picks the sheet it prefers, so a trade it values above our cost gets priced.
                for field in case["negotiable"]:
                    target = self._toward(case, field, current.get(field), record["std"])
                    if field == "unit_price" or target is None or rows[0][1][field] == target:
                        continue
                    variant = next((terms for cost, terms in rows if cost <= rows[0][0] * 1.003 + 0.01 and all(
                        terms[f] == (target if f == field else rows[0][1][f])
                        for f in case["negotiable"] if f != "unit_price")), None)
                    if variant and variant not in chosen and len(chosen) < 3:
                        chosen.append(variant)
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

    def _mirror_alternative(self, case, threads):
        """Send back, unchanged, a cheaper alternative sheet from a supplier's final offer or agreement.

        Only the standing (first) sheet can be accepted, and countering a final offer made hosted
        suppliers walk away. Its own alternative is a package it already offered, so mirroring it is
        no counter: earlier teams saw it become the agreed sheet. Hosted final offers carried cheaper
        alternatives in P10 (31.70 vs 33.60, about +0.10 score), P18 (5.30 vs 5.40, +0.03) and P08."""
        best = None
        for sid in live_threads(threads):
            if accepted := acceptable_offer(case, threads[sid], sid):
                best = min(best or accepted[1], accepted[1])
        if best is None:
            return None
        candidates = []
        for sid in live_threads(threads):
            events = threads[sid]
            reply = latest(events)
            if (reply.get("status") not in {"agreed", "final_offer"} or moves_left(case, events) < 2
                    or sid in self.unavailable):
                continue
            sent = [s.get("terms", s) for e in events if e.get("from") == "buyer" for s in e.get("term_sheets") or []]
            for sheet in (reply.get("term_sheets") or [])[1:]:
                if not isinstance(sheet, dict) or not isinstance(sheet.get("terms"), dict):
                    continue
                accepted = acceptable_offer(case, [*events[:-1], {**reply, "term_sheets": [sheet]}], sid)
                if not accepted or accepted[1] >= best * 0.998:
                    continue
                move = self.validate(case, threads, {
                    "supplier_id": sid, "action": "send",
                    "body": "Thank you. We accept your alternative package as quoted and attach it unchanged; "
                            "please confirm it.",
                    "term_sheets": [sheet["terms"]]}, reprice=False)
                if not move:
                    continue
                terms = move["message"]["term_sheets"][0]["terms"]
                _, _, std, _ = known_offer(case, events)
                if terms in sent or decision_cost(case, terms, std, sid) > accepted[1] + 0.01:
                    continue  # already sent once, or completing it changed the package
                candidates.append((accepted[1], sid, move))
        return min(candidates, key=lambda row: row[:2])[2] if candidates else None

    def _script(self, case, threads):
        live = live_threads(threads)
        if all(latest(threads[s]).get("status") in {"agreed", "final_offer"}
               or moves_left(case, threads[s]) <= 0 for s in live):
            if mirror := self._mirror_alternative(case, threads):
                return "mirror_alternative", mirror
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
            if move["message"].get("action", {}).get("type") == "accept" and self._premature(
                    case, threads, move["supplier_id"]):
                raise ValueError("model accepted an open offer above our last ask")
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
                elif open_offer and (counter := self._limit_counter(case, threads, move["supplier_id"])):
                    move = counter
            self._record(case, route, type(error).__name__, start, deadline, fallback=True)
            self.last_decision["error"] = str(error)[:300]
            return move
