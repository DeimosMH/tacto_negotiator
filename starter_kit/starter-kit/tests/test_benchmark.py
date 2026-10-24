"""Offline benchmark accounting, key parsing and metadata tests; never use real credentials."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from arena_kit.agents.completion import CompletionClient, response_metadata
from arena_kit.benchmark import (
    INPUT_OVERHEAD,
    OPENAI_BASE,
    BudgetedCompletion,
    BudgetExceeded,
    BudgetLedger,
    estimated_nano,
    preflight_models,
    read_llm_key,
)


def payload(*, input_tokens=50, output_tokens=10, status="completed"):
    return {"status": status,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "input_tokens_details": {"cached_tokens": 20},
                      "output_tokens_details": {"reasoning_tokens": 4}},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "private response text"}]}]}


def wrapper(handler, ledger=None, model="gpt-6.1-sol", **options):
    client = CompletionClient(model, "fake-benchmark-key", service_tier="default",
                              transport=httpx.MockTransport(handler), **options)
    return BudgetedCompletion(client, ledger or BudgetLedger())


@pytest.mark.parametrize("assignment", [
    "LLM_API_KEY=fake-benchmark-key\n",
    '  LLM_API_KEY = "fake-benchmark-key" # label\n',
    "LLM_API_KEY='fake-benchmark-key'\n",
    "LLM_API_KEY=fake-benchmark-key # label\n",
])
def test_reads_only_literal_llm_key_and_never_loads_other_settings(tmp_path, monkeypatch, assignment):
    path = tmp_path / "selected.env"
    path.write_text("ARENA_API_KEY=do-not-use\nLLM_BASE_URL=https://untrusted.invalid\n" + assignment)
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert read_llm_key(path) == "fake-benchmark-key"
    import os

    assert "ARENA_API_KEY" not in os.environ and "LLM_BASE_URL" not in os.environ


@pytest.mark.parametrize("content", [
    "ARENA_API_KEY=only-an-arena-key\n",
    "LLM_API_KEY=\n",
    'LLM_API_KEY="unterminated\n',
    'LLM_API_KEY="literal"; command\n',
    "LLM_API_KEY=$(command)\n",
    "LLM_API_KEY=${ANOTHER_KEY}\n",
    "LLM_API_KEY=`command`\n",
    "LLM_API_KEY=first\nLLM_API_KEY=second\n",
])
def test_rejects_missing_ambiguous_or_evaluated_keys_without_leaking_values(tmp_path, content):
    path = tmp_path / "selected.env"
    path.write_text(content)
    with pytest.raises(ValueError) as error:
        read_llm_key(path)
    assert "command" not in str(error.value) and "only-an-arena-key" not in str(error.value)


def test_preflight_contacts_only_fixed_openai_and_filters_requested_models():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "gpt-6.1-sol"}, {"id": "private-unrequested-model"}]})

    result = preflight_models("fake-benchmark-key", ["gpt-6.1-sol", "gpt-6-astra"],
                              transport=httpx.MockTransport(handle))
    assert result == {"status": "ok", "http_status": 200,
                      "models": {"gpt-6.1-sol": "available", "gpt-6-astra": "not_listed"}}
    assert len(seen) == 1 and str(seen[0].url) == OPENAI_BASE + "/models"
    assert "private-unrequested-model" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["http", "transport", "malformed"])
def test_preflight_errors_are_redacted(failure):
    def handle(request):
        if failure == "transport":
            raise httpx.ConnectError("sensitive exception text", request=request)
        if failure == "malformed":
            return httpx.Response(200, content=b"sensitive malformed text")
        return httpx.Response(401, json={"error": {"message": "sensitive server text"}})

    result = preflight_models("fake-benchmark-key", ["gpt-6.1-sol"], transport=httpx.MockTransport(handle))
    assert result["status"] == "error"
    assert "sensitive" not in json.dumps(result) and "fake-benchmark-key" not in json.dumps(result)


def test_observer_contains_only_status_and_usage_not_text_or_headers():
    observed, seen = [], []

    async def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=payload())

    client = CompletionClient("gpt-6.1-sol", "fake-benchmark-key", transport=httpx.MockTransport(handle),
                              service_tier="default")
    assert client("private system", "private prompt", deadline=time.monotonic() + 2,
                  observer=observed.append) == "private response text"
    assert observed == [{"status": "completed", "usage": {"input_tokens": 50, "output_tokens": 10,
                         "cached_input_tokens": 20, "reasoning_tokens": 4}}]
    assert seen[0]["service_tier"] == "default"


def test_usage_is_settled_and_reasoning_is_not_counted_twice():
    async def handle(request):
        return httpx.Response(200, json=payload())

    complete = wrapper(handle)
    complete("system", "prompt", deadline=time.monotonic() + 2)
    result = complete.ledger.snapshot()
    assert result["estimated_known_usage_usd"] == (50 * 2.5 + 10 * 10) / 1_000_000
    assert "1.25x" in result["price_basis"]
    assert result["unreconciled_reservations_usd"] == result["reserved_in_flight_usd"] == 0
    assert result["calls"][0]["usage"]["reasoning_tokens"] == 4
    serialized = json.dumps(result)
    for secret in ("fake-benchmark-key", "private response text", "Authorization", "prompt", "system"):
        assert secret not in serialized


def test_incomplete_response_still_accounts_for_reported_usage():
    async def handle(request):
        return httpx.Response(200, json=payload(status="incomplete"))

    complete = wrapper(handle)
    with pytest.raises(ValueError):
        complete("system", "prompt", deadline=time.monotonic() + 2)
    result = complete.ledger.snapshot()
    assert result["estimated_known_usage_usd"] > 0 and result["unreconciled_reservations_usd"] == 0
    call = result["calls"][0]
    assert call["status"] == "error" and call["provider_status"] == "incomplete"
    assert call["error_class"] == "ValueError"


def test_timeout_keeps_full_reservation_and_cancels_the_request():
    cancelled = threading.Event()

    async def handle(request):
        try:
            await asyncio.sleep(60)
            return httpx.Response(200, json=payload())
        finally:
            cancelled.set()

    complete = wrapper(handle)
    with pytest.raises(TimeoutError):
        complete("system", "prompt", deadline=time.monotonic() + 0.03)
    result = complete.ledger.snapshot()
    call = result["calls"][0]
    assert cancelled.is_set() and call["status"] == "timeout"
    assert result["estimated_known_usage_usd"] == 0
    assert result["unreconciled_reservations_usd"] == call["reserved_usd"] > 0
    assert call["estimated_cost_usd"] is None and result["reserved_in_flight_usd"] == 0


def test_missing_usage_keeps_reservation_even_on_success():
    async def handle(request):
        answer = payload()
        del answer["usage"]
        return httpx.Response(200, json=answer)

    complete = wrapper(handle)
    complete("system", "prompt", deadline=time.monotonic() + 2)
    result = complete.ledger.snapshot()
    assert result["unreconciled_reservations_usd"] > 0 and result["estimated_known_usage_usd"] == 0


def test_parallel_reservations_cannot_overdraw_the_ceiling():
    ledger = BudgetLedger(0.01)

    def attempt(_):
        try:
            return ledger.reserve("gpt-6.1-sol", 1000, 100)
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        reservations = list(pool.map(attempt, range(20)))
    assert sum(x is not None for x in reservations) == 2  # each reserves exactly $0.0035
    snapshot = ledger.snapshot()
    assert snapshot["committed_usd"] == 0.007 <= snapshot["cap_usd"]
    assert snapshot["budget_rejections"] == 18


def test_parallel_calls_keep_their_own_usage_and_latency():
    barrier = threading.Barrier(4)

    def handle(request):
        return httpx.Response(200, json=payload(input_tokens=int(json.loads(request.content)["input"])))

    complete = wrapper(handle)

    def run(tokens):
        barrier.wait()
        complete("system", str(tokens), deadline=time.monotonic() + 5)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, (11, 12, 13, 14)))
    snapshot = complete.ledger.snapshot()
    assert sorted(c["usage"]["input_tokens"] for c in snapshot["calls"]) == [11, 12, 13, 14]
    assert all(c["latency_s"] >= 0 and c["status"] == "completed" for c in snapshot["calls"])
    assert snapshot["estimated_known_usage_usd"] == ((11 + 12 + 13 + 14) * 2.5 + 40 * 10) / 1_000_000


def test_budget_rejection_happens_before_provider_dispatch():
    sent = []
    complete = wrapper(lambda request: sent.append(request), BudgetLedger(0.000001))
    with pytest.raises(BudgetExceeded):
        complete("system", "prompt", deadline=time.monotonic() + 2)
    assert not sent and not complete.ledger.snapshot()["calls"]


def test_utf8_bound_and_large_input_price_guard():
    complete = wrapper(lambda request: httpx.Response(200, json=payload()))
    complete("é", "🙂", deadline=time.monotonic() + 2)
    assert complete.ledger.snapshot()["calls"][0]["input_bound"] == 2 + 4 + INPUT_OVERHEAD
    with pytest.raises(ValueError, match="standard input-price"):
        complete("s", "a" * 272_000, deadline=time.monotonic() + 2)


def test_usage_beyond_reservation_halts_future_spending_and_flags_uncertainty():
    complete = wrapper(lambda request: httpx.Response(200, json=payload(input_tokens=9000)))
    complete("s", "u", deadline=time.monotonic() + 2)
    snapshot = complete.ledger.snapshot()
    assert snapshot["halted"] and snapshot["calls"][0]["accounting_error"] == "usage_exceeds_reserved_bounds"
    assert snapshot["estimated_known_usage_usd"] == 0 and snapshot["unreconciled_reservations_usd"] > 0
    with pytest.raises(BudgetExceeded):
        complete("s", "u", deadline=time.monotonic() + 2)


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": True, "output_tokens": 1},
                                    {"input_tokens": -1, "output_tokens": 1}])
def test_invalid_usage_is_not_treated_as_zero_cost(usage):
    assert response_metadata({"status": "completed", "usage": usage})["usage"] is None


@pytest.mark.parametrize(("model", "expected"), [("gpt-6.1-sol", 12.5), ("gpt-6-astra", 62.5), ("gpt-6-luna", 0.625)])
def test_default_cache_write_ceiling_for_one_million_input_and_output_tokens(model, expected):
    assert estimated_nano(model, 1_000_000, 1_000_000) / 1_000_000_000 == expected


def test_benchmark_rejects_unpriced_models_custom_endpoints_and_nonstandard_tiers():
    with pytest.raises(ValueError, match="verified"):
        BudgetedCompletion(CompletionClient("unknown", "fake", service_tier="default"), BudgetLedger())
    with pytest.raises(ValueError, match="fixed OpenAI"):
        BudgetedCompletion(CompletionClient("gpt-6.1-sol", "fake", base_url="https://elsewhere.invalid",
                                            service_tier="default"), BudgetLedger())
    with pytest.raises(ValueError, match="standard"):
        BudgetedCompletion(CompletionClient("gpt-6.1-sol", "fake"), BudgetLedger())
