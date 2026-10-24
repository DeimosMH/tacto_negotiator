"""Sanitized accounting for explicitly authorized OpenAI-only benchmark calls.

Importing this module performs no I/O. Offline callers inject transports. The caller must opt
into live calls and explicitly select the key file; no Arena settings or environment are loaded.
Dollar amounts conservatively price every input token as a default 30-minute cache write,
plus standard output prices. They are upper estimates, not a billing reconciliation.
"""

import copy
import math
import re
import threading
import time
from decimal import ROUND_FLOOR, Decimal

import httpx

OPENAI_BASE = "https://api.openai.com/v1"
PRICES_PER_MILLION = {"gpt-6.1-sol": (2, 10), "gpt-6-astra": (10, 50), "gpt-6-luna": (0.1, 0.5)}
NANO = 1_000_000_000
INPUT_OVERHEAD = 1024
STANDARD_INPUT_LIMIT = 272_000


def read_llm_key(path):
    """Read only a literal LLM_API_KEY assignment; ignore every other setting without loading it."""
    found = None
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            match = re.fullmatch(r"\s*LLM_API_KEY\s*=\s*(.*?)\s*", line)
            if not match:
                continue
            if found is not None:
                raise ValueError("LLM_API_KEY is assigned more than once")
            value = match[1]
            if value.startswith(("'", '"')):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0 or value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#"):
                    raise ValueError("LLM_API_KEY must be a literal value")
                value = value[1:end]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if not value or any(c.isspace() for c in value) or any(c in value for c in "$`\\\x00"):
                raise ValueError("LLM_API_KEY must be a nonempty literal value")
            found = value
    if found is None:
        raise ValueError("LLM_API_KEY is missing from the selected file")
    return found


def preflight_models(api_key, requested, *, transport=None):
    """Check fixed OpenAI model listings, returning IDs/status only and never server error bodies."""
    requested = list(dict.fromkeys(requested))
    if any(model not in PRICES_PER_MILLION for model in requested):
        raise ValueError("preflight requires models with verified benchmark pricing")
    try:
        with httpx.Client(transport=transport, trust_env=False, timeout=10) as client:
            response = client.get(OPENAI_BASE + "/models", headers={"Authorization": f"Bearer {api_key}"})
        if response.status_code != 200:
            return {"status": "error", "http_status": response.status_code, "error_class": "HTTPStatusError",
                    "models": {model: "unknown" for model in requested}}
        data = response.json().get("data")
        if not isinstance(data, list):
            raise ValueError("invalid model listing")
        ids = {item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)}
        return {"status": "ok", "http_status": 200,
                "models": {model: "available" if model in ids else "not_listed" for model in requested}}
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        return {"status": "error", "error_class": type(error).__name__,
                "models": {model: "unknown" for model in requested}}


def estimated_nano(model, input_tokens, output_tokens):
    """All input at the 1.25x default cache-write rate; reasoning is already in output."""
    if model not in PRICES_PER_MILLION:
        raise ValueError("model has no verified benchmark price")
    if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
        raise ValueError("token counts must be nonnegative integers")
    input_rate, output_rate = PRICES_PER_MILLION[model]
    rates = (Decimal(str(input_rate)) * Decimal("1.25"), Decimal(str(output_rate)))
    return sum(int(rate * 1000) * count
               for rate, count in zip(rates, (input_tokens, output_tokens), strict=True))


class BudgetExceeded(RuntimeError):
    """The requested reservation does not fit inside the remaining authorized ceiling."""


