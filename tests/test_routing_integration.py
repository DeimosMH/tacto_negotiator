"""Offline integration of routing, per-thread budgets, provider cancellation and final waves."""

import asyncio
import copy
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from test_final import Clock, wait_until
from test_final import arena as arena

from tacto_negotiator.agents.completion import CompletionClient
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.client import ArenaError
from tacto_negotiator.cost import complete_offer
from tacto_negotiator.play import RemoteTable, closing_move, moves_left, play, play_waves, standing, thread_events
from tacto_negotiator.sim import load_cases


class LocalTable:
    """A table with explicit document and supplier delays, without HTTP or provider access."""

    def __init__(self, clock, *, document_delay=0, supplier_delay=0):
        self.clock, self.document_delay, self.supplier_delay = clock, document_delay, supplier_delay
        self.case = copy.deepcopy(load_cases()["case:S02"])
        self.sent, self.closed = [], []
        self.threads = {sid: [{"from": sid, "status": "open", "term_sheets": [
            {"term_sheet_id": f"offer:{sid}", "terms": {"unit_price": 20, "payment_days": 30,
                                                       "lead_time_days": 21}}]}]
                        for sid in self.case["suppliers"]}
        if document_delay:
            next(iter(self.threads.values()))[0]["attachments"] = [{"name": "quote.md", "attachment_id": "quote"}]

    def open(self, case_id):
        return self.case, self.threads

    def attachment(self, attachment_id, **options):
        self.clock.advance(self.document_delay)
        return b"Synthetic document delay."

    def send(self, sid, message):
        self.sent.append((sid, message))
        self.clock.advance(self.supplier_delay)
        return {"from": sid, "status": "open", "term_sheets": self.threads[sid][0]["term_sheets"]}

    def close(self, sid, message):
        self.closed.append((sid, message))
        return {"ended": True, "deal": False}


def runner_clock(monkeypatch, clock):
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"], "time",
                        SimpleNamespace(time=clock, monotonic=clock, sleep=clock.advance))


def walk(case, threads):
    return {"supplier_id": next(iter(threads)), "message": {"action": {"type": "walk_away"}}}


def test_opening_document_time_is_charged_to_decision_budget(monkeypatch):
    clock = Clock()
    runner_clock(monkeypatch, clock)
    seen = []

    class Observe:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            seen.append(deadline - clock())
            return walk(case, threads)

    table = LocalTable(clock, document_delay=45)
    play(table, table.case["case_id"], Observe())
    assert seen == [10]  # 60-second thread clock - 45 seconds reading - 5 seconds to send


def test_other_rfq_threads_keep_aging_during_a_supplier_reply(monkeypatch):
    clock = Clock()
    runner_clock(monkeypatch, clock)
    seen = []

    class Observe:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            seen.append(deadline - clock())
            if len(seen) == 1:
                return {"supplier_id": next(iter(threads)), "message": {"body": "Please clarify."}}
            return walk(case, threads)

    table = LocalTable(clock, supplier_delay=40)
    play(table, table.case["case_id"], Observe())
    assert seen == [55, 15]
    assert len(table.sent) == len(table.closed) == 1


def test_idle_rfq_threads_are_messaged_while_another_supplier_replies(monkeypatch):
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"], "CONCURRENT_BELOW_S", 61)  # every idle thread is due

    class Table(LocalTable):
        def __init__(self):
            super().__init__(Clock())
            self.lock, self.busy, self.overlap = threading.Lock(), set(), 0

        def send(self, sid, message):
            with self.lock:
                assert sid not in self.busy  # one pending message per thread
                self.busy.add(sid)
                self.overlap = max(self.overlap, len(self.busy))
                self.sent.append((sid, message))
            time.sleep(0.2)  # the supplier is writing its reply
            with self.lock:
                self.busy.discard(sid)
            return {"from": sid, "status": "open", "term_sheets": self.threads[sid][0]["term_sheets"]}

        def close(self, sid, message):
            assert not self.busy  # a closing action waits for every pending reply
            return super().close(sid, message)

    class Agent:
        def __init__(self):
            self.unavailable, self.decisions, self.concurrent = set(), 0, []

        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            self.decisions += 1
            if self.decisions == 1:
                return {"supplier_id": case["suppliers"][0], "message": {"body": "First."}}
            return walk(case, threads)

        def concurrent_move(self, case, threads):
            free = [sid for sid in threads if sid not in self.unavailable]
            self.concurrent.append(free)
            return ("idle_thread", {"supplier_id": free[0], "message": {"body": "Meanwhile."}}) if free else None

    table, agent = Table(), Agent()
    play(table, table.case["case_id"], agent)
    first, second = table.case["suppliers"]
    assert table.overlap == 2 and agent.concurrent == [[second]] and agent.decisions == 2
    assert sorted(sid for sid, _ in table.sent) == [first, second] and len(table.closed) == 1
    assert [m.get("body", m["from"]) for m in table.threads[second]] == [second, "Meanwhile.", second]


