"""Offline provider contract and cancellation tests; .test requests use MockTransport only."""

import asyncio
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tacto_negotiator.agents.completion import CompletionClient, response_text, routed_completions_from_env

ANSWER = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": '{"supplier_id":"sup:A",'},
            {"type": "output_text", "text": '"action":"accept"}'},
        ]},
    ],
}


def completion(handler, **kwargs):
    return CompletionClient("gpt-6.1-sol", "local-only", base_url="http://model.test/v1",
                            transport=httpx.MockTransport(handler), **kwargs)


def test_request_and_complete_response_contract():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=ANSWER)

    client = completion(handle, effort="low", max_output_tokens=2048)
    text = client("System instruction", "Case data", deadline=time.monotonic() + 2)
    assert json.loads(text) == {"supplier_id": "sup:A", "action": "accept"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "http://model.test/v1/responses"
    assert json.loads(request.content) == {
        "model": "gpt-6.1-sol", "instructions": "System instruction", "input": "Case data",
        "reasoning": {"effort": "low"}, "max_output_tokens": 2048, "store": False,
    }


@pytest.mark.parametrize("change", [
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    {"status": "failed", "error": {"message": "provider failed"}},
    {"status": "completed", "incomplete_details": {"reason": "max_output_tokens"}},
    {"output": []},
    {"output": [{"type": "message", "role": "assistant", "status": "incomplete", "content": []}]},
    {"output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}]},
    {"output": [{"type": "function_call", "name": "send_move"}]},
    {"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": 12}]}]},
])
def test_rejects_partial_refused_or_nontext_responses(change):
    with pytest.raises(ValueError):
        response_text({**copy.deepcopy(ANSWER), **change})


@pytest.mark.parametrize("status", [429, 500, 503])
def test_provider_errors_are_not_retried(status):
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "1"}, json={"error": "synthetic fault"})

    with pytest.raises(httpx.HTTPStatusError):
        completion(handle)("system", "case", deadline=time.monotonic() + 2)
    assert len(calls) == 1


def test_deadline_cancels_pending_headers_and_releases_slot():
    cancelled = threading.Event()
    returned = threading.Event()
    semaphore = threading.BoundedSemaphore(1)

    async def handle(request):
        try:
            await asyncio.sleep(60)
            returned.set()
            return httpx.Response(200, json=ANSWER)
        finally:
            cancelled.set()

    client = completion(handle, semaphore=semaphore)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client("system", "case", deadline=started + 0.05)
    assert time.monotonic() - started < 0.5
    assert cancelled.is_set() and not returned.is_set()
    assert semaphore.acquire(blocking=False)
    semaphore.release()


def test_deadline_includes_response_body_read_and_closes_it():
    cancelled, closed = threading.Event(), threading.Event()

    class DelayedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"status":"completed",'
            try:
                await asyncio.sleep(60)
                yield b'"output":[]}'
            finally:
                cancelled.set()

        async def aclose(self):
            closed.set()

    async def handle(request):
        return httpx.Response(200, stream=DelayedBody())

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        completion(handle)("system", "case", deadline=started + 0.05)
    assert time.monotonic() - started < 0.5
    assert cancelled.is_set() and closed.is_set()


def test_queue_wait_counts_against_the_same_deadline():
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    reached_http = []

    async def handle(request):
        reached_http.append(request)
        return httpx.Response(200, json=ANSWER)

    client = completion(handle, semaphore=semaphore)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="queue"):
            client("system", "case", deadline=started + 0.05)
        assert time.monotonic() - started < 0.5 and not reached_http
    finally:
        semaphore.release()
    assert json.loads(client("system", "case", deadline=time.monotonic() + 2))["action"] == "accept"


