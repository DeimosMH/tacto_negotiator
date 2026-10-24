"""Per-read timeout/retry controls without changing arena POST idempotency or defaults."""

import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from tacto_negotiator.client import ArenaClient, ArenaError


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(sys.modules["tacto_negotiator.client"], "time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None))


@pytest.mark.parametrize(("method", "args", "path", "raw"), [
    ("session", ("s1",), "/v1/sessions/s1", False),
    ("thread", ("s1", "sup:A"), "/v1/sessions/s1/threads/sup:A", False),
    ("attachment", ("s1", "att:A"), "/v1/sessions/s1/attachments/att:A", True),
])
def test_read_overrides_do_not_change_client_defaults(no_wait, method, args, path, raw):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, content=b"document" if raw else b'{"ok":true}')

    client = ArenaClient("http://arena.test", "local-only", timeout=180, transport=httpx.MockTransport(handle))
    try:
        out = getattr(client, method)(*args, timeout=3, retries=0)
        assert out == (b"document" if raw else {"ok": True})
        getattr(client, method)(*args)
    finally:
        client.http.close()
    assert all(request.url.path == path for request in seen)
    assert seen[0].extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 3)
    assert seen[1].extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 180)
    assert client.retries == 3


@pytest.mark.parametrize("fault", ["status", "transport"])
def test_zero_retries_stops_a_transient_read_failure(no_wait, fault):
    seen = []

    def handle(request):
        seen.append(request)
        if fault == "transport":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(503, json={"detail": "synthetic unavailable"})

    client = ArenaClient("http://arena.test", "local-only", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(httpx.ReadTimeout if fault == "transport" else ArenaError):
            client.session("s1", timeout=2, retries=0)
    finally:
        client.http.close()
    assert len(seen) == 1


def test_default_retry_and_post_move_id_are_preserved(no_wait):
    seen = []

    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(200, json={"ok": True})

    client = ArenaClient("http://arena.test", "local-only", transport=httpx.MockTransport(handle))
    try:
        assert client.open_session("case:S01") == {"ok": True}
    finally:
        client.http.close()
    assert len(seen) == 2
    assert seen[0].headers["X-Move-Id"] == seen[1].headers["X-Move-Id"]


@pytest.mark.parametrize("retries", [-1, True, 1.5])
def test_invalid_retry_override_fails_before_transport(retries):
    client = ArenaClient("http://arena.test", "local-only",
                         transport=httpx.MockTransport(lambda request: pytest.fail("must not send")))
    try:
        with pytest.raises(ValueError, match="retries"):
            client.session("s1", retries=retries)
    finally:
        client.http.close()
