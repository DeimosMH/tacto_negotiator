"""Local review probes: no credentials, environment-file loading, HTTP or model calls.

Run from /tmp with the root .venv Python and an empty environment. Changes to
objects below are temporary instrumentation, not edits to application files.
"""

import copy
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

from tacto_negotiator import docs
from tacto_negotiator.agents.llm import LLMAgent
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.cost import must_haves_ok
from tacto_negotiator.play import closing_move
from tacto_negotiator.sim import Sim, load_cases

ROOT = Path(__file__).resolve().parents[2]


def guards():
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    sim = Sim({case["case_id"]: case})
    opened = sim.open("local", case["case_id"])
    sid = case["suppliers"][0]
    threads = copy.deepcopy(opened["threads"])
    # Keep the real 14-day supplier default private; the buyer has no evidence of it.
    threads[sid][0]["attachments"] = []
    decision = {"supplier_id": sid, "action": "accept"}
    legacy_move = LLMAgent(lambda *_: json.dumps(decision)).decide(case, threads)
    default_move = closing_move(case, threads)
    routed_move = RoutedAgent(None).closing_move(case, threads)
    actual = sim.message("local", opened["session_id"], sid, legacy_move["message"])["result"]
    observed = {
        "legacy": legacy_move["message"]["action"]["type"],
        "default_closer": default_move["message"]["action"]["type"],
        "routed_closer": routed_move["message"]["action"]["type"],
        "actual_payment_days": actual["terms"]["payment_days"],
        "must_haves_ok": actual["must_haves_ok"],
        "score": actual["score"],
    }
    assert observed == {
        "legacy": "accept", "default_closer": "accept", "routed_closer": "walk_away",
        "actual_payment_days": 14, "must_haves_ok": False, "score": 0.0,
    }
    return observed


def catalog():
    sys.path.insert(0, str(ROOT / "docs"))
    import build

    class Tracked(dict):
        def __init__(self, value):
            super().__init__(value)
            self.used = set()

        def get(self, key, default=None):
            if key in self:
                self.used.add(key)
            return super().get(key, default)

    original = build.SYMBOLS
    tracked = Tracked(original)
    try:
        build.SYMBOLS = tracked
        before = build.serialize(build.generate())
        unused = sorted(set(tracked) - tracked.used)
        build.SYMBOLS = {key: value for key, value in tracked.items() if key not in unused}
        after = build.serialize(build.generate())
    finally:
        build.SYMBOLS = original
    assert len(unused) == 25
    assert before == after
    return {
        "unused_symbol_count": len(unused), "unused_symbols": unused,
        "same_output_after_deletion": before == after,
        "serialized_sha256": hashlib.sha256(before.encode()).hexdigest(),
    }


def repeated_candidate_work():
    case = copy.deepcopy(load_cases()["case:S01"])
    case["negotiable"].append("contract_months")
    case["fixed_terms"].pop("contract_months")
    case["term_options"] = {
        "payment_days": list(range(14, 30)), "contract_months": list(range(1, 17)),
    }
    sid = case["suppliers"][0]
    threads = {sid: [{
        "from": "supplier", "status": "open", "body": "Our current quote.",
        "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 20}}],
        "attachments": [{
            "name": "quote.pdf",
            "text": "Our standard terms\nPayment terms (days net) 14\nContract term (months) 12",
        }],
    }]}
    current, candidate_only = RoutedAgent(None), RoutedAgent(None)
    full_validate = candidate_only.validate

    def check_candidate(case_, threads_, decision):
        # This exact call shape belongs to _packages in this fixture. The final
        # action, which includes its body, still passes the original validator.
        if set(decision) == {"supplier_id", "action", "term_sheets"}:
            return must_haves_ok(case_, decision["term_sheets"][0])
        return full_validate(case_, threads_, decision)

    candidate_only.validate = check_candidate
    outputs = {}
    for name, agent in (("current", current), ("candidate_only_check", candidate_only)):
        with patch.object(docs, "quote_terms", wraps=docs.quote_terms) as parsing:
            move = agent.decide(case, threads)
            count = parsing.call_count
        elapsed = []
        for _ in range(30):
            start = time.perf_counter()
            agent.decide(case, threads)
            elapsed.append((time.perf_counter() - start) * 1000)
        outputs[name] = {
            "quote_parses": count, "median_ms": round(statistics.median(elapsed), 3), "move": move,
        }
    assert outputs["current"]["quote_parses"] == 259
    assert outputs["candidate_only_check"]["quote_parses"] == 3
    assert outputs["current"]["move"] == outputs["candidate_only_check"]["move"]
    return {
        "candidate_count": 256, "same_move": True,
        "measurements": {name: {key: value for key, value in row.items() if key != "move"}
                         for name, row in outputs.items()},
    }


if __name__ == "__main__":
    print(json.dumps({"guard_divergence": guards(), "catalog": catalog(),
                      "candidate_work": repeated_candidate_work()}, indent=2))
