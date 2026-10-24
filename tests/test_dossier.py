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
        "idx:METAL@2025-09": {"series": "METAL", "month": "2025-09", "value": 100},
        "idx:METAL@2026-09": {"series": "METAL", "month": "2026-09", "value": 110},
    }
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    release = tmp_path / "release-sunday"
    release.mkdir()
    (release / "facts.json").write_text(json.dumps({"news:new": {
        "supplier": "sup:A", "date": "2026-10-25", "body": "New capacity."}}))
    package = DataPackage(tmp_path)
    case = {"suppliers": ["sup:A"], "parts": ["part:A"], "buyer": {"index_paths": {"METAL": .05}}}
    dossier = package.negotiation_facts(case)
    assert "doc:unrelated" not in dossier
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
