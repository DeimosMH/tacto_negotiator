"""Replicas of the hosted practice cases with a supplier calibrated on hosted replies.

The local scripted supplier concedes a fixed step to a floor and accepts at it, so it ranked
policies opposite to the hosted arena. This supplier follows what 383 recorded hosted replies
(ours and an earlier team's, same cases) show:

- in round 1 it closes a share of the gap to our ask, capped per supplier; later a smaller share;
- it concedes nothing without movement from us and never goes below its floor for the package;
- it agrees once our sheet reaches the counter it would make, not its floor;
- its final offer comes at a fixed round, earlier after a large cumulative gap;
- verified claims lower the first counter a little; trades carry premiums;
- an index clause is worth the part of its series' twelve-month rise it lets the supplier pass on.

Fitted per supplier it predicts the next counter within 1.0 % of the opening price (0.86 status
accuracy); profiles here are drawn from per-goal ranges, which held out at 2.1 % (0.77). Case
openings, standard terms, C_open and C_best are the recorded hosted ones, so local scores use the
hosted scale. Supplier reasoning and prose understanding are not modelled.
"""

import copy
import json
import random
from pathlib import Path

from .competition import CompetitionSim, Scenario
from .cost import closing_terms, cost_breakdown, decision_cost, must_haves_ok, score
from .data import DataPackage
from .evaluation import ClaimChecker
from .sim import Sim

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/practice_feedback.json"
DATA = Path(__file__).resolve().parents[2] / "data"
# (a1, cap1, a2, floor depth F, final round R, cumulative-gap % G) ranges by category goal.
# secure_supply G: 15 hosted P10 first rounds stayed open at package-adjusted gaps of 6.3-13.7 % and drew a
# final offer at 14.2 % and above.
PROFILE_RANGES = {
    "leverage": ((0.8, 0.9), (0.10, 0.20), (0.1, 0.2), (0.16, 0.20), (2, 3), (20, 30)),
    "stabilise": ((0.0, 0.7), (0.01, 0.04), (0.0, 0.3), (0.03, 0.07), (4, 5), (99, 99)),
    "routine": ((0.2, 0.3), (0.01, 0.04), (0.0, 0.2), (0.02, 0.07), (3, 4), (30, 40)),
    "secure_supply": ((0.0, 0.0), (0.01, 0.01), (0.6, 0.6), (0.02, 0.02), (2, 2), (14, 14)),
    "quality_first": ((0.0, 0.0), (0.01, 0.01), (0.3, 0.3), (0.03, 0.03), (5, 5), (99, 99)),
}
# Share of a clause's pass-through (material share x the series' twelve-month rise beyond the deadband)
# the supplier takes off the price for it. Hosted P02 (copper +47.7 %, share 0.2, 5 % deadband) countered
# a 90-day clause sheet at 349.05 and accepted 361.42 while its 90-day price without one held at
# 371.95-372.55: 22.9 EUR, 6.2 % of the opening price, against 0.7 x 0.2 x 42.7 % = 6.0 %. Steel
# (+5.9 %, share 0.45) in P08/P18 gives 0.3-1.9 %, and clause sheets there moved nothing measurable.
CLAUSE_PASS_THROUGH = 0.7
CLAIM_TYPES = {"fc": "volume_change", "cost": "should_cost", "quote": "rival_quote", "offer": "rival_quote",
               "news": "supplier_news"}


def draw_profile(goal, rng, *, opening_below_current=False):
    ranges = PROFILE_RANGES.get(goal, PROFILE_RANGES["routine"])
    a1, cap1, a2, depth = (rng.uniform(*r) for r in ranges[:4])
    final_round, gap = rng.randint(*ranges[4]), rng.uniform(*ranges[5])
    if opening_below_current:  # an RFQ bidder already priced below today's price has little room
        depth = 0.02
    return {"a1": a1, "cap1": cap1, "a2": a2, "depth": depth, "final_round": final_round, "final_gap": gap,
            "reply_delay_s": rng.uniform(15, 40)}


