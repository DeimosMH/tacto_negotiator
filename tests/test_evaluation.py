"""The offline audit must reproduce every recorded hosted cost, score and claim verdict."""

import json
from pathlib import Path

from tacto_negotiator.data import DataPackage
from tacto_negotiator.evaluation import ClaimChecker, audit_feedback

ROOT = Path(__file__).resolve().parents[1]


def test_audit_matches_recorded_hosted_feedback():
    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    report = audit_feedback(feedback, DataPackage(ROOT / "data").facts)
    assert report["sessions"] == 32 and report["claim_checks"] > 30
    assert report["mismatches"] == [] and report["unmodeled_claims"] == []


def test_forecast_claims_count_once_per_case_and_rival_quotes_once_per_supplier():
    facts = {"fc:A@2026-09": {"part": "part:A", "change_pct": 6.0},
             "fc:B@2026-09": {"part": "part:B", "change_pct": 9.0},
             "quote:1": {"part": "part:A", "unit_price": 5.0}}
    case = {"parts": ["part:A", "part:B"], "refs": ["quote:1"], "buyer": {}}
    checker = ClaimChecker(case, facts)
    claims = [{"type": "volume_change", "refs": ["fc:A@2026-09"]},
              {"type": "volume_change", "refs": ["fc:B@2026-09"], "asserted": {"change_pct": 9.0}},
              {"type": "rival_quote", "refs": ["quote:1"]}]
    assert [r["result"] for r in checker.check("sup:X", claims)] == ["verified", "duplicate", "verified"]
    assert [r["result"] for r in checker.check("sup:Y", claims[2:])] == ["verified"]
    assert [r["result"] for r in checker.check("sup:Y", claims[2:])] == ["duplicate"]


def test_supplier_estimates_reproduce_hosted_health_charges():
    from tacto_negotiator.cost import decision_cost, total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    package = DataPackage(ROOT / "data")
    checked = 0
    for session in feedback["sessions"]:
        result, case = session["result"], dict(feedback["cases"][session["case_id"]])
        if not result["deal"] or case["buyer"].get("category_goal") not in ("stabilise", "secure_supply"):
            continue
        case["_supplier_estimates"] = package.supplier_estimates(case)
        sid, standard = result["supplier_id"], session["threads"][result["supplier_id"]]["standard"]
        health = decision_cost(case, result["terms"], standard, sid) - total_cost(case, result["terms"], standard)
        assert abs(health - result["cost_breakdown"].get("relationship", 0)) < 400, session["session_id"]
        checked += 1
    assert checked >= 8


def test_counter_price_moves_above_the_health_floor_when_the_limit_allows():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import cheapest_price, total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid, std = "sup:70510", {"ppm_commitment": 250, "chargeback_eur_per_complaint": 0.0}
    sheet = {"unit_price": 373.88, "payment_days": 90, "index_clause": None}
    floor = cheapest_price(case, sheet, std, sid)
    limit = total_cost(case, {**sheet, "unit_price": floor + 4}, std)  # hosted P02 repeated the floor
    price = RoutedAgent._price_within(case, sheet, std, sid, limit)
    assert floor + 3.99 <= price <= floor + 4.01
    assert total_cost(case, {**sheet, "unit_price": price}, std) <= limit
    below = total_cost(case, {**sheet, "unit_price": floor - 5}, std)
    assert RoutedAgent._price_within(case, sheet, std, sid, below) == floor  # never below the health floor


