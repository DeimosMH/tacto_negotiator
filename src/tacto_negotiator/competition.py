"""Paired local buyer competitions with private synthetic supplier economics.

This tests policy mechanics, not the hosted arena's supplier intelligence. Factories supply
fresh agents; this module neither reads credentials nor creates model clients. Agent decisions
use real monotonic deadlines. Supplier delays advance a private virtual clock without sleeping.
"""

import copy
import itertools
import math
import random
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .cost import closing_terms, cost_breakdown, must_haves_ok, score, total_cost
from .docs import read_document
from .play import CLOSE_BEFORE_CAP_S, MOVE_LIMIT_S, SEND_MARGIN_S, closing_move, latest, live_threads, standing
from .sim import Fail, Sim


@dataclass(frozen=True)
class Scenario:
    """Only case is buyer-visible; profiles are private simulator/evaluation inputs."""

    name: str
    case: dict
    profiles: dict
    seed: int
    holdout: bool = False


def build_scenarios(seed=0, *, holdout=False):
    """Stable public cases with varied private floors, concession rates and trade premiums."""
    rng = random.Random(f"{seed}:{holdout}")
    price = round(rng.uniform(35, 55) if holdout else rng.uniform(38, 44), 2)
    base = {
        "case_id": "case:template", "set": "practice", "type": "renewal", "title": "Synthetic component renewal",
        "briefing": "Cover the stated need using permitted terms and respect every must-have.",
        "trigger_doc": None, "parts": ["part:component"], "suppliers": ["sup:A"],
        "negotiable": ["unit_price", "payment_days"], "term_options": {"payment_days": [14, 30, 60]},
        "fixed_terms": {"incoterm": "DAP", "lead_time_days": 30, "volume_per_year": 1000,
                        "contract_months": 12, "tooling_eur": 0, "index_clause": None},
        "buyer": {"need_per_year": 1000, "horizon_years": 1, "current_unit_price": price,
                  "fallback_unit_price": round(price * 1.4, 2), "interest_rate": 0.08,
                  "reference_payment_days": 60, "need_lead_time_days": 35, "category_goal": "routine",
                  "fallback_risk_pct": 3, "index_risk_pct": 2, "late_cost_per_day": 80,
                  "cost_per_defect": 0, "freight_eur_per_unit": {"EXW": 2.0, "FCA": 1.0, "DAP": 0.0},
                  "index_paths": {}, "must_haves": [], "alternative": {"kind": "none", "terms": {}}},
        "round_limit": 10, "refs": [], "facts": {},
    }
    cases = {}
    names = ("simple_price", "payment_freight", "leadtime_repair", "competing_rfq", "cheaper_alternative",
             "price_plateau", "incomplete_defaults", "contradicted_claims", "relationship_risk", "slow_reply")
    for index, name in enumerate(names, 1):
        case = copy.deepcopy(base)
        case.update(case_id=f"case:C{index:02d}", title="Component procurement")
        cases[name] = case
    simple = cases["simple_price"]
    simple["negotiable"], simple["term_options"] = ["unit_price"], {}
    simple["fixed_terms"]["payment_days"] = 30
    trade = cases["payment_freight"]
    trade["negotiable"].append("incoterm")
    trade["term_options"]["incoterm"] = ["EXW", "FCA", "DAP"]
    del trade["fixed_terms"]["incoterm"]
    lead = cases["leadtime_repair"]
    lead["negotiable"].append("lead_time_days")
    lead["term_options"]["lead_time_days"] = [45, 30, 14]
    del lead["fixed_terms"]["lead_time_days"]
    lead["buyer"]["must_haves"] = [{"field": "lead_time_days", "op": "<=", "value": 30}]
    rfq = cases["competing_rfq"]
    rfq.update(type="rfq", suppliers=["sup:A", "sup:B", "sup:C"])
    alternative = cases["cheaper_alternative"]
    alternative["buyer"]["alternative"] = {
        "kind": "quote", "ref": "quote:alternative",
        "terms": {**base["fixed_terms"], "unit_price": round(price * 0.76, 2), "payment_days": 60},
    }
    risk = cases["relationship_risk"]
    risk["buyer"].update(category_goal="stabilise", sustainable_margin_pct=8, relationship_eur_per_point=900)
    risk["briefing"] += " Supply continuity matters; a low unit price may hide relationship cost."
    cases["slow_reply"].update(type="rfq", suppliers=["sup:A", "sup:B", "sup:C"])
    out = []
    for name, case in cases.items():
        profiles = {}
        for i, sid in enumerate(case["suppliers"]):
            standard = {field: case["term_options"][field][0] for field in case["negotiable"][1:]}
            profiles[sid] = {
                "floor": round(price * rng.uniform(0.88, 1.02) * (1 - 0.025 * i), 2),
                "step": rng.choice([0.008, 0.025, 0.045]), "plateau": 0.0,
                "standard": standard, "payment_premium": 0.025 if holdout else 0.018,
                "freight_premium": {"EXW": 0, "FCA": 0.8, "DAP": 1.6},
                "lead_premium": {45: 0, 30: 0.8, 14: 2.5},
                "reply_delay_s": 0.0, "missing": None, "contradicted": False,
                "full_cost": round(price * 0.96, 2), "interest_rate": 0.12,
            }
        if name == "price_plateau":
            profiles["sup:A"].update(floor=round(price * 1.04, 2), step=0.0, plateau=1.0)
        if name == "leadtime_repair":
            # Keep the repair opportunity and provide an eligible opening for score normalization.
            case["suppliers"].append("sup:B")
            profiles["sup:B"] = copy.deepcopy(profiles["sup:A"])
            profiles["sup:B"]["standard"]["lead_time_days"] = 30
        if name == "incomplete_defaults":
            profiles["sup:A"]["missing"] = "payment_days"
        if name == "contradicted_claims":
            profiles["sup:A"]["contradicted"] = True
            case["briefing"] += " The supplier disputed the period used in an earlier index comparison."
        if name == "relationship_risk":
            profiles["sup:A"]["floor"] = round(price * 0.85, 2)
        if name == "slow_reply":
            for profile in profiles.values():
                profile.update(floor=round(price * 1.07, 2), step=0.005,
                               reply_delay_s=60.0 if holdout else 55.0)
        out.append(Scenario(name, case, profiles, seed, holdout))
    return out