def premium(case, terms, opening, standard, p0, rises=None):
    """EUR per unit the supplier wants for a package relative to its opening terms. `rises` maps index
    series to their twelve-month rise (fraction); a clause on a series not in it is worth nothing."""
    t, base = closing_terms(case, terms, standard), closing_terms(case, opening, standard)
    value = 0.01 * p0 * (t["payment_days"] - base["payment_days"]) / 30
    value += 0.005 * p0 * (base["lead_time_days"] - t["lead_time_days"])
    value += 0.015 * p0 * ((t.get("chargeback_eur_per_complaint") or 0)
                           - (base.get("chargeback_eur_per_complaint") or 0)) / 100
    value -= 0.01 * p0 * max(0, t["contract_months"] - base["contract_months"]) / 12  # security has value
    # an index clause lets the supplier pass the series' rise on, so the base price can fall
    value -= (clause_value(t.get("index_clause"), rises) - clause_value(base.get("index_clause"), rises)) * p0
    return value


def clause_value(clause, rises):
    """Share of the price a supplier gives up for an index clause (`CLAUSE_PASS_THROUGH`)."""
    rise = (rises or {}).get((clause or {}).get("series"))
    if rise is None:
        return 0.0
    return CLAUSE_PASS_THROUGH * clause.get("material_share", 0) * max(0.0, rise - clause.get("deadband_pct", 0) / 100)


def clause_reserve(case, opening, rises, depth):
    """Share of the price a supplier quoting without a clause keeps above its floor on any package
    without one: what the least generous clause on offer would let it pass on anyway, up to its floor
    depth (never above its own opening). Hosted P02 never went below 369.5-372.6 without a clause,
    whatever the payment term (opening 370.96 at 30 days, every clause worth 6-6.7 %), so its floor
    sat at the opening, not 3-7 % below it as for other stabilise cases; in P08/P18 the widest steel
    clause is worth 0.3 %."""
    clauses = [c for c in case.get("term_options", {}).get("index_clause") or [] if c]
    if opening.get("index_clause") or not clauses:
        return 0.0
    return min(depth, min(clause_value(c, rises) for c in clauses))


def index_rises(case, package):
    """Twelve-month rise (fraction) of each index series the case's clause options use."""
    out = {}
    for clause in case.get("term_options", {}).get("index_clause") or []:
        if not clause or package is None or clause["series"] in out:
            continue
        refs = package.ids(f"idx:{clause['series']}@")
        newest = package.lookup(refs[-1]) if refs else None
        month = (newest or {}).get("month")
        previous = package.lookup(f"idx:{clause['series']}@{int(month[:4]) - 1}{month[4:]}") if month else None
        if previous and (previous.get("value") or 0) > 0:
            out[clause["series"]] = newest["value"] / previous["value"] - 1
    return out


def counter(profile, p0, round_, previous, ask, cumulative_gap, last_ask):
    """The supplier's next price and status for one package (prices already package-adjusted)."""
    floor = p0 * (1 - profile["depth"]) + profile.get("package_premium", 0.0)
    if ask >= previous - 1e-9:
        return ask, "agreed"
    if round_ == 1:
        concession = min(profile["a1"] * (previous - ask), profile["cap1"] * p0)
    else:
        concession = profile["a2"] * (previous - ask)
    if last_ask is not None and ask <= last_ask + 1e-9:
        concession = 0.0  # no movement from us, none from the supplier
    price = max(floor, previous - concession, ask)
    if ask >= price - 1e-9 or (ask >= floor and price - ask < 0.002 * p0):
        return ask, "agreed"
    final = round_ >= profile["final_round"] or cumulative_gap >= profile["final_gap"]
    return price, "final_offer" if final else "open"


