"""Public evidence retrieval and routing, without credentials or network access."""

import json

from tacto_negotiator.agents.facts import FactBank
from tacto_negotiator.agents.llm import LLMAgent
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.data import DataPackage


def test_dossier_reads_related_correspondence_and_release_without_unrelated_supplier(tmp_path):
    facts = {
        "doc:note": {"supplier": "sup:A", "date": "2026-07-01", "body": "Material hedged through March."},
        "doc:unrelated": {"supplier": "sup:B", "body": "Unrelated terms."},
        "quote:1": {"part": "part:A", "supplier": "sup:B", "unit_price": 10, "price_unit": 100},
        "quote:unrelated": {"part": "part:A", "unit_price": 1},
        "idx:METAL@2025-09": {"series": "METAL", "month": "2025-09", "value": 100},
        "idx:METAL@2026-09": {"series": "METAL", "month": "2026-09", "value": 90},
    }
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    release = tmp_path / "release-sunday"
    release.mkdir()
    (release / "facts.json").write_text(json.dumps({"news:new": {
        "supplier": "sup:A", "date": "2026-10-25", "body": "New capacity."}}))
    package = DataPackage(tmp_path)
    case = {"suppliers": ["sup:A"], "parts": ["part:A"], "refs": ["quote:1"],
            "buyer": {"index_paths": {"METAL": .05}}}
    dossier = package.negotiation_facts(case)
    assert "doc:unrelated" not in dossier
    assert "quote:unrelated" not in dossier
    assert dossier["doc:note"]["text"] == "Material hedged through March."
    assert dossier["news:new"]["text"] == "New capacity."
    assert dossier["quote:1"]["price_unit"] == 100
    assert {"idx:METAL@2025-09", "idx:METAL@2026-09"} <= dossier.keys()
    assert dossier["idx:METAL@2026-09"]["computed_change_pct"] == -10  # a fall: both endpoints shown
    assert "Material hedged" in LLMAgent(None, package).prepare(case)


def test_cost_claims_route_to_model_and_keep_semantic_context():
    case = {"case_id": "case:unseen", "buyer": {"category_goal": "routine"}}
    threads = {"sup:A": [{"from": "supplier", "status": "open", "body": "Metal is 40% of our cost."}]}
    agent = RoutedAgent(None)
    assert agent._semantic_blocker(case, threads)
    threads["sup:A"][0]["body"] = "Our revised quote."
    assert not agent._semantic_blocker(case, threads)
    agent.context.case_id = case["case_id"]
    assert agent._semantic_blocker(case, threads)


def test_prompt_prices_payment_tradeoffs_and_supplier_alternatives():
    from tacto_negotiator.cost import total_cost
    from tacto_negotiator.sim import load_cases

    case = load_cases()["case:S01"]
    case = {**case, "term_options": {"payment_days": [14, 30, 60]}}
    terms = {"unit_price": 20, "payment_days": 30}
    alternative = {"unit_price": 19, "payment_days": 14}
    threads = {case["suppliers"][0]: [{"from": "supplier", "status": "open", "term_sheets": [
        {"term_sheet_id": "offer:current", "terms": terms},
        {"term_sheet_id": "offer:alternative", "terms": alternative}]}]}
    prompt = LLMAgent(None).render(case, threads)
    assert "TERM TRADEOFFS" in prompt
    assert f'"cost_eur": {total_cost(case, {**terms, "payment_days": 60})}' in prompt
    assert f'"cost_eur": {total_cost(case, alternative)}' in prompt
    assert '"term_sheet_id": "offer:alternative"' in prompt


def opening(*suppliers, price=10.0):
    return {sid: [{"from": sid, "status": "open", "body": "Our offer.", "term_sheets": [
        {"term_sheet_id": f"offer:{sid}", "terms": {"unit_price": price}}]}] for sid in suppliers}


def test_bank_cites_latest_positive_forecast_once_per_supplier():
    bank = FactBank()
    case = {"case_id": "c", "parts": ["part:A", "part:B"], "suppliers": ["sup:A", "sup:B"], "buyer": {},
            "facts": {"fc:A@2025-01": {"part": "part:A", "change_pct": 40},
                      "fc:A@2026-01": {"part": "part:A", "change_pct": 12.5},
                      "fc:B@2026-01": {"part": "part:B", "change_pct": -10}}}
    threads = opening("sup:A", "sup:B")
    [fact] = bank.assign(case, threads, "sup:A")
    assert "12.5%" in fact.sentence and "fc:A@2026-01" in fact.sentence and "2025" not in fact.sentence
    threads["sup:A"].append({"from": "buyer", "body": fact.sentence})
    assert not bank.assign(case, threads, "sup:A")
    # every bidder of an RFQ is its own negotiation: a fact told to one is new to the other
    assert [f.key for f in bank.assign(case, threads, "sup:B")] == ["fc:"]