def _floor(case, profile, terms):
    """Private reservation price includes the supplier's cost of the offered trade."""
    terms = closing_terms(case, terms, profile["standard"])
    value = profile["floor"]
    if "payment_days" in case["negotiable"]:
        value += max(0, terms["payment_days"] - 14) * profile["payment_premium"]
    if "incoterm" in case["negotiable"]:
        value += profile["freight_premium"][terms["incoterm"]]
    if "lead_time_days" in case["negotiable"]:
        value += profile["lead_premium"][terms["lead_time_days"]]
    return math.ceil(value * 100 - 1e-8) / 100


def _supplier(case, profile):
    return {"standard": {**profile["standard"], **case["fixed_terms"]},
            "full_cost": profile["full_cost"], "interest_rate": profile["interest_rate"]}


def _outside_cost(case):
    """Only a declared alternative is an attainable no-deal outcome."""
    alternative = case["buyer"].get("alternative") or {}
    if alternative.get("kind") in (None, "none"):
        return None
    terms = alternative.get("terms", {})
    cost = total_cost(case, terms)
    if not math.isfinite(cost) or not must_haves_ok(case, terms):
        raise ValueError("scenario alternative must have a finite cost and satisfy must-haves")
    return cost


def feasible_reference(scenario):
    """Best enumerated feasible deal or outside option; not a global-optimality claim.

    For relationship cases, include a finite ten-cent price grid above the reservation price
    because reducing price can increase relationship cost. Every candidate can be accepted.
    """
    case = scenario.case
    outside = _outside_cost(case)
    best = (outside if outside is not None else math.inf, None, None)
    fields = case["negotiable"][1:]
    for sid, profile in scenario.profiles.items():
        choices = []
        for field in fields:
            options = list(case["term_options"][field])
            if field in profile["standard"] and profile["standard"][field] not in options:
                options.append(profile["standard"][field])
            choices.append(options)
        for combination in itertools.product(*choices):
            terms = dict(zip(fields, combination, strict=True))
            floor = _floor(case, profile, terms)
            prices = [floor]
            if case["buyer"]["category_goal"] in {"stabilise", "secure_supply"}:
                prices += [round(floor + i / 10, 2) for i in range(1, 201)]
            for price in prices:
                candidate = {**terms, "unit_price": price}
                if must_haves_ok(case, candidate, profile["standard"]):
                    cost = total_cost(case, candidate, profile["standard"], _supplier(case, profile))
                    if cost < best[0]:
                        best = (cost, sid, candidate)
    if not math.isfinite(best[0]):
        raise ValueError("scenario has no feasible deal or declared alternative")
    return {"cost": best[0], "supplier_id": best[1], "terms": best[2],
            "kind": "best enumerated feasible deal or outside option"}