def build_replica_scenarios(seed=0, fixture=FIXTURE):
    """One scenario per recorded hosted practice case, with seeded private supplier profiles."""
    data = json.loads(Path(fixture).read_text())
    rng = random.Random(f"replica:{seed}")
    out = []
    for case_id, case in sorted(data["cases"].items()):
        session = next(s for s in data["sessions"] if s["case_id"] == case_id)
        goal = case["buyer"].get("category_goal", "routine")
        current = case["buyer"].get("current_unit_price") or 0
        profiles = {}
        for sid in case["suppliers"]:
            thread = session["threads"][sid]
            opening = {**thread["standard"], **case.get("fixed_terms", {}), **thread["opening"][0]["terms"]}
            p0 = opening["unit_price"]
            # hosted P05: the bidder at 0.87x today's price had ~2 % room, the one at 0.99x had ~22 %
            profiles[sid] = {**draw_profile(goal, rng, opening_below_current=len(case["suppliers"]) > 1
                                            and current and p0 < 0.9 * current),
                             "opening": opening, "standard": dict(thread["standard"]), "p0": p0}
        reference = {"c_open": session["result"]["c_open"], "c_best": session["result"]["c_best"]}
        out.append(Scenario(case_id[5:], copy.deepcopy(case), profiles, seed, False, HostedLikeSim, reference))
    return out