def test_a_late_custom_completion_cannot_submit_its_counteroffer(monkeypatch):
    clock = Clock()
    runner_clock(monkeypatch, clock)
    closed, decided = [], []

    class Late:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            decided.append(clock())
            clock.now = deadline + 0.1
            return {"supplier_id": next(iter(threads)), "message": {"body": "Late counter-offer"}}

        def closing_move(self, case, threads):
            closed.append(True)
            return walk(case, threads)

    table = LocalTable(clock)
    play(table, table.case["case_id"], Late())
    # Each late decision costs one timeout; the third in a row would end the threads, so it closes.
    assert len(decided) == 3 and closed == [True] and not table.sent and len(table.closed) == 1
    assert all(moves_left(table.case, events) == table.case["round_limit"] - 2 for events in table.threads.values())


def test_resumed_routed_case_uses_server_time_remaining(arena):
    arena.state.cases["case:S03"]["buyer"]["category_goal"] = "secure_supply"
    arena.state.rehearse(wait=0, cap=480, waves=1)
    client = arena.client()
    case = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S03")
    opened = client.open_session(case["case_id"])
    arena.clock.advance(40)
    budgets = []

    def fast(system, user, *, deadline):
        budgets.append(deadline - arena.clock())
        return json.dumps({"supplier_id": case["suppliers"][0], "action": "walk_away"})

    def strong(*args, **kwargs):
        pytest.fail("a resumed case with 15 seconds of model budget cannot use the strong route")

    agent = RoutedAgent(fast, strong, clock=arena.clock)
    table = RemoteTable(client, case)
    assert play(table, case["case_id"], agent, deadline=arena.clock() + 440)["ended"]
    assert budgets == [15] and table.session_id == opened["session_id"]
    assert len(arena.state.sessions) == 1


def test_closed_server_threads_are_removed_before_routing(arena):
    client = arena.client()
    table = RemoteTable(client)
    _, threads = table.open("case:S02")
    session = arena.state.sessions[table.session_id]
    first = next(iter(session["threads"]))
    session["threads"][first]["closed"] = "walked_away"
    deadlines = table.thread_deadlines(threads)
    assert first not in deadlines and threads[first][-1]["status"] == "walked_away"


def test_status_change_with_same_offer_id_refreshes_the_thread(arena):
    table = RemoteTable(arena.client())
    _, threads = table.open("case:S01")
    sid = next(iter(threads))
    offer_id = threads[sid][-1]["term_sheets"][0]["term_sheet_id"]
    current = arena.state.sessions[table.session_id]["threads"][sid]
    current["status"] = current["messages"][-1]["status"] = "final_offer"
    table.thread_deadlines(threads)
    assert threads[sid][-1]["status"] == "final_offer"
    assert threads[sid][-1]["term_sheets"][0]["term_sheet_id"] == offer_id
    reads = [r for r, _ in arena.requests if r.url.path.endswith(f"/threads/{sid}")]
    assert len(reads) == 1 and 0 < reads[0].extensions["timeout"]["read"] <= 10


