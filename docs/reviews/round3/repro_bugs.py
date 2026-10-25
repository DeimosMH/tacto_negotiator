"""Round 3 bug probes, offline: no keys, no network, no arena, no paid model calls.

Run: .venv/bin/python docs/reviews/round3/repro_bugs.py
Each line prints the behaviour at HEAD (d842b8e) for one finding in BUGS.md.
"""

import copy
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TACTO_DEALS_DIR"] = "off"  # never write the deal journal from a probe
logging.disable(logging.CRITICAL)

import importlib  # noqa: E402

play_mod = importlib.import_module("tacto_negotiator.play")
from tacto_negotiator.agents.routed import RoutedAgent  # noqa: E402
from tacto_negotiator.client import ArenaError  # noqa: E402
from tacto_negotiator.cost import must_haves_ok, total_cost  # noqa: E402
from tacto_negotiator.sim import load_cases  # noqa: E402


def offer(sid, n, terms, status="open", **extra):
    return {"from": sid, "status": status, "body": "Our offer.", "attachments": [],
            "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-{n}", "terms": terms}], **extra}


def s02():
    case = copy.deepcopy(load_cases()["case:S02"])
    case["buyer"]["must_haves"] = [{"field": "lead_time_days", "op": "<=", "value": 42}]
    return case


# ---------------------------------------------------------------------------------------------
def b1_opening_limit_counts_openings_that_break_must_haves():
    """_opening_limit takes the cheapest opening of ANY bidder, also one that breaks a must-have.
    api.md: C_open is the cheapest opening among those that keep the must-haves."""
    case = s02()
    a, b = case["suppliers"]
    std = {"payment_days": 30, "lead_time_days": 42}
    threads = {
        a: [offer(a, 0, {"unit_price": 16.00, "payment_days": 30, "lead_time_days": 42})],
        b: [offer(b, 0, {"unit_price": 13.70, "payment_days": 30, "lead_time_days": 49})],  # breaks lead <= 42
    }
    agent = RoutedAgent(lambda *a, **k: "")
    # our ask 13.50 to A, then A counters at 15.40 (ask_limit: 13.50 + 0.2 x 1.90 = 13.88): what may our second ask to A be?
    threads[a] += [{"from": "buyer", "body": "x", "term_sheets": [
        {"terms": {"unit_price": 13.50, "payment_days": 30, "lead_time_days": 42}}]},
        offer(a, 1, {"unit_price": 15.40, "payment_days": 30, "lead_time_days": 42})]
    b_ok = must_haves_ok(case, threads[b][0]["term_sheets"][0]["terms"])
    limit = agent._opening_limit(case, threads)
    b_cost = total_cost(case, threads[b][0]["term_sheets"][0]["terms"])
    move = agent.validate(case, threads, {"supplier_id": a, "action": "send", "body": "Please consider.",
                                          "term_sheets": [{"unit_price": 13.88, "payment_days": 30,
                                                           "lead_time_days": 42}]})
    sent = move["message"]["term_sheets"][0]["terms"]["unit_price"] if move else None
    agent._opening_limit = lambda *args: float("inf")  # the same move with the limit off
    free = agent.validate(case, threads, {"supplier_id": a, "action": "send", "body": "Please consider.",
                                          "term_sheets": [{"unit_price": 13.88, "payment_days": 30,
                                                           "lead_time_days": 42}]})
    free = free["message"]["term_sheets"][0]["terms"]["unit_price"] if free else None
    a_open = total_cost(case, threads[a][0]["term_sheets"][0]["terms"])
    print(f"B1 _opening_limit: B's opening keeps must-haves={b_ok}; limit={limit:.0f} "
          f"(0.999 x B's {b_cost:.0f}; A's compliant opening {a_open:.0f} is the real C_open); "
          f"ask 13.88 to A goes out at {sent} (without _opening_limit: {free})")


# ---------------------------------------------------------------------------------------------
class FakeTable:
    """A one-supplier session; scripted send/close outcomes."""

    def __init__(self, case, threads, on_send=None, on_close=None, missed=None):
        self.case, self.threads = case, threads
        self.on_send, self.on_close, self._missed = on_send, on_close, missed
        self.closed = []

    def open(self, case_id):
        return self.case, self.threads

    def send(self, sid, message):
        return self.on_send(sid, message)

    def close(self, sid, message):
        self.closed.append(message)
        return self.on_close(sid, message)

    def missed(self, sid, error):
        return self._missed(sid, error)

    def result(self):
        return {"deal": False, "end_reason": "probe"}