class CompetitionSim(Sim):
    """Vary acceptance thresholds, trade costs, concession paths and reply delays privately."""

    def __init__(self, scenario, waves):
        self.scenario = copy.deepcopy(scenario)
        self.now, self.supplier_delay_s = 1_000_000.0, 0.0
        self.reference = feasible_reference(scenario)
        super().__init__({scenario.case["case_id"]: copy.deepcopy(scenario.case)}, clock=lambda: self.now)
        self.rehearse(wait=0, cap=480, brk=0, waves=waves)

    def open(self, team, case_id):
        opened = super().open(team, case_id)
        state = self.sessions[opened["session_id"]]
        state["files"].clear()
        for sid, thread in state["threads"].items():
            profile = self.scenario.profiles[sid]
            thread["standard"] = dict(profile["standard"])
            if profile["missing"]:
                thread["standard"].pop(profile["missing"], None)
            thread["messages"].clear()
            stated = {"unit_price": thread["opening"]}
            text = "Our quote lists the stated price and known standard terms."
            if profile["contradicted"]:
                text += " We dispute the period in the previous index comparison; explain it before using that claim."
            message = self._mail(state, thread, "open", stated, text, ["quote.pdf"])
            if profile["contradicted"]:
                message["claim_results"] = [{"index": 0, "result": "contradicted"}]
        opened["threads"] = {sid: list(th["messages"]) for sid, th in state["threads"].items()}
        state["eligible_opening_cost"] = min((
            total_cost(state["case"], {"unit_price": thread["opening"]}, self.scenario.profiles[sid]["standard"],
                       _supplier(state["case"], self.scenario.profiles[sid]))
            for sid, thread in state["threads"].items()
            if must_haves_ok(state["case"], {"unit_price": thread["opening"]},
                             self.scenario.profiles[sid]["standard"])
        ), default=None)
        if state["eligible_opening_cost"] is None:
            raise ValueError("scenario has no eligible opening quote; arena score is undefined")
        return copy.deepcopy(opened)

    def _answer(self, state, thread, message, sheets):
        sid = next(sid for sid, candidate in state["threads"].items() if candidate is thread)
        profile, case = self.scenario.profiles[sid], state["case"]
        accepted = [terms for terms in sheets if terms["unit_price"] >= _floor(case, profile, terms)]
        if accepted:
            terms = max(accepted, key=lambda t: t["unit_price"] - _floor(case, profile, t))
            reply = self._mail(state, thread, "agreed", terms, "We agree to this complete package.", ["quote.pdf"])
        elif thread["status"] == "final_offer" and sheets:
            thread["closed"] = "walked_away"
            reply = self._mail(state, thread, "walked_away", thread["stated"], "We cannot improve further.")
        elif not sheets and profile["missing"] and profile["missing"] not in thread["standard"]:
            thread["standard"] = dict(profile["standard"])
            reply = self._mail(state, thread, "open", thread["stated"], "The requested default is in this quote.",
                               ["quote.pdf"])
        else:
            floor = _floor(case, profile, {**thread["standard"], **thread["stated"]})
            price = max(floor, thread["opening"] * profile["plateau"],
                        thread["stated"]["unit_price"] - thread["opening"] * profile["step"])
            terms = {**thread["stated"], "unit_price": round(price, 2)}
            status = "final_offer" if thread["round"] >= 4 else "open"
            reply = self._mail(state, thread, status, terms, "This is our current commercial position.", ["quote.pdf"])
        if profile["contradicted"]:
            reply["claim_results"] = [{"index": 0, "result": "contradicted"}]
        delay = profile["reply_delay_s"]
        self.now += delay
        self.supplier_delay_s += delay
        thread["last_at"] = self.now  # only this supplier's processing time is exempt
        self._capped(state)
        return reply

    def _check(self, case, terms, thread):
        if not isinstance(terms, dict) or any(type(v) in (int, float) and not math.isfinite(v)
                                              for v in terms.values()):
            raise Fail(422, "finite term values are required")
        return super()._check(case, terms, thread)

    def message(self, team, session_id, sid, message):
        action = message.get("action") or {}
        if action.get("type") == "accept":
            thread = self.sessions[session_id]["threads"].get(sid)
            if thread is None or action.get("term_sheet_id") != thread["offer_id"]:
                raise Fail(422, "acceptance must name this supplier's current offer")
        return super().message(team, session_id, sid, message)

    def _end(self, state, reason, sid):
        super()._end(state, reason, sid)
        case = state["case"]
        opening = state["eligible_opening_cost"]
        result = state["result"]
        realized = _outside_cost(case)
        if sid:
            profile = self.scenario.profiles[sid]
            terms = closing_terms(case, state["threads"][sid]["stated"], profile["standard"])
            realized = total_cost(case, terms, supplier=_supplier(case, profile))
            result.update(terms=terms, c_deal=realized, must_haves_ok=must_haves_ok(case, terms),
                          cost_breakdown=cost_breakdown(case, terms, supplier=_supplier(case, profile)))
        valid = not sid or result["must_haves_ok"]
        result.update(c_open=opening, c_alt=_outside_cost(case), c_best=self.reference["cost"], realized_cost=realized,
                      score=score(opening, realized, self.reference["cost"]) if valid else 0.0)