def test_409_recovery_keeps_current_offer_terms_and_rounds(arena):
    table = RemoteTable(arena.client())
    case, _ = table.open("case:S01")
    sid = case["suppliers"][0]
    arena.clock.advance(61)
    arena.state.session("local-only-buyer", table.session_id)
    session = arena.state.sessions[table.session_id]
    thread = session["threads"][sid]
    thread["round"] += 1
    thread["messages"].append({"from": "buyer", "body": "A later offer"})
    current = arena.state._mail(session, thread, "open", {"unit_price": 25, "payment_days": 14}, "New price")
    current_id = current["term_sheets"][0]["term_sheet_id"]
    current["term_sheets"].insert(0, {"term_sheet_id": "offer:older", "terms": {"unit_price": 10}})
    recovered = table.missed(sid, ArenaError(409, "round limit reached"))["messages"]
    assert standing(recovered) == (current_id, {"unit_price": 25, "payment_days": 14})
    assert moves_left(case, recovered) == case["round_limit"] - 2
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 20, "payment_days": 14}}
    agent = RoutedAgent(None)
    assert agent.closing_move(case, {sid: recovered})["message"]["action"]["type"] == "walk_away"


def test_recovery_without_matching_offer_cannot_accept():
    case = load_cases()["case:S01"]
    sid = case["suppliers"][0]
    events = thread_events({"offer_id": "offer:new", "status": "open", "closed": None,
        "messages": [{"from": "supplier", "status": "open", "term_sheets": [
            {"term_sheet_id": "offer:old", "terms": {"unit_price": 10, "payment_days": 14}}]}],
        "timeouts": [{"offer_id": "offer:old", "terms": {"unit_price": 10, "payment_days": 14}}]})
    assert standing(events) == (None, {})
    assert RoutedAgent(None).closing_move(case, {sid: events})["message"]["action"]["type"] == "walk_away"


def test_recovery_keeps_the_current_offers_alternatives_after_its_standing_sheet():
    events = thread_events({"offer_id": "offer:2", "status": "final_offer", "closed": None, "messages": [
        {"from": "supplier", "status": "final_offer", "term_sheets": [
            {"term_sheet_id": "offer:2-a", "terms": {"unit_price": 9, "payment_days": 14}},
            {"term_sheet_id": "offer:2", "terms": {"unit_price": 10, "payment_days": 14}},
            {"term_sheet_id": "offer:1", "terms": {"unit_price": 11, "payment_days": 14}}]}]})
    assert [s["term_sheet_id"] for s in events[-1]["term_sheets"]] == ["offer:2", "offer:2-a"]


def test_resumed_timeout_rounds_do_not_authorize_extra_sends(arena, monkeypatch):
    case = arena.state.cases["case:S01"]
    case["round_limit"] = 3
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 20, "payment_days": 14}}
    arena.state.rehearse(wait=0, cap=480, waves=1)
    client = arena.client()
    final = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S01")
    opened = client.open_session(final["case_id"])
    arena.clock.advance(61)

    def answer(session, thread, message, sheets):
        return arena.state._mail(session, thread, "open", {"unit_price": 25, "payment_days": 14}, "New price")

    monkeypatch.setattr(arena.state, "_answer", answer)
    result = play(RemoteTable(client, final), final["case_id"], RoutedAgent(None, clock=arena.clock))
    assert result["ended"] and not result["deal"]
    session = arena.state.sessions[opened["session_id"]]
    assert next(iter(session["threads"].values()))["round"] <= 3
    assert not any(status == 409 for request, status in arena.requests
                   if request.url.path.endswith("/messages"))


@pytest.mark.parametrize("status", ["no_bid", "walked_away"])
def test_terminal_opening_returns_result_without_document_work(status):
    case = load_cases()["case:S01"]
    result = {"ended": True, "deal": False, "end_reason": "no_deal"}

    class TerminalTable:
        def open(self, case_id):
            return case, {case["suppliers"][0]: [{"from": "supplier", "status": status,
                "term_sheets": [], "attachments": [{"name": "quote.pdf", "attachment_id": "unused"}]}]}

        def result(self):
            return result

    assert play(TerminalTable(), case["case_id"], RoutedAgent(None)) is result