def test_concession_schedule_moves_the_price_not_the_flat_health_cost():
    """Hosted P02 v6: 20 % of a gap in C including supplier health moved the ask 361.51 -> 366.94
    against a 373.88 counter; the schedule now moves 20 % of the price gap."""
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.play import ask_limit, known_offer

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid = "sup:70510"
    quote = [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\nIncoterm DAP"}]

    def sheet(price, pay):
        return {"unit_price": price, "payment_days": pay, "index_clause": None}

    events = [{"from": "supplier", "status": "open", "attachments": quote,
               "term_sheets": [{"term_sheet_id": "offer:0", "terms": sheet(370.96, 30)}]},
              {"from": "buyer", "term_sheets": [{"terms": sheet(361.51, 90)}]},
              {"from": "supplier", "status": "open", "attachments": quote,
               "term_sheets": [{"term_sheet_id": "offer:1", "terms": sheet(373.88, 90)}]}]
    limit = ask_limit(case, events, sid)
    std = known_offer(case, events)[2]
    price = RoutedAgent._price_within(case, {**sheet(373.88, 90)}, std, sid, limit)
    assert 363.5 <= price <= 364.5  # 361.51 + 0.2 x 12.37


def test_opening_offers_the_suppliers_lead_time_at_equal_cost():
    """Hosted P10: one 42-day sheet drew a 33.8 final offer (0.555); with the supplier's 56 days at an
    equal-cost price it answered 31.6 (0.671). Scripts and model sheets both carry that variant."""
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import decision_cost
    from tacto_negotiator.play import known_offer

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P10"])
    case["_supplier_estimates"] = DataPackage(ROOT / "data").supplier_estimates(case)
    sid = "sup:70410"
    opening = {"unit_price": 32.2, "lead_time_days": 54, "volume_per_year": 19890, "contract_months": 12}
    quote = [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\nIncoterm DAP"}]
    threads = {sid: [{"from": "supplier", "status": "open", "attachments": quote,
                      "term_sheets": [{"term_sheet_id": "offer:0", "terms": opening}]}]}
    agent = RoutedAgent(None)
    scripted = agent._structured_bargain(case, threads)[1]["message"]["term_sheets"]
    model = agent.validate(case, threads, {"supplier_id": sid, "action": "send", "term_sheets": [
        {"unit_price": 26.3, "volume_per_year": 23400, "contract_months": 36, "lead_time_days": 42}]})
    for sheets in (scripted, model["message"]["term_sheets"]):
        terms = [sheet["terms"] for sheet in sheets]
        slow = [t for t in terms if t["lead_time_days"] == 56]
        assert slow and all(t["volume_per_year"] >= 22230 and t["contract_months"] >= 24 for t in terms)
        fast = [t for t in terms if t["lead_time_days"] == 42]
        assert slow[0]["unit_price"] < min(t["unit_price"] for t in fast)
        std = known_offer(case, threads[sid])[2]
        assert decision_cost(case, slow[0], std, sid) <= min(decision_cost(case, t, std, sid) for t in fast) * 1.03


def test_an_equal_cost_sheet_at_an_implausible_price_is_not_sent():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P18"])
    sid = case["suppliers"][0]
    opening = {"from": "sales@70740", "status": "open", "body": "Our quote.",
               "term_sheets": [{"term_sheet_id": "offer:0", "terms": {
                   "unit_price": 6.1, "payment_days": 30, "index_clause": None, "contract_months": 12}}],
               "attachments": [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30"}]}
    ours = {"from": "buyer", "body": "Our package.", "term_sheets": [{"terms": {
        "unit_price": 4.57, "payment_days": 90, "index_clause": None, "contract_months": 24}}]}
    counter = {**opening, "term_sheets": [{"term_sheet_id": "offer:1", "terms": {
        "unit_price": 5.4, "payment_days": 90, "index_clause": None, "contract_months": 24}}]}
    threads = {sid: [opening, ours, counter]}
    long = {"unit_price": 4.75, "payment_days": 90, "index_clause": None, "contract_months": 24}
    short = {**long, "contract_months": 12}  # hosted P18 v8: equal cost only because demand goes uncovered
    low, high = 0.5, 4.75
    for _ in range(40):
        mid = (low + high) / 2
        fits = total_cost(case, {**short, "unit_price": mid}) <= total_cost(case, long)
        low, high = (mid, high) if fits else (low, mid)
    short["unit_price"] = round(low, 2)
    move = RoutedAgent(None).validate(case, threads, {"supplier_id": sid, "action": "send", "body": "Packages.",
                                                      "term_sheets": [long, short]})
    prices = [s["terms"]["unit_price"] for s in move["message"]["term_sheets"]]
    assert short["unit_price"] < 0.9 * long["unit_price"] and min(prices) >= 0.9 * max(prices)


def test_scripted_opening_covers_the_need_when_that_is_cheaper_at_our_ask():
    """Hosted P08/P18: openings on the quoted 12-month term drew 5.99/6.00 against 6.05/6.10 quotes,
    24/36-month openings 5.40-5.55. At the quote price 12 months looked cheapest (fewer units at it)."""
    from tacto_negotiator.agents.routed import RoutedAgent

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P08"])
    sid = case["suppliers"][0]
    opening = {"from": "sales@70710", "status": "open", "body": "Our quote.",
               "term_sheets": [{"term_sheet_id": "offer:0", "terms": {
                   "unit_price": 6.05, "payment_days": 30, "index_clause": None, "contract_months": 12}}],
               "attachments": [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30"}]}
    reason, move = RoutedAgent(None)._structured_bargain(case, {sid: [opening]})
    terms = [sheet["terms"] for sheet in move["message"]["term_sheets"]]
    assert reason == "enumerated_opening"
    assert all(t["contract_months"] >= 24 for t in terms) and max(t["unit_price"] for t in terms) <= 4.54
def test_relationship_opening_leads_with_an_index_clause_at_its_health_floor():
    """Hosted P02: with a 5 % deadband copper clause the supplier countered 349.05 (anchor run); without
    one it never went below 371.95 (0.39-0.50). The clause sheet at its health floor scores about 0.72."""
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.cost import cheapest_price, decision_cost, score
    from tacto_negotiator.play import known_offer

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    sid = "sup:70510"
    result = next(s for s in feedback["sessions"] if s["case_id"] == "case:P02")["result"]
    quote = [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\nIncoterm DAP"}]
    opening = {"unit_price": 370.96, "payment_days": 30, "index_clause": None}
    events = [{"from": "supplier", "status": "open", "attachments": quote,
               "term_sheets": [{"term_sheet_id": "offer:0", "terms": opening}]}]
    threads = {sid: events}
    agent = RoutedAgent(None, package=DataPackage(ROOT / "data"))
    agent._prepare(case)
    std = known_offer(case, events)[2]
    model = agent.validate(case, threads, {"supplier_id": sid, "action": "send",
                                           "term_sheets": [{"unit_price": 361.51, "payment_days": 90}]})
    for move in (agent._structured_bargain(case, threads)[1], model):
        lead = move["message"]["term_sheets"][0]["terms"]
        assert lead["index_clause"]["deadband_pct"] == 5 and lead["payment_days"] == 90
        assert lead["unit_price"] == cheapest_price(case, lead, std, sid)
        assert score(result["c_open"], decision_cost(case, lead, std, sid), result["c_best"]) > 0.7
    # offered once; when the supplier holds a clause itself the schedule takes over
    plain = {**lead, "index_clause": None}
    countered = [*events, {"from": "buyer", "term_sheets": model["message"]["term_sheets"]},
                 {"from": "supplier", "status": "open", "attachments": quote, "term_sheets": [
                     {"term_sheet_id": "offer:1", "terms": {**opening, "unit_price": 373.88, "payment_days": 90}}]}]
    assert agent._clause_sheet(case, countered, sid, [plain]) is None
    held = [{**events[0], "term_sheets": [{"term_sheet_id": "offer:0",
                                           "terms": {**opening, "index_clause": lead["index_clause"]}}]}]
    assert agent._clause_sheet(case, held, sid, [plain]) is None


def test_small_index_rises_and_other_goals_get_no_clause_sheet():
    """Hosted P08/P18 steel rose 5.9 % against a 2 % expected clause cost; clause sheets moved nothing."""
    from tacto_negotiator.agents.routed import RoutedAgent

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    agent = RoutedAgent(None, package=DataPackage(ROOT / "data"))
    p02, p08 = feedback["cases"]["case:P02"], feedback["cases"]["case:P08"]
    assert agent._clause_pays(p02, p02["term_options"]["index_clause"][-1])
    assert not agent._clause_pays(p08, p08["term_options"]["index_clause"][-1])
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"term_sheet_id": "offer:0", "terms": {
        "unit_price": 370.96, "payment_days": 30, "index_clause": None}}]}]
    sheet = {"unit_price": 361.51, "payment_days": 90, "index_clause": None}
    routine = {**p02, "buyer": {**p02["buyer"], "category_goal": "routine"}}
    assert agent._clause_sheet(routine, events, "sup:70510", [sheet]) is None


def test_secure_supply_openings_stay_at_the_shallow_anchor():
    """Hosted P10 gave a round-1 final offer to every first ask 13-20 % below its 32.20 quote and stayed
    open at 8-10 % (29.2 on its own 56 days drew 31.6, 0.671). Model sheets at 26.2/25.8 are lifted to the
    anchored price; the supplier's lead time comes back as an equal-cost trade. Other goals keep deep asks."""
    from tacto_negotiator.agents.routed import RoutedAgent

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P10"])
    sid = "sup:70410"
    quote = [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\nIncoterm DAP"}]
    opening = {"unit_price": 32.2, "lead_time_days": 54, "volume_per_year": 19890, "contract_months": 12}
    threads = {sid: [{"from": "supplier", "status": "open", "attachments": quote,
                      "term_sheets": [{"term_sheet_id": "offer:0", "terms": opening}]}]}
    deep = [{"unit_price": 26.2, "volume_per_year": 23400, "contract_months": 36, "lead_time_days": 42},
            {"unit_price": 25.8, "volume_per_year": 23400, "contract_months": 36, "lead_time_days": 56}]
    agent = RoutedAgent(None, package=DataPackage(ROOT / "data"))
    agent._prepare(case)
    for move in (agent.validate(case, threads, {"supplier_id": sid, "action": "send", "term_sheets": deep}),
                 agent._structured_bargain(case, threads)[1]):
        sheets = [s["terms"] for s in move["message"]["term_sheets"]]
        assert min(s["unit_price"] for s in sheets) >= 0.9 * 32.2
        assert any(s["lead_time_days"] == 56 for s in sheets)
    routine = {**case, "buyer": {**case["buyer"], "category_goal": "routine"}}
    agent._prepare(routine)
    move = agent.validate(routine, threads, {"supplier_id": sid, "action": "send", "term_sheets": deep[:1]})
    assert move["message"]["term_sheets"][0]["terms"]["unit_price"] <= 26.2


def test_a_sheet_the_supplier_countered_moves_instead_of_repeating():
    """An unchanged sheet draws no concession (hosted P02 v4 repeated its floor and the supplier walked)."""
    from tacto_negotiator.agents.routed import RoutedAgent

    case = {"negotiable": ["unit_price", "payment_days"], "fixed_terms": {}, "buyer": {}}
    ours = {"unit_price": 361.42, "payment_days": 90}
    events = [{"from": "supplier", "status": "open", "term_sheets": [
                  {"term_sheet_id": "offer:0", "terms": {"unit_price": 370.96, "payment_days": 30}}]},
              {"from": "buyer", "term_sheets": [{"terms": ours}]},
              {"from": "supplier", "status": "open", "term_sheets": [
                  {"term_sheet_id": "offer:1", "terms": {"unit_price": 365.0, "payment_days": 90}}]}]
    standing = events[-1]["term_sheets"][0]["terms"]
    moved = RoutedAgent._off_repeat(case, events, dict(ours), standing, {})
    assert moved["unit_price"] == round(361.42 + 0.2 * 3.58, 2)
    other = {"unit_price": 361.0, "payment_days": 60}  # another package keeps its scheduled price
    assert RoutedAgent._off_repeat(case, events, other, standing, {}) == other


def test_the_clause_sheet_carries_the_longest_payment_term():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.compete import unavailable

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P02"])
    agent = RoutedAgent(unavailable, package=DataPackage(ROOT / "data"))
    agent._prepare(case)
    sid = case["suppliers"][0]
    opening = next(s for s in feedback["sessions"] if s["case_id"] == "case:P02")["threads"][sid]["opening"]
    events = [{"from": "supplier", "status": "open", "body": "Quote.", "term_sheets": opening,
               "attachments": [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30"}]}]
    sheet = agent._clause_sheet(case, events, sid, [{"unit_price": 357.0, "payment_days": 30, "index_clause": None}])
    assert sheet["payment_days"] == 90  # hosted P02 v16 agreed to a 30-day clause sheet: 0.634 vs 0.735


def test_no_sheet_dearer_than_a_rival_standing_offer():
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.compete import unavailable
    from tacto_negotiator.cost import total_cost

    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    case = dict(feedback["cases"]["case:P05"])
    agent = RoutedAgent(unavailable, package=DataPackage(ROOT / "data"))
    agent._prepare(case)

    def quote(ppm):
        return [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 30\n"
                 f"Lead time (days) 28\nQuality commitment (ppm) {ppm}\nChargeback per complaint (EUR) 0"}]

    def offer(sid, n, price):
        return {"from": "supplier", "status": "open", "body": "Our offer.", "term_sheets": [
            {"term_sheet_id": f"offer:{sid[4:]}-{n}", "terms": {"unit_price": price, "payment_days": 30,
                                                                "lead_time_days": 28}}]}

    def ask(price):
        return {"from": "buyer", "term_sheets": [{"terms": {"unit_price": price, "payment_days": 30,
                                                            "lead_time_days": 28}}]}

    rival = [{**offer("sup:70210", 0, 36.0), "attachments": quote(1200)}, ask(27.0), offer("sup:70210", 1, 28.5)]
    bidder = [{**offer("sup:70250", 0, 38.5), "attachments": quote(600)}, ask(28.88), offer("sup:70250", 1, 31.0)]
    opening = [{**offer("sup:70240", 0, 33.0), "attachments": quote(1400)}]
    threads = {"sup:70210": rival, "sup:70250": bidder, "sup:70240": opening}
    # Hosted P05 v17: the model offered 29.18 at 14 days while 70210 stood at 28.50 at 30 days; 70250 agreed.
    move = agent.validate(case, threads, {"supplier_id": "sup:70250", "action": "send", "body": "Our proposal.",
                                          "term_sheets": [{"unit_price": 29.18, "payment_days": 14,
                                                           "lead_time_days": 28}]})
    std = {"payment_days": 30, "lead_time_days": 28, "ppm_commitment": 600, "chargeback_eur_per_complaint": 0.0}
    rival_cost = total_cost(case, {"unit_price": 28.5, "payment_days": 30, "lead_time_days": 28},
                            {**std, "ppm_commitment": 1200})
    last_ask = total_cost(case, {"unit_price": 28.88, "payment_days": 30, "lead_time_days": 28}, std)
    assert last_ask > rival_cost  # the rival has since undercut our last ask: hold it, do not concede
    sheets = move["message"]["term_sheets"]
    assert sheets and all(total_cost(case, s.get("terms", s), std) <= last_ask + 1 for s in sheets)
