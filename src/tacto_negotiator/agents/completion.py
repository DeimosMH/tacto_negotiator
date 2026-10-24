"""Bounded OpenAI Responses or Anthropic Messages calls for RoutedAgent; no credentials are loaded
from files.

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


def response_metadata(response):
    """Usage-only observation for accounting; never expose response text or request credentials."""
    status = response.get("status") if isinstance(response, dict) else None
    result = {"status": status if isinstance(status, str) and status in
              {"completed", "incomplete", "failed", "cancelled"} else "unknown",
              "usage": None}
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return result
    if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
        return result
    clean = {k: usage[k] for k in ("input_tokens", "output_tokens")}
    for group, name, limit in (("input_tokens_details", "cached_tokens", "input_tokens"),
                               ("output_tokens_details", "reasoning_tokens", "output_tokens")):
        detail = usage.get(group) or {}
        value = detail.get(name) if isinstance(detail, dict) else None
        if type(value) is int and 0 <= value <= usage[limit]:
            clean["cached_input_tokens" if name == "cached_tokens" else name] = value
    result["usage"] = clean
    return result


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
        service_tier=None,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("a model ID is required")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("set LLM_API_KEY or OPENAI_API_KEY for the routed agent")
        if effort not in EFFORTS:
            raise ValueError(f"reasoning effort must be one of {sorted(EFFORTS)}")
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        if service_tier not in (None, "default"):
            raise ValueError("only an explicit standard service tier is supported")
        self.model, self.effort = model, effort
        self.url = base_url.rstrip("/") + "/responses"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.max_output_tokens = max_output_tokens
        self.semaphore = semaphore if semaphore is not None else threading.BoundedSemaphore(10)
        self.transport = transport
        self.service_tier = service_tier

    def __call__(self, system, user, *, deadline, observer=None):
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
            result = asyncio.run(self._request(system, user, deadline, observer))
            _remaining(deadline)  # discard a reply if parsing/cleanup used the remaining budget
            return result
        finally:
            self.semaphore.release()

    async def _request(self, system, user, deadline, observer=None):
        body = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "reasoning": {"effort": self.effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if self.service_tier is not None:
            body["service_tier"] = self.service_tier
        # shortcut: cancellation is cooperative I/O, not an OS process deadline; isolate the
        # transport in a process if an uncooperative resolver/transport requires a hard kill.
        async with asyncio.timeout(_remaining(deadline)):
            async with httpx.AsyncClient(transport=self.transport, trust_env=False) as client:
                response = await client.post(self.url, headers=self.headers, json=body, timeout=_remaining(deadline))
                response.raise_for_status()
                payload = response.json()
                if observer is not None:
                    observer(response_metadata(payload))
                result = response_text(payload)
                _remaining(deadline)
                return result


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def anthropic_metadata(message):
    """Same accounting shape as response_metadata; input_tokens includes cache reads and writes."""
    usage = getattr(message, "usage", None)
    counts = [getattr(usage, name, None) or 0 for name in
              ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")]
    if usage is None or not all(type(value) is int and value >= 0 for value in counts):
        return {"status": "unknown", "usage": None}
    return {"status": "completed" if message.stop_reason == "end_turn" else "incomplete",
            "usage": {"input_tokens": sum(counts[:3]), "output_tokens": counts[3],
                      "cached_input_tokens": counts[2]}}


class AnthropicCompletionClient(CompletionClient):
    """Claude via the Anthropic SDK with the same deadline, semaphore and observer contract.

    Thinking is adaptive (always on for Claude Opus 5.5); effort is its only depth control.
    Server-side fallbacks re-run a safety-classifier refusal on another model in the same call.
    """

    def __init__(self, model, api_key, *, effort="low", max_output_tokens=16000, semaphore=None,
                 service_tier=None, base_url=None, speed=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("set ANTHROPIC_API_KEY for an Anthropic model")
        if speed not in (None, "fast"):
            raise ValueError("speed is 'fast' or unset")
        super().__init__(model, api_key, effort=effort, max_output_tokens=max_output_tokens,
                         semaphore=semaphore, service_tier=service_tier)
        self.url, self.headers, self.api_key = ANTHROPIC_URL, {}, api_key
        # Fast mode: the same model and effort at ~2.3x output speed (204 vs 90 tok/s measured); latency is
        # almost all output tokens (hosted v16: r = 0.99, ~11 s per 1k tokens), so decisions take ~half the time.
        self.speed = speed

    async def _request(self, system, user, deadline, observer=None):
        import anthropic

        async with asyncio.timeout(_remaining(deadline)):
            async with anthropic.AsyncAnthropic(api_key=self.api_key, max_retries=0,
                                                timeout=_remaining(deadline)) as client:
                request = dict(
                    model=self.model,
                    max_tokens=self.max_output_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": self.effort},
                    fallbacks="default",
                )
                message = None
                if self.speed == "fast":
                    try:
                        message = await client.beta.messages.create(
                            speed="fast", betas=["server-side-fallback-2026-07-01", "fast-mode-2026-02-01"], **request)
                    except anthropic.RateLimitError:  # fast mode has its own rate limit: standard speed instead
                        message = None
                if message is None:
                    message = await client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], **request)
                if observer is not None:
                    observer(anthropic_metadata(message))
                if message.stop_reason != "end_turn":
                    raise ValueError(f"model stopped with {message.stop_reason}")
                result = "".join(block.text for block in message.content if block.type == "text")
                if not result.strip():
                    raise ValueError("model response has no decision text")
                _remaining(deadline)
                return result


def routed_completions_from_env():
    """Read explicit process configuration; the strong route is disabled unless configured.

    LLM_PROVIDER=anthropic uses LLM_API_KEY_ANTHROPIC (or ANTHROPIC_API_KEY) and defaults to claude-opus-5-5."""
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider not in ("openai", "anthropic"):
        raise ValueError("the routed agent supports LLM_PROVIDER=openai or anthropic")
    maximum = int(os.environ.get("LLM_MAX_CONCURRENT", "10"))
    if maximum < 1:
        raise ValueError("LLM_MAX_CONCURRENT must be positive")
    if provider == "anthropic":
        client, default_model = AnthropicCompletionClient, "claude-opus-5-5"
        key = os.environ.get("LLM_API_KEY_ANTHROPIC") or os.environ.get("ANTHROPIC_API_KEY")
        shared = {"api_key": key, "semaphore": threading.BoundedSemaphore(maximum)}
    else:
        client, default_model = CompletionClient, "gpt-6.1-sol"
        shared = {
            "api_key": os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            "semaphore": threading.BoundedSemaphore(maximum),
        }
    if provider == "anthropic" and os.environ.get("LLM_SPEED"):
        shared["speed"] = os.environ["LLM_SPEED"]
    fast = client(os.environ.get("LLM_MODEL", default_model), effort=os.environ.get("LLM_EFFORT", "low"), **shared)
    strong_model = os.environ.get("LLM_STRONG_MODEL", "").strip()
    strong = (client(strong_model, effort=os.environ.get("LLM_STRONG_EFFORT", "medium"), **shared)
              if strong_model else None)
    return fast, strong
