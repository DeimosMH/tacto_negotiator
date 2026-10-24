"""Local reproductions for BUGS.md, against the reviewed code at 4aca02d.

Each assertion confirms the current defect; these are not passing safety tests.
No dotenv, key file, model, hosted arena, or network transport is used.
"""

import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.client import ArenaError
from tacto_negotiator.competition import build_scenarios
from tacto_negotiator.cost import total_cost
from tacto_negotiator.docs import read_document, standard_terms
from tacto_negotiator.play import RemoteTable, play
from tacto_negotiator.sim import Fail, Sim, load_cases


def no_model(*args, **kwargs):
    raise AssertionError("a reproduction attempted a model call")


def stale_timeout_acceptance():
    """Resume, exhaust rounds after a timeout, then accept using the stale timeout price."""
    runner = importlib.import_module("tacto_negotiator.play")
    now = [100.0]

    def clock():
        return now[0]

    case = copy.deepcopy(load_cases()["case:S01"])
    case["round_limit"] = 3
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 20, "payment_days": 14}}

    class ChangedQuoteSim(Sim):
        def _answer(self, session, thread, message, sheets):
            return self._mail(session, thread, "open", {"unit_price": 25, "payment_days": 14},
                              "New price after a timeout.")

    sim = ChangedQuoteSim({case["case_id"]: case}, clock=clock)
    opened = sim.open("local", case["case_id"])
    now[0] += 61
    sim.session("local", opened["session_id"])
    rejected = []

    class LocalClient:
        """An in-memory ArenaClient replacement, including its error conversion."""

        def open_session(self, case_id):
            raise ArenaError(409, "one session per case in the final: " + opened["session_id"])

        def session(self, session_id, **kwargs):
            return copy.deepcopy(sim.state("local", session_id))

        def thread(self, session_id, supplier_id, **kwargs):
            return copy.deepcopy(sim.thread("local", session_id, supplier_id))

        def attachment(self, session_id, attachment_id, **kwargs):
            return sim.attachment("local", session_id, attachment_id)[0]

        def send_message(self, session_id, supplier_id, message):
            try:
                return copy.deepcopy(sim.message("local", session_id, supplier_id, message))
            except Fail as error:
                rejected.append(error.detail)
                raise ArenaError(error.status, error.detail) from None

    model_calls = []

    def model(*args, **kwargs):
        model_calls.append(True)
        return no_model(*args, **kwargs)

    with patch.object(runner, "time", SimpleNamespace(time=clock, monotonic=clock)):
        result = play(RemoteTable(LocalClient(), case), case["case_id"], RoutedAgent(model, clock=clock))
    assert not model_calls
    assert rejected == ["round limit reached: accept the standing offer or walk away"]
    assert result["deal"] and result["terms"]["unit_price"] == 25
    assert result["c_deal"] > result["c_alt"]
    return {"accepted_price": result["terms"]["unit_price"], "alternative_price": 20,
            "c_deal": result["c_deal"], "c_alt": result["c_alt"], "model_calls": len(model_calls),
            "rejected_move": rejected[0]}


