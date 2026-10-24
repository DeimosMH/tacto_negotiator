"""Local scoring probes for LOGIC.md. No HTTP, model, key or dotenv access.

Run with the repository's .venv Python from /tmp and an empty environment.
Assertions describe the current defects, so a corrected scorer should fail them.
"""

import copy
import json

from tacto_negotiator.competition import CompetitionSim, _supplier, build_scenarios
from tacto_negotiator.cost import must_haves_ok, score, total_cost


def ineligible_opening():
    """A cheap invalid opening suppresses a valid bidder's measured saving."""
    scenario = copy.deepcopy(next(s for s in build_scenarios(0) if s.name == "competing_rfq"))
    case = scenario.case
    case["suppliers"] = ["sup:A", "sup:B"]
    scenario.profiles.pop("sup:C")
    case["negotiable"].append("lead_time_days")
    case["term_options"]["lead_time_days"] = [45, 14]
    case["fixed_terms"].pop("lead_time_days")
    case["buyer"]["late_cost_per_day"] = 0
    case["buyer"]["must_haves"] = [{"field": "lead_time_days", "op": "<=", "value": 14}]
    for sid, profile in scenario.profiles.items():
        profile["standard"]["lead_time_days"] = 45 if sid == "sup:A" else 14
        profile["floor"] = 30

    sim = CompetitionSim(scenario, 1)
    opened = sim.open("local", "case:R1-C04")
    session = sim.sessions[opened["session_id"]]
    openings = {}
    for sid, thread in session["threads"].items():
        profile = scenario.profiles[sid]
        terms = {"unit_price": thread["opening"]}
        openings[sid] = {
            "eligible": must_haves_ok(case, terms, profile["standard"]),
            "cost": total_cost(case, terms, profile["standard"], _supplier(case, profile)),
        }
    assert not openings["sup:A"]["eligible"] and openings["sup:B"]["eligible"]

    price = round(sum(th["opening"] for th in session["threads"].values()) / 2, 2)
    reply = sim.message("local", session["id"], "sup:B", {"term_sheets": [{"terms": {
        "unit_price": price, "payment_days": 14, "lead_time_days": 14,
    }}]})
    assert reply["status"] == "agreed"
    result = sim.message("local", session["id"], "sup:B", {"action": {
        "type": "accept", "term_sheet_id": reply["term_sheets"][0]["term_sheet_id"],
    }})["result"]
    eligible_open = openings["sup:B"]["cost"]
    correct_score = score(eligible_open, result["c_deal"], result["c_best"])
    assert result["must_haves_ok"] and result["score"] == 0 and correct_score > 0
    return {
        "openings": openings,
        "reported_c_open": result["c_open"],
        "eligible_c_open": eligible_open,
        "c_deal": result["c_deal"],
        "c_best": result["c_best"],
        "reported_score": result["score"],
        "eligible_opening_score": correct_score,
    }


def invented_outside_option():
    """The uncovered-demand rate becomes a nonexistent no-deal alternative."""
    scenario = next(s for s in build_scenarios(0) if s.name == "simple_price")
    scenario.case["buyer"]["fallback_unit_price"] = 20
    assert scenario.case["buyer"]["alternative"]["kind"] == "none"
    sim = CompetitionSim(scenario, 1)
    opened = sim.open("local", "case:R1-C01")
    result = sim.message("local", opened["session_id"], "sup:A", {
        "action": {"type": "walk_away"},
    })["result"]
    assert not result["deal"] and result["c_alt"] is None and result["score"] == 1
    return {
        "alternative_kind": "none",
        **{key: result[key] for key in ("deal", "c_alt", "c_best", "c_open", "score", "realized_cost")},
        "required_no_alternative_score": 0,
    }


if __name__ == "__main__":
    print(json.dumps({
        "finding_1_ineligible_opening": ineligible_opening(),
        "finding_2_invented_outside_option": invented_outside_option(),
    }, indent=2, allow_nan=False))
