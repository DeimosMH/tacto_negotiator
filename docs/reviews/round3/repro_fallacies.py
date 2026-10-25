"""Reproductions for round3/FALLACIES.md (logic review at d842b8e, 2026-10-25).

Offline only: in-memory threads, the shipped fixtures, the local hosted-like supplier and the deal
journal files under docs/. No arena, no model, no .env. One line (or a short block) per finding.

    .venv/bin/python docs/reviews/round3/repro_fallacies.py
"""

import copy
import glob
import json
import logging
import re
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
logging.disable(logging.CRITICAL)

from tacto_negotiator.agents import routed as routed_mod  # noqa: E402
from tacto_negotiator.agents.facts import CASH_STRESS_TOPICS, HEDGED, NEED_WORK_TOPICS  # noqa: E402
from tacto_negotiator.agents.routed import CLAUSE_RISE_RATIO, RoutedAgent  # noqa: E402
from tacto_negotiator.compete import make_factories  # noqa: E402
from tacto_negotiator.competition import run_competition  # noqa: E402
from tacto_negotiator.cost import cheapest_price, cost_breakdown, decision_cost, total_cost  # noqa: E402
from tacto_negotiator.data import DataPackage  # noqa: E402
from tacto_negotiator.hosted_like import (HostedLikeSim, build_oos_scenarios, clause_value,  # noqa: E402
                                          index_rises, premium)
from tacto_negotiator.play import ask_limit  # noqa: E402
from tacto_negotiator.sim import load_cases  # noqa: E402

PKG = HostedLikeSim.shared_package()
CASES = {c["case_id"]: c for c in json.loads((ROOT / "tests/fixtures/practice_cases.json").read_text())["cases"]}


def sheets_of(move):
    return [(s["terms"]["unit_price"], (s["terms"].get("index_clause") or {}).get("deadband_pct"),
             s["terms"].get("contract_months"), s["terms"].get("payment_days"))
            for s in move["message"].get("term_sheets", [])]


def l1_concession_still_inverted():
    case = copy.deepcopy(load_cases()["case:S01"])
    sid = case["suppliers"][0]

    def sup(p, n):
        return {"from": "supplier", "status": "open", "body": "x",
                "term_sheets": [{"term_sheet_id": f"offer:{n}", "terms": {"unit_price": p, "payment_days": 30}}]}
    buy = {"from": "buyer", "body": "x", "term_sheets": [{"terms": {"unit_price": 15, "payment_days": 30}}]}
    c0 = total_cost(case, {"unit_price": 0.0001, "payment_days": 30})
    c1 = total_cost(case, {"unit_price": 1, "payment_days": 30})
    out = {}
    for label, reply in (("holds 20", 20), ("concedes to 19", 19), ("concedes to 17", 17)):
        limit = ask_limit(case, [sup(20, 1), buy, sup(reply, 2)], sid)
        out[label] = round(0.0001 + (limit - c0) / (c1 - c0) - 15, 2)
    print(f"L1 (=R2 A1, open) our next move from 15: {out}  -> the more it concedes, the less we move")


def l2_mirror_to_lone_final():
    case = copy.deepcopy(load_cases()["case:S01"])
    sid = case["suppliers"][0]
    alt = {"unit_price": 17.6, "payment_days": 30}
    threads = {sid: [
        {"from": "supplier", "status": "open", "body": "x",
         "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 20, "payment_days": 30}}]},
        {"from": "buyer", "body": "x", "term_sheets": [{"terms": {"unit_price": 15, "payment_days": 30}}]},
        {"from": "supplier", "status": "final_offer", "body": "x", "term_sheets": [
            {"term_sheet_id": "offer:2", "terms": {"unit_price": 18, "payment_days": 30}},
            {"term_sheet_id": "offer:2b", "terms": alt}]}]}
    agent = RoutedAgent(None)
    guarded = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "body": "x", "term_sheets": [alt]})
    reason, move = agent._script(case, threads)
    print(f"L2 (=R2 A2, open) sole supplier's final offer: guard -> {guarded}; script -> {reason!r}, "
          f"{len(move['message'].get('term_sheets', []))} sheet sent; routed.py:133 still says 'v5: 3 of 3'")


def p16_opening():
    sc = [s for s in build_oos_scenarios(0) if s.name == "P16"][0]
    case = copy.deepcopy(sc.case)
    sid = case["suppliers"][0]
    terms = {f: sc.profiles[sid]["opening"][f] for f in case["negotiable"]}
    threads = {sid: [{"from": "supplier", "status": "open", "body": "Our offer.",
                      "term_sheets": [{"term_sheet_id": "offer:0", "terms": terms}]}]}
    return case, sid, terms, threads