def test_fast_and_strong_share_capacity_under_concurrent_requests():
    semaphore = threading.BoundedSemaphore(1)
    lock = threading.Lock()
    active, peak = 0, 0

    async def handle(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            await asyncio.sleep(0.03)
            return httpx.Response(200, json=ANSWER)
        finally:
            with lock:
                active -= 1

    fast = completion(handle, semaphore=semaphore)
    strong = CompletionClient("gpt-6-astra", "local-only", base_url="http://model.test/v1", effort="medium",
                              semaphore=semaphore, transport=httpx.MockTransport(handle))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda client: client("system", "case", deadline=time.monotonic() + 3),
                                [fast, strong, fast, strong]))
    assert all(json.loads(text)["action"] == "accept" for text in results)
    assert peak == 1 and active == 0


@pytest.mark.parametrize("deadline", [float("inf"), float("nan"), True, "later"])
def test_rejects_invalid_absolute_deadlines(deadline):
    client = completion(lambda request: pytest.fail("HTTP must not run"))
    with pytest.raises(ValueError, match="deadline"):
        client("system", "case", deadline=deadline)


def test_expired_deadline_never_sends_http():
    client = completion(lambda request: pytest.fail("HTTP must not run"))
    with pytest.raises(TimeoutError):
        client("system", "case", deadline=time.monotonic() - 1)


@pytest.fixture
def provider_env(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT", "LLM_STRONG_MODEL", "LLM_STRONG_EFFORT",
                 "LLM_MAX_CONCURRENT", "LLM_API_KEY", "OPENAI_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "local-only")


def test_factory_defaults_and_explicit_strong_model(provider_env, monkeypatch):
    fast, strong = routed_completions_from_env()
    assert (fast.model, fast.effort, strong) == ("gpt-6.1-sol", "low", None)
    monkeypatch.setenv("LLM_STRONG_MODEL", "gpt-6-astra")
    monkeypatch.setenv("LLM_MAX_CONCURRENT", "1")
    monkeypatch.setenv("LLM_BASE_URL", "http://model.test/v1/")
    fast, strong = routed_completions_from_env()
    assert (strong.model, strong.effort) == ("gpt-6-astra", "medium")
    assert fast.semaphore is strong.semaphore and fast.url == "http://model.test/v1/responses"


@pytest.mark.parametrize(("name", "value"), [
    ("LLM_PROVIDER", "gemini"), ("LLM_MAX_CONCURRENT", "0"), ("LLM_MAX_CONCURRENT", "bad"),
    ("LLM_EFFORT", "none"), ("LLM_MODEL", ""), ("LLM_API_KEY", ""),
])
def test_bad_configuration_fails_before_any_request(provider_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        routed_completions_from_env()


def test_factory_accepts_standard_openai_key_name(provider_env, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "local-only")
    assert routed_completions_from_env()[0].model == "gpt-6.1-sol"


def test_anthropic_provider_reads_its_own_key_and_accounts_cache_tokens(monkeypatch):
    from types import SimpleNamespace

    from tacto_negotiator.agents.completion import AnthropicCompletionClient, anthropic_metadata
    from tacto_negotiator.benchmark import BudgetedCompletion, BudgetLedger

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY_ANTHROPIC", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    fast, strong = routed_completions_from_env()
    assert isinstance(fast, AnthropicCompletionClient) and strong is None
    assert (fast.model, fast.effort, fast.api_key) == ("claude-opus-5-5", "low", "test-key")
    fast.service_tier = "default"
    BudgetedCompletion(fast, BudgetLedger(1))  # priced and accepted by the spend ledger
    usage = SimpleNamespace(input_tokens=10, cache_creation_input_tokens=5, cache_read_input_tokens=100,
                            output_tokens=7)
    assert anthropic_metadata(SimpleNamespace(usage=usage, stop_reason="end_turn")) == {
        "status": "completed", "usage": {"input_tokens": 115, "output_tokens": 7, "cached_input_tokens": 100}}
    assert anthropic_metadata(SimpleNamespace(usage=usage, stop_reason="max_tokens"))["status"] == "incomplete"
