"""Read-only report API checks against a temporary directory and loopback server."""

import json
import threading

import httpx
import pytest

from tacto_negotiator import web


@pytest.fixture
def dashboard(tmp_path):
    server = web.create_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            yield tmp_path, client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_only_competition_reports_are_listed_and_private_fields_are_removed(dashboard):
    root, client = dashboard
    report = {"config": {"scoring_version": 2, "api_key": "private"}, "budget": {"calls": ["private"]},
              "matches": [{"variant_id": "scripts", "cost_eur": None, "prompt": "private",
                           "terms": {"unit_price": 2, "unknown": "private"},
                           "decisions": [{"route": "script", "headers": {"Authorization": "private"},
                                          "standing_prices": [{"unit_price": 3, "floor": "private"}],
                                          "sent_action": {"action": "send", "body": "private",
                                                          "term_sheets": [{"unit_price": 2, "secret": "private"}]},
                                          "supplier_reply": {"status": "offer", "body": "private"}}]}]}
    (root / "current.json").write_text(json.dumps(report))
    (root / "checkpoint.json").write_text('{"status": "running"}')
    (root / "broken.json").write_text('{"matches":')
    (root / ".private.json").write_text(json.dumps(report))
    (root / "subdirectory.json").mkdir()
    assert client.get("/api/reports").json() == [
        {"name": "current.json", "matches": 1, "scoring_version": 2, "excluded": False}]
    response = client.get("/api/reports/current.json")
    assert response.status_code == 200
    assert "private" not in response.text and "prompt" not in response.text
    row = response.json()["matches"][0]
    assert row["cost_eur"] is None
    assert row["decisions"][0]["sent_action"]["term_sheets"] == [{"unit_price": 2}]
    assert row["decisions"][0]["supplier_reply"] == {"status": "offer"}


def test_legacy_empty_and_unicode_reports(dashboard):
    root, client = dashboard
    (root / "old.json").write_text('{"matches": [{"variant_id": "naive"}]}')
    (root / "empty ż.json").write_text('{"matches": []}')
    legacy = client.get("/api/reports/old.json").json()
    assert legacy["config"] == {} and legacy["matches"][0]["decisions"] == []
    assert client.get("/api/reports/empty%20%C5%BC.json").json()["matches"] == []


def test_routes_cannot_read_credentials_traverse_or_follow_symlinks(dashboard):
    root, client = dashboard
    (root / ".env").write_text("FAKE_TEST_KEY=not-a-real-credential")
    outside = root.parent / "outside.json"
    outside.write_text('{"matches": []}')
    (root / "linked.json").symlink_to(outside)
    for route in ("/.env", "/api/reports/.env", "/api/reports/%2e%2e%2foutside.json",
                  "/api/reports/linked.json", "/api/reports/%2Fetc%2Fpasswd.json", "/web.py"):
        assert client.get(route).status_code == 404, route
    assert client.get("/api/reports").json() == []
    assert client.post("/api/reports/test.json", json={"matches": []}).status_code == 501
    assert not (root / "test.json").exists()


def test_local_host_and_static_assets(dashboard):
    _, client = dashboard
    for host in ("attacker.example", "[invalid"):
        assert client.get("/api/reports", headers={"Host": host}).status_code == 403
    for path, mime in (("/", "text/html"), ("/styles.css", "text/css"), ("/dashboard.js", "text/javascript")):
        response = client.get(path)
        assert response.status_code == 200 and response.content
        assert response.headers["content-type"].startswith(mime)
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


def test_bad_report_has_clean_error_and_server_keeps_working(dashboard):
    root, client = dashboard
    for data in ("{", '{"matches": [null]}', '{"matches": [{"decisions": [null]}]}',
                 '{"matches": [{"decisions": [{"sent_action": {"term_sheets": null}}]}]}'):
        (root / "bad.json").write_text(data)
        response = client.get("/api/reports/bad.json")
        assert response.status_code == 400
        assert str(root) not in response.text
        assert client.get("/api/reports").json() == []
    assert client.get("/api/reports/missing.json").status_code == 404


def test_report_size_limit(tmp_path, monkeypatch):
    path = tmp_path / "large.json"
    path.write_text('{"matches": []}' + " " * 100)
    monkeypatch.setattr(web, "MAX_REPORT_BYTES", 50)
    with pytest.raises(ValueError, match="32 MB"):
        web.read_report(path)


def test_invalid_directory_and_help_do_not_start_services(tmp_path, monkeypatch, capsys):
    with pytest.raises(ValueError, match="directory"):
        web.create_server(tmp_path / "missing")
    monkeypatch.setattr(web, "create_server", lambda *_: pytest.fail("help must not start a server"))
    with pytest.raises(SystemExit) as result:
        web.main(["--help"])
    assert result.value.code == 0
    assert "--reports" in capsys.readouterr().out