def _public_threads(sim, session, cache):
    """A deep copy prevents agents from observing or mutating private simulator state."""
    threads = {}
    for sid, thread in session["threads"].items():
        messages = copy.deepcopy(thread["messages"])
        if thread["closed"]:
            messages.append({"from": "arena", "status": "walked_away", "term_sheets": [], "attachments": []})
        for message in messages:
            for attachment in message.get("attachments", []):
                key = attachment["attachment_id"]
                if key not in cache:
                    content, _ = sim.attachment("local", session["id"], key)
                    cache[key] = read_document(attachment["name"], content)
                attachment["text"] = cache[key]
        threads[sid] = messages
    return threads


def _count_completions(agent, counts):
    for name in ("complete", "fast", "strong"):
        complete = getattr(agent, name, None)
        if not callable(complete):
            continue

        def counted(*args, _complete=complete, **kwargs):
            counts["attempts"] += 1
            return _complete(*args, **kwargs)

        counted.model = getattr(complete, "model", "injected")
        setattr(agent, name, counted)


def _action_summary(decision, case, offers):
    """Retain audit terms, never free-form bodies/prompts or arbitrary model strings."""
    if not isinstance(decision, dict) or not isinstance(decision.get("message"), dict):
        return {"action": "invalid"}
    message = decision["message"]
    action = message.get("action") or {}
    kind = action.get("type", "send") if isinstance(action, dict) else "invalid"
    sid = decision.get("supplier_id")
    out = {"supplier_id": sid if sid in case["suppliers"] else "invalid",
           "action": kind if kind in {"send", "accept", "walk_away"} else "invalid"}
    if kind == "accept":
        sheet = action.get("term_sheet_id")
        out["term_sheet_id"] = sheet if isinstance(sheet, str) and sheet in offers else "invalid"
    sheets = message.get("term_sheets")
    if isinstance(sheets, list):
        out["term_sheets"] = []
        for sheet in sheets[:3]:
            terms = sheet.get("terms", {}) if isinstance(sheet, dict) else {}
            clean = {}
            if isinstance(terms, dict):
                for field, value in terms.items():
                    if field not in case["negotiable"]:
                        continue
                    allowed = case.get("term_options", {}).get(field, [])
                    clean[field] = (value if (type(value) in (int, float) and math.isfinite(value))
                                    or value in allowed else "invalid")
            out["term_sheets"].append(clean)
    return out


