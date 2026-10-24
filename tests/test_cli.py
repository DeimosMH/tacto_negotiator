"""Buyer CLI dispatch without reading credentials or connecting to a server."""

from types import SimpleNamespace

import pytest

from tacto_negotiator import cli


def test_help_exits_before_loading_environment(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_env", lambda: pytest.fail("help must not read .env"))
    with pytest.raises(SystemExit) as ended:
        cli.main(["--help"])
    assert ended.value.code == 0
    assert "usage: tacto" in capsys.readouterr().out


@pytest.mark.parametrize(("argv", "policy", "data"), [
    (["play", "case:S01"], "routed", "/local/data"),
    (["run"], "routed", "/local/data"),
    (["play", "case:S01", "--agent", "naive"], "naive", "/local/data"),
    (["run", "--agent", "llm", "--data", "/explicit/data"], "llm", "/explicit/data"),
])
def test_buyer_default_and_explicit_policies(monkeypatch, argv, policy, data):
    selected = []
    client = SimpleNamespace(practice_remaining=None, session=lambda _: {}, cases=lambda _: [])
    monkeypatch.setattr(cli, "load_env", lambda: monkeypatch.setenv("ARENA_DATA", "/local/data"))
    monkeypatch.setattr(cli, "ArenaClient", lambda: client)
    monkeypatch.setattr(cli, "RemoteTable", lambda _: SimpleNamespace(session_id="local-only"))
    monkeypatch.setattr(cli, "play", lambda *_: {})
    monkeypatch.setattr(cli, "play_all", lambda *_: {})

    def factory(name, path):
        selected.append((name, path))
        return object

    monkeypatch.setattr(cli, "agent_factory", factory)
    cli.main(argv)
    assert selected == [(policy, data)]