def rewritten_standard_terms():
    """A legal 90-day supplier standard is changed to a worse 30-day wire term."""
    case = copy.deepcopy(load_cases()["case:S01"])
    supplier = case["suppliers"][0]
    case["term_options"]["payment_days"] = [14, 30]
    threads = {supplier: [{
        "from": "supplier", "status": "open",
        "term_sheets": [{"term_sheet_id": "offer:A", "terms": {"unit_price": 20}}],
        "attachments": [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 90"}],
    }]}
    proposed = {"unit_price": 18, "payment_days": 90}
    move = RoutedAgent(no_model).validate(case, threads, {
        "supplier_id": supplier, "action": "send", "term_sheets": [proposed],
    })
    actual = move["message"]["term_sheets"][0]["terms"]
    assert proposed["payment_days"] == 90 and actual["payment_days"] == 30
    assert total_cost(case, actual) > total_cost(case, proposed)
    payment_result = {"proposed": proposed, "wire": actual, "planned_cost": total_cost(case, proposed),
                      "wire_cost": total_cost(case, actual)}

    # The independent logic review supplied this full script-path example.
    freight_case = next(s.case for s in build_scenarios() if s.name == "payment_freight")
    freight_case["term_options"]["incoterm"] = ["EXW"]
    freight_case["buyer"]["freight_eur_per_unit"]["EXW"] = 10
    alternative = {"unit_price": 30, "incoterm": "DAP", "payment_days": 60}
    freight_case["buyer"]["alternative"] = {"kind": "quote", "terms": alternative}
    threads = {"sup:A": [{
        "from": "sup:A", "status": "open", "body": "Our offer.",
        "term_sheets": [{"term_sheet_id": "offer:1", "terms": {
            "unit_price": 100, "payment_days": 14, "incoterm": "DAP",
        }}],
        "attachments": [{"name": "quote.pdf", "text":
                         "Our standard terms\nIncoterm DAP\nPayment terms (days net) 14"}],
    }]}
    agent = RoutedAgent(no_model)
    move = agent.decide(freight_case, threads)
    sent = [sheet["terms"] for sheet in move["message"]["term_sheets"]]
    sent_costs = [total_cost(freight_case, terms) for terms in sent]
    alternative_cost = total_cost(freight_case, alternative)
    assert agent.last_decision["route"] == "script" and not agent.last_decision["fallback"]
    assert all(terms["incoterm"] == "EXW" for terms in sent)
    assert min(sent_costs) > alternative_cost
    return {"payment_rewrite": payment_result, "scripted_freight_rewrite": {
        "wire_packages": sent, "wire_costs": sent_costs, "alternative_cost": alternative_cost,
    }}


def index_clause_disappears():
    """Use the real simulator PDF producer/parser and obtain a zero-score forbidden deal."""
    case = copy.deepcopy(load_cases()["case:S01"])
    supplier = case["suppliers"][0]
    case["negotiable"].append("index_clause")
    del case["fixed_terms"]["index_clause"]
    clause = {"series": "S1", "material_share": 0.5, "deadband_pct": 0, "base_month": "2026-09"}
    case["term_options"]["index_clause"] = [clause, None]
    case["buyer"]["must_haves"] = [{"field": "index_clause", "op": "==", "value": None}]
    case["buyer"]["index_paths"] = {"S1": 0.2}
    sim = Sim({case["case_id"]: case}, no_clock=True)
    opened = sim.open("local", case["case_id"])
    threads = opened["threads"]
    for events in threads.values():
        for message in events:
            for attachment in message["attachments"]:
                content, _ = sim.attachment("local", opened["session_id"], attachment["attachment_id"])
                attachment["text"] = read_document(attachment["name"], content)
    parsed = standard_terms(threads[supplier])
    move = RoutedAgent(no_model).closing_move(case, threads)
    result = sim.message("local", opened["session_id"], move["supplier_id"], move["message"])["result"]
    assert parsed["index_clause"] is None
    assert move["message"]["action"]["type"] == "accept"
    assert result["deal"] and result["terms"]["index_clause"] == clause
    assert not result["must_haves_ok"] and result["score"] == 0
    return {"parsed_index": parsed["index_clause"], "actual_index": result["terms"]["index_clause"],
            "action": move["message"]["action"]["type"], "must_haves_ok": result["must_haves_ok"],
            "score": result["score"]}


def opening_no_bid_crashes():
    """All opening suppliers decline: read_deadline fails before result() can run."""
    case = copy.deepcopy(load_cases()["case:S01"])
    supplier = case["suppliers"][0]
    result_reads = []

    class NoBidTable:
        def open(self, case_id):
            return case, {supplier: [{"from": "supplier", "status": "no_bid",
                                      "term_sheets": [], "attachments": []}]}

        def result(self):
            result_reads.append(True)
            return {"ended": True, "deal": False, "end_reason": "no_deal"}

    try:
        play(NoBidTable(), case["case_id"], RoutedAgent(no_model))
    except ValueError as error:
        assert "min()" in str(error) and not result_reads
        return {"exception": type(error).__name__, "message": str(error), "result_reads": len(result_reads)}
    raise AssertionError("the reviewed all-no-bid defect no longer reproduces")


if __name__ == "__main__":
    for reproduce in (stale_timeout_acceptance, rewritten_standard_terms,
                      index_clause_disappears, opening_no_bid_crashes):
        print(json.dumps({"reproduction": reproduce.__name__, "observed": reproduce()}, sort_keys=True))