def _match(variant, factory, scenario, wave, waves, decision_seconds):
    started = time.monotonic()
    sim = CompetitionSim(scenario, waves)
    sim.now += (wave - 1) * 480
    wave_start = sim.now
    opened = sim.open("local", f"case:R{wave}-{scenario.case['case_id'][5:]}")
    session = sim.sessions[opened["session_id"]]
    case = copy.deepcopy(opened["case"])
    counts, decisions, cache = Counter(), [], {}
    error = None
    invalid_moves = 0
    try:
        agent = factory()
        _count_completions(agent, counts)
        for _ in range(100):
            sim.session("local", session["id"])
            if session["status"] == "ended":
                break
            prep = time.monotonic()
            threads = _public_threads(sim, session, cache)
            live = live_threads(threads)
            prices = [{"supplier_id": sid, "unit_price": standing(threads[sid])[1].get("unit_price"),
                       "term_sheet_id": standing(threads[sid])[0], "status": latest(threads[sid]).get("status")}
                      for sid in live]
            sim.now += time.monotonic() - prep
            if not live:
                sim._settle(session, "no_deal")
                break
            wave_left = wave_start + 480 - sim.now
            thread_left = min(session["threads"][sid]["last_at"] + MOVE_LIMIT_S - sim.now for sid in live)
            budget = max(0, min(decision_seconds, thread_left - SEND_MARGIN_S,
                                wave_left - CLOSE_BEFORE_CAP_S))
            before = counts["attempts"]
            decision_start = time.monotonic()
            forced_close = wave_left <= CLOSE_BEFORE_CAP_S or thread_left <= SEND_MARGIN_S
            if hasattr(agent, "last_decision"):
                agent.last_decision = {}
            decision_error = None
            try:
                if forced_close:
                    decision = getattr(agent, "closing_move", closing_move)(case, threads)
                elif timed := getattr(agent, "decide_timed", None):
                    decision = timed(case, threads, deadline=decision_start + budget, wave_seconds_left=wave_left)
                else:
                    decision = agent.decide(case, threads)
            except Exception as failure:
                decision_error = failure
            elapsed = time.monotonic() - decision_start
            sim.now += elapsed
            trace = dict(getattr(agent, "last_decision", {}))
            trace.update(decision_wall_s=elapsed, budget_s=budget, model_attempts=counts["attempts"] - before,
                         forced_close=forced_close, elapsed_over_budget=elapsed > budget if budget > 0 else False)
            trace.update(wave_seconds_left=wave_left, thread_seconds_left=thread_left,
                         standing_prices=prices)
            trace.setdefault("route", "model" if trace["model_attempts"] else "script")
            trace.setdefault("fallback", False)
            trace.setdefault("reason", "deadline_close" if forced_close else "agent_policy")
            if decision_error:
                trace["error"] = type(decision_error).__name__
            decisions.append(trace)
            if decision_error:
                raise decision_error
            trace["proposed_action"] = _action_summary(decision, case, session["offers"])
            sim.session("local", session["id"])
            if session["status"] == "ended":
                break
            if elapsed > budget and budget > 0:
                fresh = _public_threads(sim, session, cache)
                decision = getattr(agent, "closing_move", closing_move)(case, fresh)
                trace["fallback"] = True
                trace["deadline_override"] = True
            trace["sent_action"] = _action_summary(decision, case, session["offers"])
            try:
                reply = sim.message("local", session["id"], decision["supplier_id"], decision["message"])
                sheet = (reply.get("term_sheets") or [{}])[0]
                trace["supplier_reply"] = {"supplier_id": decision["supplier_id"], "status": reply.get("status"),
                                           "unit_price": sheet.get("terms", {}).get("unit_price"),
                                           "term_sheet_id": sheet.get("term_sheet_id")}
            except Fail as failure:
                trace["submission_error"] = failure.status
                if failure.status != 409:
                    invalid_moves += int(failure.status == 422)
                    raise
        else:
            raise RuntimeError("synthetic move limit exceeded")
    except Exception as failure:
        error = type(failure).__name__  # keep prompts, request headers and credentials out of reports
    if session["status"] != "ended":
        sim.now = wave_start + 480
        sim._capped(session)
    result = session["result"]
    valid = error is None and invalid_moves == 0 and (not result["deal"] or bool(result["must_haves_ok"]))
    return {
        "variant_id": variant, "scenario": scenario.name, "seed": scenario.seed, "holdout": scenario.holdout,
        "wave": wave, "case_id": case["case_id"], "deal": result["deal"], "valid": valid,
        "supplier_id": result["supplier_id"], "terms": result["terms"],
        "finished_before_cap": error is None and session["end_reason"] != "wave_cap",
        "score": result["score"] if valid else 0.0, "cost_eur": result["realized_cost"],
        "reference_cost_eur": sim.reference["cost"], "reference_kind": sim.reference["kind"],
        "regret_eur": (round(result["realized_cost"] - sim.reference["cost"], 2)
                       if valid and result["realized_cost"] is not None else None),
        "end_reason": session["end_reason"], "error": error, "invalid_moves": invalid_moves,
        "thread_timeouts": sum(len(th["late"]) for th in session["threads"].values()),
        "model_attempts": counts["attempts"], "fallbacks": sum(bool(d["fallback"]) for d in decisions),
        "routes": dict(Counter(d["route"] for d in decisions)), "decisions": decisions,
        "decision_wall_s": sum(d["decision_wall_s"] for d in decisions),
        "supplier_delay_s": sim.supplier_delay_s, "virtual_elapsed_s": sim.now - wave_start,
        "wall_s": time.monotonic() - started,
    }


