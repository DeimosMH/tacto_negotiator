"""Sunday readiness: virtual 480-second waves, real runner/API, synthetic failures.

No provider calls or credentials. Timing injections are scenarios, not model benchmarks.
The concurrency test bypasses rate limiting; a separate test checks shared-client pacing.
"""

import copy
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from arena_kit.agents.llm import LLMAgent
from arena_kit.agents.naive import NaiveAgent
from arena_kit.client import ArenaClient, ArenaError
from arena_kit.cost import complete_offer
from arena_kit.play import RemoteTable, closing_move, play, play_waves
from arena_kit.sim import Sim, create_app, load_cases


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def arena(monkeypatch):
    """Run the actual HTTP routes in memory; .test URLs never leave MockTransport."""
    clock = Clock()
    state = Sim(load_cases(), clock=clock)
    requests, observations, clients = [], set(), []
    monkeypatch.setattr("arena_kit.sim.MAX_CALLS", 100_000)
    runner_time = SimpleNamespace(time=clock, monotonic=clock, sleep=lambda _: time.sleep(0.005))
    monkeypatch.setattr(sys.modules["arena_kit.play"], "time", runner_time)
    with TestClient(create_app(state)) as api:
        def handle(request):
            response = api.request(request.method, str(request.url), content=request.content, headers=request.headers)
            requests.append((request, response.status_code))
            if request.url.path == "/v1/cases" and response.status_code == 200:
                for case in response.json():
                    if not case.get("open", True):
                        observations.add((request.headers["X-API-Key"], case["wave"]))
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)

        def client(name="buyer", transport=None, retries=0):
            key = f"local-only-{name}"
            state.teams[key] = name
            result = ArenaClient("http://arena.test", key, rate=1_000_000, retries=retries,
                                 transport=httpx.MockTransport(transport or handle))
            clients.append(result)
            return result

        yield SimpleNamespace(clock=clock, state=state, client=client, handle=handle,
                              requests=requests, observations=observations)
        for client_ in clients:
            client_.http.close()


def wait_until(predicate, seconds=15):
    until = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < until, "local runner did not reach the expected state"
        time.sleep(0.01)


