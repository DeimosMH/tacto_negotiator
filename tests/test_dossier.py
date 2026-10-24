"""Public evidence retrieval and routing, without credentials or network access."""

import json

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
        "idx:METAL@2026-09": {"series": "METAL", "month": "2026-09", "value": 110},
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
    assert dossier["idx:METAL@2026-09"]["computed_change_pct"] == 10
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


def test_script_cites_latest_positive_forecast_once_without_model():
    agent = RoutedAgent(None)
    case = {"parts": ["part:A", "part:B"], "facts": {
        "fc:A@2025-01": {"part": "part:A", "change_pct": 40},
        "fc:A@2026-01": {"part": "part:A", "change_pct": 12.5},
        "fc:B@2026-01": {"part": "part:B", "change_pct": -10}}}
    body = agent._forecast_evidence(case, {})
    assert "12.5%" in body and "fc:A@2026-01" in body
    assert "2025" not in body and "part:B" not in body
    assert not agent._forecast_evidence(case, {"sup:A": [{"from": "buyer", "body": body}]})


def test_script_sends_one_forecast_per_case_weighted_by_volume():
    agent = RoutedAgent(None)
    case = {"parts": ["part:A", "part:B"], "facts": {
        "fc:A@2026-01": {"part": "part:A", "change_pct": 20, "volume_per_year": 100},
        "fc:B@2026-01": {"part": "part:B", "change_pct": 5, "volume_per_year": 1000}}}
    body = agent._forecast_evidence(case, {})
    assert "fc:B@2026-01" in body and "fc:A" not in body
    # hosted replays mark any later forecast claim duplicate, so another part is not cited either
    assert not agent._forecast_evidence(case, {"sup:X": [{"from": "buyer", "body": body}]})


def test_dossier_offers_the_steepest_recent_fall_and_flags_a_rise(tmp_path):
    facts = {f"idx:M@2026-{m:02d}": {"series": "M", "month": f"2026-{m:02d}", "value": v}
             for m, v in zip(range(1, 10), (100, 104, 106, 103, 101, 99, 100, 102, 105), strict=True)}
    facts["idx:M@2025-09"] = {"series": "M", "month": "2025-09", "value": 90}
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    package = DataPackage(tmp_path)
    case = {"suppliers": ["sup:A"], "parts": ["part:A"], "buyer": {"index_paths": {"M": []}}}
    dossier = package.negotiation_facts(case)
    assert dossier["idx:M@2026-09"]["note"].startswith("rising")  # +16.7 % over twelve months
    fall = dossier["idx:M@2026-06"]  # 106 -> 99: the steepest 3-12 month fall ending in the last 6 months
    assert fall["comparison_ref"] == "idx:M@2026-03" and fall["computed_change_pct"] == -6.6