def run_competition(factories, scenarios=None, seeds=(0, 1, 2), waves=5, workers=4, *,
                    holdout=False, decision_seconds=25):
    """Run paired policies in parallel within five sequential virtual 480-second waves.

    factories maps variant names to no-argument fresh-agent factories. scenarios is an optional
    sequence of IDs from build_scenarios(). Provider clients/ledgers can be shared by factories.
    Live completions, if supplied, are the caller's explicit responsibility; defaults use none.
    """
    if not factories or type(waves) is not int or waves < 1 or type(workers) is not int or workers < 1:
        raise ValueError("factories, positive waves and positive workers are required")
    if not math.isfinite(decision_seconds) or decision_seconds <= 0:
        raise ValueError("decision_seconds must be finite and positive")
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError("at least one seed is required")
    requested = None if scenarios is None else set(scenarios)
    cases = []
    for seed in seeds:
        available = build_scenarios(seed, holdout=holdout)
        if requested is not None and (not requested or requested - {s.name for s in available}):
            raise ValueError("scenarios must name existing scenario IDs")
        cases.extend(s for s in available if requested is None or s.name in requested)
    active = peak = 0
    lock = threading.Lock()

    def run(job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            return _match(*job, waves, decision_seconds)
        finally:
            with lock:
                active -= 1

    matches = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for wave in range(1, waves + 1):
            jobs = [(name, factory, scenario, wave) for scenario in cases for name, factory in factories.items()]
            matches.extend(pool.map(run, jobs))
    aggregate = {}
    for name in factories:
        rows = [row for row in matches if row["variant_id"] == name]
        latencies = sorted(d["decision_wall_s"] for row in rows for d in row["decisions"])
        regrets = [r["regret_eur"] for r in rows if r["regret_eur"] is not None]
        points = {str(w): round(100 * statistics.mean(r["score"] for r in rows if r["wave"] == w), 1)
                  for w in range(1, waves + 1)}
        aggregate[name] = {
            "matches": len(rows), "valid": sum(r["valid"] for r in rows), "deals": sum(r["deal"] for r in rows),
            "errors": sum(r["error"] is not None for r in rows), "wave_points": points,
            "finished_before_cap": sum(r["finished_before_cap"] for r in rows),
            "total_points": round(sum(points.values()), 1), "mean_score": statistics.mean(r["score"] for r in rows),
            "mean_regret_eur": statistics.mean(regrets) if regrets else None,
            "model_attempts": sum(r["model_attempts"] for r in rows), "fallbacks": sum(r["fallbacks"] for r in rows),
            "thread_timeouts": sum(r["thread_timeouts"] for r in rows),
            "decision_p95_s": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None,
            "decision_wall_s": sum(r["decision_wall_s"] for r in rows),
            "supplier_delay_s": sum(r["supplier_delay_s"] for r in rows),
            "routes": dict(sum((Counter(r["routes"]) for r in rows), Counter())),
        }
    return {
        "config": {"scoring_version": 2, "seeds": list(seeds), "waves": waves, "wave_seconds": 480, "workers": workers,
                   "holdout": holdout, "decision_seconds": decision_seconds,
                   "scenarios": [s.name for s in cases[:len(cases) // len(seeds)]]},
        "peak_active_matches": peak, "aggregate": aggregate, "matches": matches,
        "limitations": ["Synthetic supplier profiles do not predict hosted negotiation scores.",
                        "Scoring v2 requires an eligible opening; leadtime_repair includes a valid second bidder.",
                        "No deal without a declared alternative scores zero; its cost and regret are unknown (null).",
                        "Reference is a feasible candidate benchmark, not a proof of global optimality.",
                        "No arena HTTP rate limiting, venue network or shared hosted-service load is simulated.",
                        "Worker queue wait is excluded: each match starts its own virtual 480-second clock.",
                        "Five waves repeat the same seed/scenario economics; they are repetitions, not new case types.",
                        "Fixed contradiction feedback and hidden penalties test mechanics, not semantic persuasion.",
                        "Legacy agents without decide_timed cannot have blocking calls cancelled by this harness.",
                        "Model attempts count complete/fast/strong callables; other designs need their own telemetry."],
    }