def test_five_eight_minute_waves_three_teams_ten_cases_at_once(arena):
    state, clock = arena.state, arena.clock
    examples = list(state.cases.values())
    state.cases = {f"case:stress-{i}": {**copy.deepcopy(examples[i % 3]), "case_id": f"case:stress-{i}"}
                   for i in range(10)}
    schedule = state.rehearse(wait=60, cap=480, brk=30, waves=5)
    peaks = []

    def all_open():
        with state.lock:
            counts = Counter(s["team"] for s in state.sessions.values() if s["status"] == "open")
            assert sorted(counts.values()) == [10, 10, 10]
            peaks.append(sum(counts.values()))

    barrier = threading.Barrier(30, action=all_open, timeout=10)

    class TwoMoves(NaiveAgent):
        first = True

        def decide(self, case, threads):
            if self.first:
                self.first = False
                barrier.wait()  # prove every team's ten cases are concurrently open
                return super().decide(case, threads)
            return closing_move(case, threads)

    clients = [arena.client(str(i)) for i in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        runners = [pool.submit(play_waves, c, TwoMoves, waves=5, parallel=10, poll=0.01) for c in clients]
        try:
            wait_until(lambda: sum(status == 403 for _, status in arena.requests) >= 3)
            for wave in schedule:
                with state.lock:
                    clock.now = wave["opens"]

                def finished(n=wave["n"]):
                    with state.lock:
                        return sum(s["status"] == "ended" for s in state.sessions.values()) == n * 30

                wait_until(finished)
                with state.lock:
                    assert all(s["created_at"] <= clock() < wave["closes"]
                               for s in state.sessions.values() if s["wave"] == wave["n"])
                    clock.now = wave["closes"]
                wait_until(lambda n=wave["n"]: all((f"local-only-{i}", n) in arena.observations for i in range(3)))
            results = [f.result(timeout=5) for f in runners]
        finally:
            barrier.abort()
            with state.lock:
                clock.now = schedule[-1]["closes"] + 1
        assert [len(r) for r in results] == [50, 50, 50]
        assert all(not isinstance(r, Exception) and r["ended"] for team in results for r in team.values())
    assert peaks == [30] * 5 and len(state.sessions) == 150
    assert len({(s["team"], s["case"]["case_id"]) for s in state.sessions.values()}) == 150
    assert all(not r["deal"] or r["must_haves_ok"] for team in results for r in team.values())
    assert all(c.me()["practice"]["used"] == 0 for c in clients)
    assert not any("backoffice" in str(req.url) or "leaderboard" in str(req.url) for req, _ in arena.requests)


def test_wave_visibility_cap_and_restart_resume(arena):
    state, clock, client = arena.state, arena.clock, arena.client()
    waves = state.rehearse(wait=0, cap=480, brk=30, waves=5)
    case = next(c for c in client.cases("final") if c["case_id"] == "case:R1-S01")
    session = client.open_session(case["case_id"])
    resumed = RemoteTable(client, case)
    assert play(resumed, case["case_id"], NaiveAgent(), deadline=clock() + 80)["deal"]
    assert resumed.session_id == session["session_id"] and len(state.sessions) == 1
    # A completed session can also be resumed without issuing a second acceptance.
    assert play(RemoteTable(client, case), case["case_id"], NaiveAgent())["deal"]
    pending = client.open_session("case:R1-S02")["session_id"]
    with pytest.raises(ArenaError, match="no such case"):
        client.open_session("case:R2-S01")
    clock.now = waves[0]["closes"]
    capped = client.session(pending)
    assert capped["end_reason"] == "wave_cap" and not capped["result"]["deal"]
    assert {c["wave"] for c in client.cases("final")} == {1}
    assert not any(c["open"] for c in client.cases("final"))
    clock.now = waves[1]["opens"]
    assert {c["wave"] for c in client.cases("final") if c["open"]} == {2}


def test_a_broken_case_does_not_stop_the_other_final_cases(arena):
    wave = arena.state.rehearse(wait=0, cap=480, waves=1)[0]
    failed = threading.Event()

    class SometimesBroken(NaiveAgent):
        def decide(self, case, threads):
            if case["case_id"] == "case:R1-S01":
                failed.set()
                raise ValueError("synthetic policy failure")
            return closing_move(case, threads)

    with ThreadPoolExecutor(max_workers=1) as pool:
        runner = pool.submit(play_waves, arena.client(), SometimesBroken, waves=1, poll=0.01)
        try:
            def others_done():
                with arena.state.lock:
                    return sum(s["status"] == "ended" for s in arena.state.sessions.values()) == 2

            wait_until(lambda: failed.is_set() and others_done())
        finally:
            arena.clock.now = wave["closes"]
        results = runner.result(timeout=5)
    assert isinstance(results.pop("case:R1-S01"), ValueError)
    assert all(r["ended"] for r in results.values())


def test_an_active_case_sees_a_host_extension(arena, monkeypatch):
    arena.state.cases = {"case:S01": arena.state.cases["case:S01"]}
    arena.state.rehearse(wait=0, cap=480, waves=1)
    started, updated, release = threading.Event(), threading.Event(), threading.Event()
    deadlines = []
    start, _, brk = arena.state.rehearsal

    def observe(table, case_id, agent, deadline):
        deadlines.append(deadline())
        started.set()
        wait_until(lambda: deadline() == start + 600, seconds=5)
        deadlines.append(deadline())
        updated.set()
        assert release.wait(5)
        return {"ended": True}

    monkeypatch.setattr(sys.modules["arena_kit.play"], "play", observe)
    client = arena.client()
    with ThreadPoolExecutor(max_workers=1) as pool:
        runner = pool.submit(play_waves, client, NaiveAgent, waves=1, poll=0.01)
        try:
            assert started.wait(5)
            with arena.state.lock:
                arena.state.rehearsal = (start, 600, brk)
            # Wait for the polling runner, not this test, to consume a refreshed listing.
            assert updated.wait(5)
        finally:
            release.set()
            arena.clock.now = start + 600
        runner.result(timeout=5)
    assert deadlines == [start + 480, start + 600]


@pytest.mark.parametrize("fault", ["lost_reply", "rate_limit"])
def test_retry_does_not_repeat_a_committed_final_move(arena, monkeypatch, fault):
    arena.state.rehearse(wait=0, cap=480, waves=5)
    clock = arena.clock
    monkeypatch.setattr(sys.modules["arena_kit.client"], "time",
                        SimpleNamespace(monotonic=clock, sleep=clock.advance))
    attempts = []

    def unreliable(request):
        if request.url.path.endswith("/messages"):
            attempts.append(request.headers["X-Move-Id"])
            if len(attempts) == 1:
                if fault == "rate_limit":
                    return httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "busy"})
                arena.handle(request)  # committed on the server; the reply is lost
                raise httpx.ReadError("synthetic lost response", request=request)
        return arena.handle(request)

    client = arena.client(transport=unreliable, retries=1)
    opened = client.open_session("case:R1-S01")
    sid = next(iter(opened["threads"]))
    move = NaiveAgent().decide(opened["case"], opened["threads"])["message"]
    client.send_message(opened["session_id"], sid, move)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert arena.state.sessions[opened["session_id"]]["threads"][sid]["round"] == 1


def test_one_shared_client_paces_a_concurrent_burst():
    sent = []

    def handle(request):
        sent.append(time.monotonic())
        return httpx.Response(200, json={"ok": True})

    client = ArenaClient("http://arena.test", "local-only", transport=httpx.MockTransport(handle), rate=9)
    try:
        with ThreadPoolExecutor(max_workers=18) as pool:
            assert all(r["ok"] for r in pool.map(lambda _: client.me(), range(18)))
    finally:
        client.http.close()
    sent.sort()
    assert sent[-1] - sent[0] >= 17 / 9 - 0.05
    assert max(sum(start <= t < start + 0.95 for t in sent) for start in sent) <= 10