def test_transient_timer_read_failure_keeps_negotiating_on_local_deadlines(arena):
    # Hosted cases closed with a 0 s budget seconds after a reply when a slow refresh meant "now".
    reads = []

    def handle(request):
        if request.method == "GET" and request.url.path == "/v1/sessions/sim1":
            reads.append(request)
            return httpx.Response(503, json={"detail": "synthetic metadata outage"})
        return arena.handle(request)

    table = RemoteTable(arena.client(transport=handle, retries=3))
    result = play(table, "case:S02", RoutedAgent(lambda *args, **kwargs: "not JSON", clock=arena.clock))
    moves = [r for r, _ in arena.requests if r.url.path.endswith("/messages")]
    assert result["ended"] and len(moves) > 1 and reads
    assert all(0 < read.extensions["timeout"]["read"] <= 10 for read in reads)


@pytest.mark.parametrize("failed_document", ["opening", "reply"])
def test_unavailable_quote_documents_cannot_cause_unguarded_acceptance(arena, failed_document):
    case = arena.state.cases["case:S03"]
    case["buyer"]["category_goal"] = "secure_supply"
    sid = case["suppliers"][0]
    failed_reads, model_calls = [], []

    def handle(request):
        round_ = "-0-" if failed_document == "opening" else "-1-"
        if "/attachments/" in request.url.path and round_ in request.url.path:
            failed_reads.append(request)
            arena.clock.advance(1)
            raise httpx.ReadTimeout("synthetic quote timeout", request=request)
        return arena.handle(request)

    def complete(system, user, *, deadline):
        model_calls.append(True)
        if failed_document == "opening":
            return json.dumps({"supplier_id": sid, "action": "accept"})
        terms = complete_offer(case, {"unit_price": round(case["buyer"]["current_unit_price"] * 1.1, 2)})
        return json.dumps({"supplier_id": sid, "action": "send", "body": "Please confirm this package.",
                           "term_sheets": [terms]})

    result = play(RemoteTable(arena.client(transport=handle, retries=3)), case["case_id"],
                  RoutedAgent(complete, clock=arena.clock))
    assert result["ended"] and len(failed_reads) <= 1 and model_calls  # a failed read is not retried
    assert all(0 < r.extensions["timeout"]["read"] <= 10 for r in failed_reads)
    if result["deal"]:  # only an offer whose own quote was read may be accepted
        read = [r.url.path for r, status in arena.requests if "/attachments/" in r.url.path and status == 200]
        accepts = [json.loads(r.content)["message"]["action"]["term_sheet_id"] for r, _ in arena.requests
                   if r.url.path.endswith("/messages") and (json.loads(r.content)["message"].get("action") or {})
                   .get("type") == "accept"]
        accepted = accepts[-1]
        assert any(path.endswith(f"{accepted.split(':')[-1]}-quote") for path in read), (accepted, read)


def test_thread_deadline_closure_preserves_attempted_model_and_timeout(arena, monkeypatch):
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"].time, "sleep", arena.clock.advance)
    arena.state.cases["case:S03"]["buyer"]["category_goal"] = "secure_supply"
    arena.state.rehearse(wait=0, cap=480, waves=1)
    client = arena.client()
    case = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S03")
    opened = client.open_session(case["case_id"])
    arena.clock.advance(35)
    budgets = []

    def complete(system, user, *, deadline):
        budgets.append(deadline - arena.clock())
        arena.clock.now = deadline
        raise TimeoutError("synthetic exact-deadline timeout")

    agent = RoutedAgent(complete, clock=arena.clock, max_decision_seconds=60)  # the model uses the thread's time
    result = play(RemoteTable(client, case), case["case_id"], agent, deadline=arena.clock() + 445)
    assert result["ended"]
    # A missed thread deadline costs one timeout (api.md, Time); the third miss in a row closes.
    assert len(budgets) == 3 and budgets[0] == 20
    thread = next(iter(arena.state.sessions[opened["session_id"]]["threads"].values()))
    assert len(thread["late"]) == 2 and thread["round"] == 2
    assert agent.last_decision["route"] == "fast" and agent.last_decision["fallback"]
    assert agent.last_decision["reason"] == "TimeoutError"
    assert agent.last_decision["case_id"] == case["case_id"]
    assert agent.last_decision["closure"] == "deadline_close"