def b2_stale_accept_does_not_count_the_timeout():
    """After the 422 'not a standing offer' (a timeout answer replaced the offer), the runner sets
    received[sid] = now and never adds the timeout to `lapses`, unlike the 409 'late' path."""
    case = {"case_id": "case:T", "suppliers": ["sup:a"], "negotiable": ["unit_price"], "round_limit": 10,
            "buyer": {"category_goal": "routine", "alternative": {"kind": "none"}}}
    seen = {}

    def close(sid, message):
        if message["action"]["term_sheet_id"] == "offer:a-1":
            raise ArenaError(422, "not a standing offer in this session")
        return {"deal": True}

    def missed(sid, error):  # the thread now holds the timeout answer: same terms, new offer id
        return {"messages": [offer("sup:a", 1, {"unit_price": 10.0}, "final_offer"),
                             offer("sup:a", 2, {"unit_price": 10.0}, "final_offer")]}

    class Agent:
        def decide(self, case, threads):
            return {"supplier_id": "sup:a", "message": {"action": {
                "type": "accept", "term_sheet_id": play_mod.standing(threads["sup:a"])[0]}}}

    # spy on the runner's lapse bookkeeping
    real = play_mod.mark_lapse

    def spy(case, events, count=1):
        seen["mark_lapse"] = seen.get("mark_lapse", 0) + count
        return real(case, events, count)

    play_mod.mark_lapse = spy
    try:
        table = FakeTable(case, {"sup:a": [offer("sup:a", 1, {"unit_price": 10.0}, "final_offer")]},
                          on_close=close, missed=missed)
        out = play_mod._play(table, "case:T", Agent())
    finally:
        play_mod.mark_lapse = real
    print(f"B2 422 stale accept: retried and closed={out.get('deal')}, but timeouts recorded by the runner="
          f"{seen.get('mark_lapse', 0)} (the replaced offer proves one happened; the 409 'late' path counts it)")


def b3_a_422_on_a_message_ends_play_with_offers_standing():
    """Any 422 on a non-accept message propagates out of play(): the case is lost (no deal), although an
    acceptable offer stands and rounds are left."""
    case = copy.deepcopy(load_cases()["case:S01"])
    sid = case["suppliers"][0]
    threads = {sid: [offer(sid, 1, {"unit_price": 20.0, "payment_days": 30})]}

    def send(s, message):
        raise ArenaError(422, "value outside the allowed options")  # e.g. a supplier-standard value

    agent = RoutedAgent(lambda *a, **k: "")
    table = FakeTable(case, threads, on_send=send)
    try:
        play_mod._play(table, case["case_id"], agent)
        outcome = "play returned"
    except ArenaError as e:
        outcome = f"play raised ArenaError {e.status} -> case ends without a deal"
    acceptable = play_mod.acceptable_offer(case, threads[sid], sid)
    print(f"B3 422 on a send: {outcome}; acceptable offer standing={bool(acceptable)}, "
          f"moves left={play_mod.moves_left(case, threads[sid])}")


def b4_fast_mode_forced_on_every_anthropic_client():
    """LLM_SPEED (default fast) is applied to the strong client and to any LLM_MODEL; fast mode exists for
    Opus 5.5/5/4.8 only, and only RateLimitError falls back to standard speed."""
    from tacto_negotiator.agents import completion

    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({"LLM_PROVIDER": "anthropic", "LLM_API_KEY_ANTHROPIC": "dummy-not-used",
                           "LLM_MODEL": "claude-sonnet-5-5", "LLM_STRONG_MODEL": "claude-sonnet-5-5"})
        fast, strong = completion.routed_completions_from_env()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    import inspect
    src = inspect.getsource(completion.AnthropicCompletionClient._request)
    caught = [n for n in ("RateLimitError", "BadRequestError", "InternalServerError", "APIStatusError") if n in src]
    print(f"B4 fast mode: model={fast.model} speed={fast.speed}; strong={strong.model} speed={strong.speed}; "
          f"fast-mode errors that fall back to standard: {caught}")


def b5_strip_wasted_claims_drops_rival_quote_for_second_bidder():
    """_strip_wasted_claims treats every ID cited in any thread as used; api.md lets a rival's quote be
    cited once per supplier."""
    case = copy.deepcopy(load_cases()["case:S02"])
    a, b = case["suppliers"]
    threads = {a: [offer(a, 1, {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 42}),
                   {"from": "buyer", "body": "B offers EUR 15 (offer:70360-1).", "term_sheets": []}],
               b: [offer(b, 1, {"unit_price": 15.0, "payment_days": 30, "lead_time_days": 42})]}
    out = RoutedAgent._strip_wasted_claims(case, threads, "A rival offers EUR 16 (offer:70340-1).")
    threads[a][1]["body"] = "Quote (offer:70340-1)."  # A's own offer was cited to A
    out2 = RoutedAgent._strip_wasted_claims(case, threads, "A competing offer is EUR 16 (offer:70340-1).")
    print(f"B5 _strip_wasted_claims: first cite to B kept={out!r}; after it was cited in A's thread -> {out2!r}")


def b6_local_testing_command_shadowed_by_tmp_module():
    """docs/LOCAL_TESTING.md runs pytest from /tmp; python -m puts the cwd first on sys.path, so any
    /tmp/<module>.py (here /tmp/h2.py, imported by httpx) breaks the suite."""
    shadow = [p.name for p in Path("/tmp").glob("*.py") if p.stem in {"h2", "httpx", "anthropic", "json", "csv"}]
    print(f"B6 test command from /tmp: shadowing modules present in /tmp={shadow}")


if __name__ == "__main__":
    t = time.time()
    b1_opening_limit_counts_openings_that_break_must_haves()
    b2_stale_accept_does_not_count_the_timeout()
    b3_a_422_on_a_message_ends_play_with_offers_standing()
    b4_fast_mode_forced_on_every_anthropic_client()
    b5_strip_wasted_claims_drops_rival_quote_for_second_bidder()
    b6_local_testing_command_shadowed_by_tmp_module()
