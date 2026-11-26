"""Dashboard rendering, static assets, and job lifecycle tests."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from webdoctor import server
from webdoctor.server import app as server_app


AUTH = {"X-WebDoctor-Token": server_app.DASHBOARD_TOKEN}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server_app, "jobs", {})
    with TestClient(server_app.app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def test_dashboard_renders_and_static_assets_load(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "v1.0.0" in response.text
    assert "__VER__" not in response.text
    assert "/static/dashboard.css" in response.text
    assert "/static/dashboard.js" in response.text

    css = client.get("/static/dashboard.css")
    js = client.get("/static/dashboard.js")
    assert css.status_code == 200 and "body" in css.text
    assert js.status_code == 200 and "ai_base" not in js.text
    assert client.get("/api/models", headers=AUTH).status_code == 200
    assert client.get("/api/progress/unknown-job", headers=AUTH).status_code == 404


def test_progress_exposes_only_whitelisted_fields(client):
    server_app.jobs["abc123def456"] = {
        "status": "running",
        "percent": 42,
        "message": "Working",
        "created": 1.0,
        "config": {"url": "https://example.com"},
        "password": "super-secret",
        "ai_key": "sk-secret",
        "session_json": '{"cookies": []}',
    }
    data = client.get("/api/progress/abc123def456", headers=AUTH).json()
    assert set(data) == {"status", "percent", "message", "created", "report"}
    assert "super-secret" not in str(data)
    assert "sk-secret" not in str(data)


def test_diagnose_rejects_custom_ai_base_and_private_url(client):
    base = {"url": "https://example.com", "browser": False, "links": False, "ai": False}
    response = client.post("/api/diagnose", json={**base, "ai_base": "http://evil.example"}, headers=AUTH)
    assert response.status_code == 400
    assert "Custom AI base" in response.json()["detail"]
    # Loopback is intentionally allowed for local demos; other private ranges are not.
    response = client.post("/api/diagnose", json={**base, "url": "http://10.0.0.5/admin"}, headers=AUTH)
    assert response.status_code == 400


def test_report_file_rejects_invalid_job_id(client):
    response = client.get("/api/reports/not-a-valid-job/html", headers=AUTH)
    assert response.status_code == 400


def test_export_buttons_use_native_blob_downloads(client):
    html = client.get("/").text
    js = client.get("/static/dashboard.js").text
    assert 'class="sec export-btn" data-fmt="html"' in html
    for fmt in ("html", "md", "json", "pdf"):
        assert f'data-fmt="{fmt}"' in html
    assert 'target="_blank"' not in html
    assert "URL.createObjectURL" in js
    assert "a.download='webdoctor-'+id+'.'+fmt" in js
    assert "downloadReport(id, fmt, btn)" in js


def test_report_file_serves_as_attachment(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "REPORT_DIR", str(tmp_path))
    job_dir = tmp_path / "abc123def456"
    job_dir.mkdir()
    (job_dir / "report.html").write_text("<h1>report</h1>", encoding="utf-8")
    response = client.get("/api/reports/abc123def456/html", headers=AUTH)
    assert response.status_code == 200
    disposition = response.headers["content-disposition"].lower()
    assert "attachment" in disposition
    assert "webdoctor-abc123def456.html" in disposition


def test_job_pruning_and_capacity(monkeypatch):
    monkeypatch.setattr(server_app, "jobs", {})
    monkeypatch.setattr(server_app, "MAX_JOBS", 2)
    monkeypatch.setattr(server_app, "JOB_TTL_SECONDS", 10)

    assert server_app._reserve_job_slot(0.0) is True
    server_app.jobs["j1"] = {"status": "running", "created": 0.0}
    assert server_app._reserve_job_slot(1.0) is True
    server_app.jobs["j2"] = {"status": "completed", "created": 1.0}
    # Full, but the finished job is evicted to make room.
    assert server_app._reserve_job_slot(2.0) is True
    assert "j2" not in server_app.jobs
    assert "j1" in server_app.jobs

    # A running-only queue is never evicted.
    server_app.jobs["j3"] = {"status": "running", "created": 2.0}
    assert server_app._reserve_job_slot(3.0) is False

    # Expired finished jobs are pruned too.
    server_app.jobs["j4"] = {"status": "failed", "created": 0.0}
    server_app._prune_jobs_locked(100.0)
    assert "j4" not in server_app.jobs

    # Updates to a pruned job are ignored rather than raising.
    server_app._update_job("gone", status="completed")
    assert server_app._get_job("gone") is None


def test_background_run_tolerates_pruned_job(monkeypatch):
    monkeypatch.setattr(server_app, "jobs", {})
    request = server_app.DiagnoseRequest(url="https://example.com")
    # Must not raise a KeyError when the job disappeared before the worker ran.
    server_app._background_run("pruned", request)


def test_login_test_validates_url(client):
    response = client.post("/api/login-test", json={
        "login_url": "file:///etc/passwd", "username": "u", "password": "p",
    }, headers=AUTH)
    assert response.status_code == 400


def test_api_requires_local_dashboard_token(client):
    assert client.get("/api/models").status_code == 401
    bad = {"X-WebDoctor-Token": "not-the-token"}
    assert client.get("/api/models", headers=bad).status_code == 401
    assert client.post("/api/diagnose", json={"url": "https://example.com"}).status_code == 401
    assert client.get("/api/progress/anything", headers=bad).status_code == 401
    assert client.get("/api/reports/anything/html").status_code == 401
    assert client.post("/api/login-test", json={
        "login_url": "https://example.com", "username": "u", "password": "p",
    }, headers=bad).status_code == 401


@pytest.mark.parametrize("host", [
    "evil.example",
    "127.0.0.1.evil.example",
    "localhost.evil.test",
    "[::ffff:10.0.0.5]",
])
def test_non_loopback_host_is_rejected(client, host):
    response = client.get("/", headers={"Host": host})
    assert response.status_code == 400
    assert "loopback" in response.json()["detail"]


def test_loopback_host_with_port_is_allowed(client):
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_dashboard_sends_hardening_headers_and_no_inline_handlers(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "onclick=" not in response.text
    assert "onchange=" not in response.text
    assert 'meta name="webdoctor-token"' in response.text


def test_token_is_injected_into_page_but_not_static_js(client):
    response = client.get("/")
    assert server_app.DASHBOARD_TOKEN in response.text
    js = client.get("/static/dashboard.js").text
    assert server_app.DASHBOARD_TOKEN not in js
    assert "apiFetch" in js
