"""Replicas of the hosted practice cases with a supplier calibrated on hosted replies.

The local scripted supplier concedes a fixed step to a floor and accepts at it, so it ranked
policies opposite to the hosted arena. This supplier follows what 383 recorded hosted replies
(ours and an earlier team's, same cases) show:

- in round 1 it closes a share of the gap to our ask, capped per supplier; later a smaller share;
- it concedes nothing without movement from us and never goes below its floor for the package;
- it agrees once our sheet reaches the counter it would make, not its floor;
- its final offer comes at a fixed round, earlier after a large cumulative gap;
- verified claims lower the first counter a little; trades carry premiums.

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
PROFILE_RANGES = {
    "leverage": ((0.8, 0.9), (0.10, 0.20), (0.1, 0.2), (0.16, 0.20), (2, 3), (20, 30)),
    "stabilise": ((0.0, 0.7), (0.01, 0.04), (0.0, 0.3), (0.03, 0.07), (4, 5), (99, 99)),
    "routine": ((0.2, 0.3), (0.01, 0.04), (0.0, 0.2), (0.02, 0.07), (3, 4), (30, 40)),
    "secure_supply": ((0.0, 0.0), (0.01, 0.01), (0.6, 0.6), (0.02, 0.02), (2, 2), (15, 15)),
    "quality_first": ((0.0, 0.0), (0.01, 0.01), (0.3, 0.3), (0.03, 0.03), (5, 5), (99, 99)),
}
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


def premium(case, terms, opening, standard, p0):
    """EUR per unit the supplier wants for a package relative to its opening terms."""
    t, base = closing_terms(case, terms, standard), closing_terms(case, opening, standard)
    value = 0.01 * p0 * (t["payment_days"] - base["payment_days"]) / 30
    value += 0.005 * p0 * (base["lead_time_days"] - t["lead_time_days"])
    value += 0.015 * p0 * ((t.get("chargeback_eur_per_complaint") or 0)
                           - (base.get("chargeback_eur_per_complaint") or 0)) / 100
    value -= 0.01 * p0 * max(0, t["contract_months"] - base["contract_months"]) / 12  # security has value
    if t.get("index_clause") and not base.get("index_clause"):
        value -= 0.02 * p0  # an index clause shifts price risk to us, so the base price can fall
    return value


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
        best = None
        for terms in sheets:
            extra = premium(case, terms, profile["opening"], profile["standard"], p0)
            previous = stated["unit_price"] + extra - premium(case, stated, profile["opening"], profile["standard"], p0)
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
                floor = p0 * (1 - profile["depth"]) + premium(case, terms, profile["opening"], profile["standard"], p0)
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