class BudgetLedger:
    """Thread-safe reservations and sanitized call records, with integer currency accounting."""

    def __init__(self, cap_usd=60):
        if isinstance(cap_usd, bool) or not math.isfinite(cap_usd) or cap_usd <= 0:
            raise ValueError("budget must be finite and positive")
        self.cap_nano = int((Decimal(str(cap_usd)) * NANO).to_integral_value(rounding=ROUND_FLOOR))
        self.lock = threading.Lock()
        self.calls = []
        self.settled_nano = 0
        self.reserved_nano = 0
        self.rejected = 0
        self.halted = False

    def reserve(self, model, input_bound, output_bound):
        if input_bound > STANDARD_INPUT_LIMIT:
            raise ValueError("prompt exceeds the benchmark's standard input-price limit")
        amount = estimated_nano(model, input_bound, output_bound)
        with self.lock:
            if self.halted or self.settled_nano + self.reserved_nano + amount > self.cap_nano:
                self.rejected += 1
                raise BudgetExceeded("benchmark budget cannot reserve another request")
            call_id = len(self.calls)
            self.calls.append({"call_id": call_id, "model": model, "status": "in_flight",
                               "input_bound": input_bound, "output_bound": output_bound,
                               "reservation_nano": amount, "estimated_cost_nano": None,
                               "usage": None, "latency_s": None, "error_class": None})
            self.reserved_nano += amount
            return call_id

    def finish(self, call_id, *, latency_s, status, error_class=None, metadata=None):
        """Settle trustworthy usage; an unknown/cancelled call keeps its full reservation."""
        with self.lock:
            call = self.calls[call_id]
            if call["status"] != "in_flight":
                raise ValueError("benchmark call was already recorded")
            call.update(latency_s=round(latency_s, 6), status=status, error_class=error_class)
            if metadata is not None:
                call["provider_status"] = metadata.get("status", "unknown")
            usage = metadata.get("usage") if metadata else None
            if not isinstance(usage, dict):
                return
            if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
                return
            call["usage"] = {k: usage[k] for k in ("input_tokens", "output_tokens")}
            for name, maximum in (("reasoning_tokens", "output_tokens"), ("cached_input_tokens", "input_tokens")):
                value = usage.get(name)
                if type(value) is int and 0 <= value <= usage[maximum]:
                    call["usage"][name] = value
            if usage["input_tokens"] > call["input_bound"] or usage["output_tokens"] > call["output_bound"]:
                # Unexpected provider accounting must stop new spending; do not silently release its reservation.
                call["accounting_error"] = "usage_exceeds_reserved_bounds"
                self.halted = True
                return
            amount = estimated_nano(call["model"], usage["input_tokens"], usage["output_tokens"])
            call["estimated_cost_nano"] = amount
            self.reserved_nano -= call["reservation_nano"]
            self.settled_nano += amount

    def snapshot(self):
        with self.lock:
            calls = copy.deepcopy(self.calls)
            active = sum(c["reservation_nano"] for c in calls if c["status"] == "in_flight")
            for call in calls:
                call["reserved_usd"] = call.pop("reservation_nano") / NANO
                cost = call.pop("estimated_cost_nano")
                call["estimated_cost_usd"] = cost / NANO if cost is not None else None
            committed = self.settled_nano + self.reserved_nano
            return {"cap_usd": self.cap_nano / NANO,
                    "estimated_known_usage_usd": self.settled_nano / NANO,
                    "reserved_in_flight_usd": active / NANO,
                    "unreconciled_reservations_usd": (self.reserved_nano - active) / NANO,
                    "committed_usd": committed / NANO,
                    "remaining_usd": (self.cap_nano - committed) / NANO,
                    "budget_rejections": self.rejected, "halted": self.halted,
                    "price_basis": "Conservative standard estimate: all input at 1.25x default 30-minute "
                                   "cache-write rate, output at standard rate; not a billing reconciliation",
                    "calls": calls}


class BudgetedCompletion:
    """Reserve before dispatch and observe usage per call, never through mutable last-response state."""

    def __init__(self, client, ledger):
        if client.model not in PRICES_PER_MILLION:
            raise ValueError("model has no verified benchmark price")
        if client.url != OPENAI_BASE + "/responses" or client.service_tier != "default":
            raise ValueError("benchmark requests require the fixed OpenAI URL and standard service tier")
        self.client, self.ledger, self.model = client, ledger, client.model

    def __call__(self, system, user, *, deadline):
        if not isinstance(system, str) or not isinstance(user, str):
            raise ValueError("benchmark prompts must be strings")
        bound = len(system.encode("utf-8")) + len(user.encode("utf-8")) + INPUT_OVERHEAD
        call_id = self.ledger.reserve(self.model, bound, self.client.max_output_tokens)
        observed = []
        started = time.monotonic()
        status, error_class = "completed", None
        try:
            return self.client(system, user, deadline=deadline, observer=observed.append)
        except Exception as error:
            status = "timeout" if isinstance(error, (TimeoutError, httpx.TimeoutException)) else "error"
            error_class = type(error).__name__
            raise
        finally:
            self.ledger.finish(call_id, latency_s=time.monotonic() - started, status=status,
                               error_class=error_class, metadata=observed[-1] if observed else None)