@pytest.mark.parametrize("failure", ["malformed", "timeout", "rejected"])
@pytest.mark.parametrize("valid_rival", [False, True])
def test_model_fallback_never_accepts_a_must_have_violation(failure, valid_rival):
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 60}]
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 50, "payment_days": 60}}

    def offer(sid, price, days):
        return [{"from": sid, "status": "final_offer", "term_sheets": [
            {"term_sheet_id": f"offer:{sid}", "terms": {"unit_price": price, "payment_days": days}}]}]

    threads = {"sup:bad": offer("sup:bad", 20, 14)}
    if valid_rival:
        threads["sup:good"] = offer("sup:good", 21, 60)
    case["suppliers"] = list(threads)

    def complete(system, user):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic model timeout")
        return "not JSON" if failure == "malformed" else '{"supplier_id":"sup:bad","action":"accept"}'

    decision = LLMAgent(complete).decide(case, threads)
    action = decision["message"]["action"]
    assert action["type"] == ("accept" if valid_rival else "walk_away")
    if valid_rival:
        assert decision["supplier_id"] == "sup:good"


def test_runner_rechecks_wave_deadline_after_decision(arena):
    arena.state.rehearse(wait=0, cap=100, waves=5)

    class Slow(NaiveAgent):
        def decide(self, case, threads):
            arena.clock.advance(30)  # enters the 90-second closing reserve while deciding
            return super().decide(case, threads)

    result = play(RemoteTable(arena.client()), "case:R1-S01", Slow(), deadline=arena.clock() + 100)
    session = next(iter(arena.state.sessions.values()))
    assert result["deal"]
    assert all(th["round"] == 0 for th in session["threads"].values())


@pytest.mark.parametrize(("delay", "deal", "reason"), [
    (20, True, "accepted"), (45, True, "accepted"), (61, True, "accepted"),
    (181, False, "timeout"), (481, False, "wave_cap"),
])
def test_synthetic_model_delay_exposes_the_uncancelled_call(arena, delay, deal, reason):
    arena.state.rehearse(wait=0, cap=480, waves=5)

    class Delayed:
        first = True

        def decide(self, case, threads):
            if self.first:
                self.first = False
                arena.clock.advance(delay)
            return closing_move(case, threads)

    result = play(RemoteTable(arena.client()), "case:R1-S01", Delayed(), deadline=arena.clock() + 480)
    session = next(iter(arena.state.sessions.values()))
    assert result["deal"] is deal and session["end_reason"] == reason
    if delay == 61:
        assert any(th["timeouts"] == 1 for th in session["threads"].values())


def test_serial_supplier_waits_can_lose_other_rfq_threads(arena):
    case = arena.state.cases["case:S01"]
    case["suppliers"] = ["sup:A", "sup:B", "sup:C"]
    arena.state.rehearse(wait=0, cap=480, waves=5)
    client = arena.client()

    class SlowSupplierTable(RemoteTable):
        def send(self, sid, message):
            reply = super().send(sid, message)
            arena.clock.advance(60)  # only the replying supplier's processing time is excluded
            arena.state.sessions[self.session_id]["threads"][sid]["last_at"] = arena.clock()
            return reply

    class FocusOneSupplier:
        calls = 0

        def decide(self, case, threads):
            self.calls += 1
            arena.clock.advance(1)
            if self.calls > 3:
                return closing_move(case, threads)
            return {"supplier_id": "sup:A", "message": {
                "term_sheets": [{"terms": complete_offer(case, {"unit_price": 1})}]}}

    table = SlowSupplierTable(client)
    result = play(table, "case:R1-S01", FocusOneSupplier(), deadline=arena.clock() + 480)
    threads = arena.state.sessions[table.session_id]["threads"]
    assert result["deal"] and threads["sup:A"]["timeouts"] == 0
    # Diagnostic: a successful case can hide lost bidding options. This is not a readiness pass.
    assert all(threads[sid]["closed"] == "timeout" for sid in ("sup:B", "sup:C"))


@pytest.mark.parametrize("options", [{"waves": 0}, {"cap": 0}, {"wait": -1}, {"brk": -1}])
def test_rehearsal_rejects_invalid_timing(options):
    with pytest.raises(ValueError):
        Sim(load_cases()).rehearse(**options)


def test_cli_can_rehearse_the_five_wave_final(monkeypatch, capsys):
    from arena_kit import sim

    built = []
    monkeypatch.setattr(sim, "create_app", built.append)
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    sim.main(["serve", "--final-rehearsal", "--rehearsal-waves", "5", "--rehearsal-cap", "480"])
    assert len(built[0].waves()) == 5
    assert all(w["closes"] - w["opens"] == 480 for w in built[0].waves())
    assert "--waves 5" in capsys.readouterr().out
