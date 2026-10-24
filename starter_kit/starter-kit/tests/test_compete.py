"""CLI opt-in and fresh policy state checks; all providers and suppliers remain offline."""

import json

import pytest

from arena_kit.compete import ModelAgent, main, make_factories, write_report


def test_live_requires_explicit_opt_in_before_key_read(tmp_path):
    for arguments in (["--strategies", "sol"], ["--strategies", "sol", "--live"]):
        with pytest.raises(SystemExit) as error:
            main([*arguments, "--output", str(tmp_path / "report.json")])
        assert error.value.code == 2
    assert not list(tmp_path.iterdir())


def test_offline_run_never_loads_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("arena_kit.compete.read_llm_key", lambda _: pytest.fail("offline key read"))
    path = tmp_path / "report.json"
    assert main(["--scenarios", "simple_price", "--seeds", "0", "--waves", "1", "--output", str(path)]) == 0
    report = json.loads(path.read_text())
    assert report["status"] == "completed" and report["budget"] is None
    assert set(report["aggregate"]) == {"naive", "close", "scripts"}
    assert report["aggregate"]["scripts"]["errors"] == 0


def test_model_only_bypasses_scripts_but_preserves_guard():
    marker = object()
    factory = make_factories(["sol"], {"sol": marker})["sol"]
    first, second = factory(), factory()
    assert isinstance(first, ModelAgent) and first is not second
    assert first.fast is marker and first._script({}, {}) is None
    assert first.validate({}, {}, {"action": "accept"}) is None


def test_write_report_replaces_complete_json(tmp_path):
    path = tmp_path / "nested" / "report.json"
    write_report(path, {"status": "running"})
    write_report(path, {"status": "completed"})
    assert json.loads(path.read_text()) == {"status": "completed"}
    assert not path.with_suffix(".json.tmp").exists()