def _messages(arena):
    return [json.loads(r.content)["message"] for r, _ in arena.requests if r.url.path.endswith("/messages")]


def test_a_missed_thread_deadline_with_moves_left_costs_one_timeout_not_the_case(arena, monkeypatch):
    # F3 (docs/reviews/FLOW.md): a late decision - here a walk-away fallback - is not sent; the thread
    # times out once (api.md, Time) and the negotiation goes on.
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"].time, "sleep", arena.clock.advance)
    arena.state.rehearse(wait=0, cap=480, waves=1)
    client = arena.client()
    case = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S01")
    opened = client.open_session(case["case_id"])
    budgets = []

    class Agent:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            budgets.append(deadline - arena.clock())
            sid = next(iter(threads))
            if len(budgets) == 1:
                arena.clock.now = deadline + 0.1  # e.g. a model that answered too late
                return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}
            if len(budgets) == 2:
                return {"supplier_id": sid, "message": {"body": "Please review your price."}}
            return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}

    result = play(RemoteTable(client, case), case["case_id"], Agent(), deadline=arena.clock() + 480)
    thread = next(iter(arena.state.sessions[opened["session_id"]]["threads"].values()))
    assert len(budgets) == 3 and budgets[1] > 50  # a fresh 60 s window after the timeout
    assert [m.get("body") or m["action"]["type"] for m in _messages(arena)] == ["Please review your price.",
                                                                            "walk_away"]
    assert len(thread["late"]) == 1 and result["ended"] and not result["deal"]
    assert arena.state.sessions[opened["session_id"]]["end_reason"] == "walked_away"


def test_no_decision_time_left_waits_for_the_timeout_instead_of_closing(arena, monkeypatch):
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"].time, "sleep", arena.clock.advance)
    arena.state.rehearse(wait=0, cap=480, waves=1)
    client = arena.client()
    case = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S01")
    opened = client.open_session(case["case_id"])
    arena.clock.advance(57)  # resumed with 3 s on the thread: too little to decide or send
    budgets, closed = [], []

    class Agent:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            budgets.append(deadline - arena.clock())
            return {"supplier_id": next(iter(threads)), "message": {"action": {"type": "walk_away"}}}

        def closing_move(self, case, threads):
            closed.append(True)
            return closing_move(case, threads)

    play(RemoteTable(client, case), case["case_id"], Agent(), deadline=arena.clock() + 420)
    thread = next(iter(arena.state.sessions[opened["session_id"]]["threads"].values()))
    assert not closed and len(budgets) == 1 and budgets[0] > 50 and len(thread["late"]) == 1


def test_the_third_consecutive_miss_accepts_an_acceptable_offer(arena, monkeypatch):
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"].time, "sleep", arena.clock.advance)
    arena.state.rehearse(wait=0, cap=480, waves=1)
    client = arena.client()
    case = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S01")
    opened = client.open_session(case["case_id"])
    decided = []

    class Late:
        def decide_timed(self, case, threads, *, deadline, wave_seconds_left):
            decided.append(True)
            arena.clock.now = deadline + 0.1
            return {"supplier_id": next(iter(threads)), "message": {"body": "Too late."}}

    result = play(RemoteTable(client, case), case["case_id"], Late(), deadline=arena.clock() + 480)
    thread = next(iter(arena.state.sessions[opened["session_id"]]["threads"].values()))
    assert len(decided) == 3 and len(thread["late"]) == 2 and thread["closed"] != "timeout"
    assert [m["action"]["type"] for m in _messages(arena)] == ["accept"] and result["deal"]


