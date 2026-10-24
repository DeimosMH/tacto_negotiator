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


@pytest.mark.parametrize("mode", ["complete", "partial", "failed"])
def test_final_points_are_per_wave_and_missing_results_are_unknown(monkeypatch, capsys, mode):
    cases = [{"case_id": "one", "wave": 1}, {"case_id": "two", "wave": 1}, {"case_id": "three", "wave": 2}]
    results = {"one": {"score": 1}, "two": RuntimeError("lost result") if mode == "partial" else {"score": 0},
               "three": {"score": 1}}
    if mode == "failed":
        results = dict.fromkeys(results, RuntimeError("lost results"))
    client = SimpleNamespace(practice_remaining=None, cases=lambda _: cases)
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "ArenaClient", lambda: client)
    monkeypatch.setattr(cli, "agent_factory", lambda *_: object)
    monkeypatch.setattr(cli, "play_waves", lambda *_: results)
    cli.main(["run", "--set", "final"])
    text = capsys.readouterr().out
    if mode != "complete":
        assert "total unknown" in text and "1: None" in text
        assert "points so far:" not in text
    else:
        assert "waves: {1: 50.0, 2: 100.0}" in text and "points so far: 150.0" in text