def test_bank_sends_one_forecast_weighted_by_volume():
    case = {"case_id": "c", "parts": ["part:A", "part:B"], "suppliers": ["sup:A"], "buyer": {}, "facts": {
        "fc:A@2026-01": {"part": "part:A", "change_pct": 20, "volume_per_year": 100},
        "fc:B@2026-01": {"part": "part:B", "change_pct": 5, "volume_per_year": 1000}}}
    facts = FactBank().assign(case, opening("sup:A"), "sup:A")
    assert [f.refs for f in facts] == [("fc:B@2026-01",)]


def test_dossier_offers_the_steepest_recent_fall_and_flags_a_rise(tmp_path):
    facts = {f"idx:M@2026-{m:02d}": {"series": "M", "month": f"2026-{m:02d}", "value": v}
             for m, v in zip(range(1, 10), (100, 104, 106, 103, 101, 99, 100, 102, 105), strict=True)}
    facts["idx:M@2025-09"] = {"series": "M", "month": "2025-09", "value": 90}
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    package = DataPackage(tmp_path)
    case = {"suppliers": ["sup:A"], "parts": ["part:A"], "buyer": {"index_paths": {"M": []}}}
    dossier = package.negotiation_facts(case)
    assert dossier["idx:M@2026-09"]["note"].startswith("rose")  # +16.7 % over twelve months
    assert "idx:M@2025-09" not in dossier and "computed_change_pct" not in dossier["idx:M@2026-09"]
    fall = dossier["idx:M@2026-06"]  # 106 -> 99: the steepest 3-12 month fall ending in the last 6 months
    assert fall["comparison_ref"] == "idx:M@2026-03" and fall["computed_change_pct"] == -6.6


def test_bank_paces_falling_cost_drivers_two_in_round_one_then_one_per_message(tmp_path):
    facts = {f"idx:M@2026-{m:02d}": {"series": "M", "month": f"2026-{m:02d}", "value": v}
             for m, v in zip(range(1, 10), (100, 104, 106, 103, 101, 99, 100, 102, 105), strict=True)}
    for name, scale in (("E", 1.0), ("F", 0.5)):
        facts |= {f"idx:{name}@2026-{m:02d}": {"series": name, "month": f"2026-{m:02d}", "value": (100 - m) * scale}
                  for m in range(1, 10)}
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    package = DataPackage(tmp_path)
    clause = {"series": "M", "material_share": 0.4, "deadband_pct": 0}
    case = {"case_id": "c", "parts": ["part:A"], "suppliers": ["sup:A"], "buyer": {"category_goal": "leverage"},
            "term_options": {"index_clause": [None, clause, {**clause, "series": "E", "material_share": 0.1},
                                              {**clause, "series": "F", "material_share": 0.01}]}}
    assert [row[1] for row in package.index_falls(case)] == ["M", "E", "F"]  # -6.6 % x 0.4 before -8.1 % x 0.1
    bank, threads = FactBank(package), opening("sup:A")
    first = bank.assign(case, threads, "sup:A")
    assert [f.key for f in first] == ["idx:M", "idx:E"]  # two index series in round 1, the tiny fall is a filler
    assert "M fell from 106 (idx:M@2026-03) to 99 (idx:M@2026-06), a change of -6.60%." == first[0].sentence
    threads["sup:A"] += [{"from": "buyer", "body": " ".join(f.sentence for f in first)},
                         {"from": "sup:A", "status": "open", "body": "Counter.", "term_sheets": [
                             {"term_sheet_id": "offer:2", "terms": {"unit_price": 9.5}}]}]
    assert [f.key for f in bank.assign(case, threads, "sup:A")] == ["idx:F"]  # filler once strong ones are used


def test_paced_body_keeps_only_the_assigned_fact_and_adds_it_when_missing():
    case = {"case_id": "c", "parts": ["part:A"], "suppliers": ["sup:A"], "buyer": {}, "facts": {
        "fc:A@2026-01": {"part": "part:A", "change_pct": 12.5},
        "cost:A": {"part": "part:A", "unit_cost": 8.0, "price_unit": 1}}}
    agent, threads = RoutedAgent(None), opening("sup:A")
    threads["sup:A"] += [{"from": "buyer", "body": "Opening."},
                         {"from": "sup:A", "status": "open", "body": "Counter.", "term_sheets": [
                             {"term_sheet_id": "offer:2", "terms": {"unit_price": 9.5}}]}]
    body = agent._paced_body(case, threads, "sup:A", "Dear team,\n\nOur forecast fc:A@2026-01 rose 12.5%. "
                             "Please review.\n\nKind regards\nBuyer")
    # one new fact per later message: the should-cost (strongest) replaces the forecast, kept for later
    assert "fc:A" not in body and "Our should-cost estimate for part:A is EUR 8 per unit (cost:A)" in body
    assert body.index("cost:A") < body.index("Kind regards") and "Please review." in body


