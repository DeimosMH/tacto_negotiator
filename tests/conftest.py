import socket
import threading
import time

import pytest


class Clock:
    """A clock the tests move by hand, so the 60-second rule is testable without waiting."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def sim():
    """tacto-sim on a free local port: (base_url, Sim, Clock). Event code 'sim'."""
    pytest.importorskip("fastapi")
    import uvicorn

    from tacto_negotiator.sim import Sim, create_app, load_cases

    clock = Clock()
    state = Sim(load_cases(), clock=clock)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(state), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}", state, clock
    server.should_exit = True
    thread.join()


@pytest.fixture(autouse=True)
def no_deal_journal(monkeypatch):
    """Tests never write to docs/deals (deals.py); a test that checks the journal sets its own folder."""
    monkeypatch.setenv("TACTO_DEALS_DIR", "off")
