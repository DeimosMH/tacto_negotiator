"""Bounded OpenAI Responses calls for RoutedAgent; no credentials are loaded from files.

Call from synchronous case workers with an absolute time.monotonic() deadline. Provider
queueing, HTTP and response reading share that budget. Each request owns and closes its async
client; timed-out requests are cancelled instead of leaving a background model worker running.
"""

import asyncio
import math
import os
import threading
import time

import httpx

EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("model decision deadline expired")
    return left


def response_text(response):
    """Extract only complete assistant text; partial JSON must never become a decision."""
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise ValueError("model response was not completed")
    if response.get("error") or response.get("incomplete_details"):
        raise ValueError("model response contains an error or incomplete output")
    output = response.get("output")
    if not isinstance(output, list):
        raise ValueError("model response has no output array")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("invalid model output item")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ValueError("unexpected model output type")
        if item.get("status") not in (None, "completed") or not isinstance(item.get("content"), list):
            raise ValueError("model message was not completed")
        for part in item["content"]:
            if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ValueError("model output is not decision text")
            texts.append(part["text"])
    result = "".join(texts)
    if not result.strip():
        raise ValueError("model response has no decision text")
    return result


class CompletionClient:
    """One model configuration; fast and strong clients can share a concurrency semaphore."""

    def __init__(
        self,
        model,
        api_key,
        *,
        effort="low",
        base_url="https://api.openai.com/v1",
        max_output_tokens=4096,
        semaphore=None,
        transport=None,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("a model ID is required")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("set LLM_API_KEY or OPENAI_API_KEY for the routed agent")
        if effort not in EFFORTS:
            raise ValueError(f"reasoning effort must be one of {sorted(EFFORTS)}")
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        self.model, self.effort = model, effort
        self.url = base_url.rstrip("/") + "/responses"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.max_output_tokens = max_output_tokens
        self.semaphore = semaphore if semaphore is not None else threading.BoundedSemaphore(10)
        self.transport = transport

    def __call__(self, system, user, *, deadline):
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError("deadline must be a finite absolute monotonic time")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("CompletionClient must run in a synchronous case worker")
        if not self.semaphore.acquire(timeout=_remaining(deadline)):
            raise TimeoutError("model concurrency queue exhausted the decision deadline")
        try:
            _remaining(deadline)
            result = asyncio.run(self._request(system, user, deadline))
            _remaining(deadline)  # discard a reply if parsing/cleanup used the remaining budget
            return result
        finally:
            self.semaphore.release()

    async def _request(self, system, user, deadline):
        body = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "reasoning": {"effort": self.effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        # shortcut: cancellation is cooperative I/O, not an OS process deadline; isolate the
        # transport in a process if an uncooperative resolver/transport requires a hard kill.
        async with asyncio.timeout(_remaining(deadline)):
            async with httpx.AsyncClient(transport=self.transport, trust_env=False) as client:
                response = await client.post(self.url, headers=self.headers, json=body, timeout=_remaining(deadline))
                response.raise_for_status()
                result = response_text(response.json())
                _remaining(deadline)
                return result


def routed_completions_from_env():
    """Read explicit process configuration; the strong route is disabled unless configured."""
    if os.environ.get("LLM_PROVIDER", "openai").lower() != "openai":
        raise ValueError("the routed agent uses OpenAI Responses; set LLM_PROVIDER=openai")
    maximum = int(os.environ.get("LLM_MAX_CONCURRENT", "10"))
    if maximum < 1:
        raise ValueError("LLM_MAX_CONCURRENT must be positive")
    shared = {
        "api_key": os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        "semaphore": threading.BoundedSemaphore(maximum),
    }
    fast = CompletionClient(os.environ.get("LLM_MODEL", "gpt-6.1-sol"),
                            effort=os.environ.get("LLM_EFFORT", "low"), **shared)
    strong_model = os.environ.get("LLM_STRONG_MODEL", "").strip()
    strong = (CompletionClient(strong_model, effort=os.environ.get("LLM_STRONG_EFFORT", "medium"), **shared)
              if strong_model else None)
    return fast, strong