def l3_clause_sheet_disables_held_package():
    case, sid, terms, threads = p16_opening()
    agent = RoutedAgent(None, package=PKG)
    agent._prepare(case)
    reason, move = agent._script(case, threads)
    print(f"L3 P16 (leverage, synthesized opening {terms['unit_price']}) round 1 {reason}: {sheets_of(move)}")
    clause0 = [c for c in case["term_options"]["index_clause"] if c and c["deadband_pct"] == 0][0]
    own = {**terms, "index_clause": clause0, "unit_price": 88.0, "contract_months": 24}
    for label, first in (("with our clause sheet", move["message"]["term_sheets"]),
                         ("clause sheet removed", [s for s in move["message"]["term_sheets"]
                                                   if not s["terms"]["index_clause"]])):
        t = copy.deepcopy(threads)
        t[sid].append({"from": "buyer", "body": "x", "term_sheets": copy.deepcopy(first)})
        t[sid].append({"from": "supplier", "status": "open", "body": "We propose an index clause.",
                       "term_sheets": [{"term_sheet_id": "offer:1", "terms": own}]})
        a = RoutedAgent(None, package=PKG)
        a._prepare(case)
        r, m = a._script(case, t)
        print(f"   supplier then imposes its own 0% clause; {label}: _imposed_clause={a._imposed_clause(t[sid])}"
              f" -> round 2 sends {len(m['message']['term_sheets'])} sheet(s) {sheets_of(m)}")


def l4_clause_gate_knife_edge_and_circular():
    rows = []
    for cid in ("case:P02", "case:P08", "case:P16"):
        case = CASES[cid]
        rises = index_rises(case, PKG)
        b = case["buyer"]
        for c in case["term_options"]["index_clause"]:
            if not c:
                continue
            path = b["index_paths"].get(c["series"], 0)
            ours = max(0, abs(path) - c["deadband_pct"] / 100) + b.get("index_risk_pct", 0) / 100
            rise = rises[c["series"]]
            rows.append(f"{cid[5:]} db{c['deadband_pct']:g}: rise {rise:.1%} / our cost {ours:.0%} = "
                        f"{rise / ours:.2f} (replica gives {clause_value(c, rises):.2%} of price)")
    print(f"L4 clause gate ratio threshold {CLAUSE_RISE_RATIO}:")
    for row in rows:
        print("   " + row)


def bench(scenario, patches=(), waves=2):
    factories = make_factories(["scripts"], package=PKG)
    with contextlib_exit(patches):
        res = run_competition(factories, [scenario], (0, 1), waves, 4, bench="oos", timing="final")
    rows = res["matches"]
    return {(r["seed"], r["wave"]): round(r["score"], 3) for r in rows}


class contextlib_exit:
    def __init__(self, patches):
        self.patches = [patch.object(*p) if len(p) == 3 else patch(*p) for p in patches]

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def l5_bench_waves_repeat_and_clause_gain_is_built_in():
    on = bench("P16")
    off = bench("P16", [(RoutedAgent, "_equal_cost_clause", lambda *a, **k: None)])
    print(f"L5 oos P16 scores by (seed, wave): clause sheet on {on}; off {off}")
    print("   -> waves are exact repeats (n = seeds x cases, not x waves); the P16 gain exists only under "
          "the replica's clause_value, which hosted P08/P18 (5.9 % rise) did not show")
    p14 = bench("P14", waves=1)
    print(f"   oos P14 (stabilise) scripts: {p14} - replica scores health with the agent's own estimate")


def l6_lead_time_premium_by_construction():
    case = CASES["case:P12"]
    base = {"unit_price": 10.0, "lead_time_days": 21, "payment_days": 30}
    std = {"payment_days": 30, "lead_time_days": 21, "incoterm": "DAP", "ppm_commitment": 500,
           "chargeback_eur_per_complaint": 0.0, "volume_per_year": case["buyer"]["need_per_year"],
           "contract_months": 12, "tooling_eur": 0, "effective_date": None, "index_clause": None}
    slower = premium(case, {**base, "lead_time_days": 23}, base, std, 10.0)
    faster = premium(case, {**base, "lead_time_days": 16}, base, std, 10.0)
    print(f"L6 replica premium (EUR/unit, p0=10): 21->23 days {slower:+.3f}, 21->16 days {faster:+.3f}; "
          "hosted evidence (P10 54->42) is only the faster-than-standard direction")