def test_provider_timeout_cancels_io_and_closes_once(arena, monkeypatch):
    monkeypatch.setattr(sys.modules["tacto_negotiator.play"], "time", time)
    arena.state.clock = time.time
    arena.state.cases["case:S02"]["buyer"]["category_goal"] = "secure_supply"
    arena.state.rehearse(wait=0, cap=480, waves=1)
    cancelled = threading.Event()
    requests = []

    async def delayed(request):
        requests.append(request)
        try:
            await asyncio.sleep(60)
            pytest.fail("late provider output must not complete")
        finally:
            cancelled.set()

    complete = CompletionClient("gpt-6.1-sol", "local-only", base_url="http://model.test/v1",
                                transport=httpx.MockTransport(delayed))
    agent = RoutedAgent(complete, max_decision_seconds=0.1, min_model_seconds=0)
    table = RemoteTable(arena.client())
    started = time.monotonic()
    result = play(table, "case:R1-S02", agent, deadline=time.time() + 480)
    assert time.monotonic() - started < 2
    assert result["ended"] and cancelled.is_set() and requests
    moves = [json.loads(req.content)["message"] for req, _ in arena.requests if req.url.path.endswith("/messages")]
    # the model budget expired well inside the thread's deadline: counter with a script, close only at the end
    assert moves[0].get("term_sheets") and not moves[0].get("action") and moves[-1].get("action")
    assert agent.last_decision["fallback"]


def test_routed_buyer_plays_five_full_waves_with_both_model_routes(arena):
    # Keep model-route coverage explicit as supported quantitative cases gain scripts.
    arena.state.cases["case:S02"]["term_options"]["payment_days"] = list(range(121))
    arena.state.cases["case:S03"]["buyer"]["category_goal"] = "secure_supply"
    schedule = arena.state.rehearse(wait=0, cap=480, brk=30, waves=5)
    agents, called = [], []

    def complete(route, system, user, *, deadline):
        assert deadline > arena.clock()
        called.append(route)
        # Shared prompt includes the case file before the supplier threads.
        case = json.JSONDecoder().raw_decode(user.split("CASE FILE\n", 1)[1])[0]
        return json.dumps({"supplier_id": case["suppliers"][0], "action": "accept"})

    def factory():
        agent = RoutedAgent(lambda *args, **kw: complete("fast", *args, **kw),
                            lambda *args, **kw: complete("strong", *args, **kw), clock=arena.clock)
        agents.append(agent)
        return agent

    with ThreadPoolExecutor(max_workers=1) as pool:
        runner = pool.submit(play_waves, arena.client(), factory, waves=5, poll=0.01)
        try:
            for wave in schedule:
                with arena.state.lock:
                    arena.clock.now = wave["opens"]

                def finished(n=wave["n"]):
                    with arena.state.lock:
                        return sum(s["status"] == "ended" for s in arena.state.sessions.values()) == n * 3

                wait_until(finished)
                arena.clock.now = wave["closes"]
                wait_until(lambda n=wave["n"]: ("local-only-buyer", n) in arena.observations)
            results = runner.result(timeout=5)
        finally:
            arena.clock.now = schedule[-1]["closes"] + 1
    assert len(results) == len(agents) == 15
    assert all(isinstance(r, dict) and r["ended"] for r in results.values())
    assert all(not r["deal"] or r["must_haves_ok"] for r in results.values())
    assert called.count("fast") >= 5 and called.count("strong") >= 5
    assert all(th["timeouts"] == 0 for s in arena.state.sessions.values() for th in s["threads"].values())


def test_cli_factory_shares_provider_capacity_between_case_agents(monkeypatch):
    from tacto_negotiator.cli import agent_factory

    for name, value in {"LLM_API_KEY": "local-only", "LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-6.1-sol",
                        "LLM_STRONG_MODEL": "gpt-6-astra", "LLM_EFFORT": "low", "LLM_STRONG_EFFORT": "medium",
                        "LLM_MAX_CONCURRENT": "2", "LLM_BASE_URL": "http://model.test/v1"}.items():
        monkeypatch.setenv(name, value)
    factory = agent_factory("routed", None)
    one, two = factory(), factory()
    assert isinstance(one, RoutedAgent) and one is not two
    assert one.fast is two.fast and one.strong is two.strong
    assert one.fast.semaphore is one.strong.semaphore


@pytest.mark.parametrize("command", ["play", "run"])
def test_cli_exposes_the_routed_agent(monkeypatch, capsys, command):
    from tacto_negotiator import cli

    monkeypatch.setattr(cli, "load_env", lambda: pytest.fail("help must not read .env"))
    with pytest.raises(SystemExit) as ended:
        cli.main([command, "--help"])
    assert ended.value.code == 0 and "routed" in capsys.readouterr().out