class HostedLikeSim(CompetitionSim):
    """CompetitionSim's clock, waves and scoring flow with the calibrated supplier above."""

    _package = None

    def __init__(self, scenario, waves, package=None):
        self.scenario = copy.deepcopy(scenario)
        self.now, self.supplier_delay_s = 1_000_000.0, 0.0
        if package is None:  # shared, read-only: the supplier's claim facts and health estimates
            if HostedLikeSim._package is None:
                HostedLikeSim._package = DataPackage(DATA)
                HostedLikeSim._package.lookup("")
            package = HostedLikeSim._package
        self.package = package
        self.reference = {"cost": scenario.reference["c_best"], "kind": "recorded hosted C_best"}
        Sim.__init__(self, {scenario.case["case_id"]: copy.deepcopy(scenario.case)}, clock=lambda: self.now)
        # After Sim.__init__, which resets the facts: hosted checks claims against the whole package
        # (cited index falls verified there), so an empty fact table marked every claim contradicted.
        self.facts = {**(package.facts if package is not None else {}), **scenario.case.get("facts", {})}
        self.rehearse(wait=0, cap=480, brk=0, waves=waves)

    def open(self, team, case_id):
        opened = Sim.open(self, team, case_id)
        state = self.sessions[opened["session_id"]]
        state["files"].clear()
        state["checker"] = ClaimChecker(state["case"], self.facts)
        state["estimates"] = self.package.supplier_estimates(state["case"]) if self.package is not None else {}
        for sid, thread in state["threads"].items():
            profile = self.scenario.profiles[sid]
            thread.update(standard=dict(profile["standard"]), opening=profile["p0"], cumulative_gap=0.0,
                          last_asks={}, claimed=False)
            thread["messages"].clear()
            stated = {f: profile["opening"][f] for f in state["case"]["negotiable"] if f in profile["opening"]}
            self._mail(state, thread, "open", stated, "Please find our quote attached.", ["quote.pdf"])
        opened["threads"] = {sid: list(th["messages"]) for sid, th in state["threads"].items()}
        state["eligible_opening_cost"] = self.scenario.reference["c_open"]
        return copy.deepcopy(opened)

    def _claims(self, state, sid, message):
        refs = list(dict.fromkeys(message.get("refs", []) + _ids(message.get("body", ""))))
        claims, indices = [], [r for r in refs if r.startswith("idx:")]
        if len(indices) >= 2:
            claims.append({"type": "index_change", "refs": indices[:2]})
        for ref in refs:
            kind = CLAIM_TYPES.get(ref.split(":", 1)[0])
            if kind:
                claims.append({"type": kind, "refs": [ref]})
        return state["checker"].check(sid, claims[:3], state["offers"]) if claims else []

    def _answer(self, state, thread, message, sheets):
        sid = next(s for s, candidate in state["threads"].items() if candidate is thread)
        profile, case = self.scenario.profiles[sid], state["case"]
        p0, round_ = profile["p0"], thread["round"]
        results = self._claims(state, sid, message)
        verified_bonus = 0.0
        if any(r["result"] == "verified" for r in results) and not thread["claimed"]:
            thread["claimed"], verified_bonus = True, 0.005 * p0
        stated = thread["stated"]
        if "index_rises" not in state:
            state["index_rises"] = index_rises(case, self.package)
        rises = state["index_rises"]
        reserve = clause_reserve(case, profile["opening"], rises, profile["depth"]) * p0

        def package_premium(t):  # without a clause the reserve stays on the floor and absorbs premiums we ask
            value = premium(case, t, profile["opening"], profile["standard"], p0, rises)
            return value if t.get("index_clause") else value + max(0.0, reserve - max(0.0, value))

        best = None
        for terms in sheets:
            extra = package_premium(terms)
            previous = (stated["unit_price"] + premium(case, terms, profile["opening"], profile["standard"], p0, rises)
                        - premium(case, stated, profile["opening"], profile["standard"], p0, rises))
            key = json.dumps({k: v for k, v in terms.items() if k != "unit_price"}, sort_keys=True)
            gap = max(0.0, (previous - terms["unit_price"]) / p0 * 100)
            price, status = counter({**profile, "package_premium": extra}, p0, round_, previous,
                                    terms["unit_price"], thread["cumulative_gap"] + gap, thread["last_asks"].get(key))
            margin = terms["unit_price"] - (p0 * (1 - profile["depth"]) + extra)
            option = (status == "agreed", margin, terms, price, status, key, gap)
            if best is None or option[:2] > best[:2]:
                best = option
        if best is None:  # prose only: the supplier restates its position
            reply = self._mail(state, thread, thread["status"], stated, "We hold our offer.", ["quote.pdf"])
        elif thread["status"] == "final_offer" and not best[0]:
            thread["closed"] = "walked_away"
            reply = self._mail(state, thread, "walked_away", stated, "We cannot improve further.")
        else:
            agreed, _, terms, price, status, key, gap = best
            thread["cumulative_gap"] += gap
            thread["last_asks"][key] = terms["unit_price"]
            if not agreed:
                floor = p0 * (1 - profile["depth"]) + package_premium(terms)
                price = max(floor, price - verified_bonus)
            reply = self._mail(state, thread, status, {**terms, "unit_price": round(price, 2)},
                               "This is our position on your package.", ["quote.pdf"])
        reply["claim_results"] = results
        delay = profile["reply_delay_s"]
        self.now += delay
        self.supplier_delay_s += delay
        thread["last_at"] = self.now
        self._capped(state)
        return reply

    def _end(self, state, reason, sid):
        Sim._end(self, state, reason, sid)
        case, result = state["case"], state["result"]
        realized = result.get("c_alt")  # no deal scores the declared alternative, if any
        valid = True
        if sid:
            profile = self.scenario.profiles[sid]
            terms = closing_terms(case, state["threads"][sid]["stated"], profile["standard"])
            scored = {**case, "_supplier_estimates": state.get("estimates", {})}
            realized = decision_cost(scored, terms, profile["standard"], sid)
            valid = must_haves_ok(case, terms)
            result.update(terms=terms, c_deal=realized, must_haves_ok=valid,
                          cost_breakdown=cost_breakdown(case, terms, profile["standard"]))
        result.update(c_open=state["eligible_opening_cost"], c_best=self.reference["cost"],
                      realized_cost=realized,
                      score=score(state["eligible_opening_cost"], realized, self.reference["cost"]) if valid else 0.0)


def _ids(text):
    from .agents.llm import ID_RE

    return ID_RE.findall(text or "")