def l7_p02_payment_misdiagnosis():
    case = CASES["case:P02"]
    est = DataPackage(ROOT / "data").supplier_estimates(case)
    seen = {}
    for f in sorted(glob.glob(str(ROOT / "docs/deals/*P02*.json"))):
        r = json.loads(Path(f).read_text()).get("result") or {}
        t, cb = r.get("terms"), r.get("cost_breakdown")
        if not t or not cb or not t.get("index_clause") or r.get("supplier_id") not in est:
            continue
        ours = cost_breakdown(case, t, None, est[r["supplier_id"]])
        seen[(t["payment_days"], t["unit_price"])] = (round(r["score"], 3), round(sum(cb.values())),
                                                       round(sum(ours.values())))
    print("L7 P02 clause deals (days, price): (score, arena C, our model C) ->",
          {k: v for k, v in sorted(seen.items()) if v[0] > 0.5})
    scored = {**case, "_supplier_estimates": est}
    sid = case["suppliers"][0]
    clause = [c for c in case["term_options"]["index_clause"] if c and c["deadband_pct"] == 5][0]
    by_days = {}
    for days in (30, 60, 90):
        v = {"payment_days": days, "index_clause": clause, "unit_price": 360.0}
        v["unit_price"] = cheapest_price(scored, v, None, sid)
        by_days[days] = round(decision_cost(scored, v, None, sid))
    print(f"   our own model at each term's health floor: {by_days} -> it already prefers 90; "
          "'our model rates them alike' (routed.py:366) is not what the model says")


def l8_held_package_not_held():
    case, sid, terms, threads = p16_opening()
    with patch.object(RoutedAgent, "_equal_cost_clause", lambda *a, **k: None):
        agent = RoutedAgent(None, package=PKG)
        agent._prepare(case)
        _, move = agent._script(case, threads)
        clause0 = [c for c in case["term_options"]["index_clause"] if c and c["deadband_pct"] == 0][0]
        t = copy.deepcopy(threads)
        t[sid].append({"from": "buyer", "body": "x", "term_sheets": copy.deepcopy(move["message"]["term_sheets"])})
        t[sid].append({"from": "supplier", "status": "open", "body": "x", "term_sheets": [
            {"term_sheet_id": "offer:1", "terms": {**terms, "index_clause": clause0, "unit_price": 88.0,
                                                    "contract_months": 24}}]})
        _, m = agent._script(case, t)
    print(f"L8 'held' package: our lead {sheets_of(move)[0]} -> held sheet {sheets_of(m)[0]} "
          "(payment term changed: the fallback is our cheapest sheet, not our package)")


def l9_topic_overlap_and_hedge_regex():
    both = NEED_WORK_TOPICS & CASH_STRESS_TOPICS
    fp = "We have secured additional capacity until March for your parts."
    print(f"L9 topics both 'needs work -> lower price' and 'cash stress': {sorted(both)}; "
          f"HEDGED matches {fp!r}: {bool(HEDGED.search(fp))}")


def l10_replicated_deterministic_runs():
    out = {}
    for arm in ("heldA-P08", "heldB-P08", "heldA-P18", "heldB-P18", "p07-sec0-", "p07-sec0.025-"):
        scores = []
        for f in sorted(glob.glob(str(ROOT / f"docs/results/live-practice-2026-10-25-{arm}*.json"))):
            scores += [round(m["score"], 4) for m in json.loads(Path(f).read_text()).get("matches", [])]
        out[arm.rstrip("-")] = scores
    print(f"L10 hosted 'repeated' arms: {out} -> identical repeats are n = 1 per arm")


def l11_doc_prompt_contradictions():
    runbook = (ROOT / "docs/FINAL_RUNBOOK.md").read_text()
    readiness = (ROOT / "docs/FINAL_READINESS.md").read_text()
    llm = (ROOT / "src/tacto_negotiator/agents/llm.py").read_text()
    print("L11 runbook 'never countered' + 'sent back once':", "never countered" in runbook and "sent back once" in runbook,
          "| readiness says fallback 'otherwise walks away':", "otherwise walks away" in readiness,
          "| prompt 'Accept only final offers and agreements' while _premature allows open accepts at <=3 moves:",
          bool(re.search(r"Accept only final offers and agreements", llm)))


if __name__ == "__main__":
    for probe in (l1_concession_still_inverted, l2_mirror_to_lone_final, l3_clause_sheet_disables_held_package,
                  l4_clause_gate_knife_edge_and_circular, l5_bench_waves_repeat_and_clause_gain_is_built_in,
                  l6_lead_time_premium_by_construction, l7_p02_payment_misdiagnosis, l8_held_package_not_held,
                  l9_topic_overlap_and_hedge_regex, l10_replicated_deterministic_runs, l11_doc_prompt_contradictions):
        try:
            probe()
        except Exception as error:  # keep the other probes running
            print(f"{probe.__name__}: probe error {type(error).__name__}: {error}")
