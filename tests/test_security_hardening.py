"""Security regression tests: dashboard secrecy, URL validation, report escaping."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import webdoctor.server.app as server
from webdoctor.models import TestReport, Issue, Severity, CheckResult, CheckStatus
from webdoctor.reporters.html_report import generate_html
from webdoctor.runner import validate_target_url


def test_validate_target_url_blocks_ssrf_targets():
    assert validate_target_url("example.com") == "https://example.com"
    assert validate_target_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    for bad in ("http://169.254.169.254/latest/meta-data", "http://10.0.0.5",
                "https://metadata.google.internal/"):
        with pytest.raises(ValueError):
            validate_target_url(bad)
    assert validate_target_url("http://10.0.0.5", allow_private=True) == "http://10.0.0.5"
    for bad_scheme in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            validate_target_url(bad_scheme)


def test_progress_endpoint_never_returns_submitted_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))

    def fake_run(*_args, **_kwargs):
        return TestReport(url="https://example.com").compute_summary()

    monkeypatch.setattr(server, "run_diagnostics", fake_run)
    client = TestClient(server.app)
    response = client.post("/api/diagnose", json={
        "url": "https://example.com",
        "pdf": False,
        "password": "SUPER_SECRET_PASSWORD",
        "ai_key": "KEY_SECRET",
        "session_json": '{"cookies":[{"name":"SESSION_SECRET"}]}',
        "ai_base": None,
    })
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    progress = None
    for _ in range(100):
        progress = client.get(f"/api/progress/{job_id}").json()
        if progress.get("status") in ("completed", "failed"):
            break
        time.sleep(0.02)
    assert progress is not None
    assert progress["status"] == "completed", progress
    body = json.dumps(progress)
    for secret in ("SUPER_SECRET_PASSWORD", "KEY_SECRET", "SESSION_SECRET"):
        assert secret not in body
    assert "password" not in progress


def test_diagnose_rejects_private_urls_and_custom_ai_base():
    client = TestClient(server.app)
    bad_url = client.post("/api/diagnose", json={
        "url": "http://169.254.169.254/latest/meta-data", "pdf": False,
    })
    assert bad_url.status_code == 400
    bad_base = client.post("/api/diagnose", json={
        "url": "https://example.com", "pdf": False, "ai_base": "http://internal.local/v1",
    })
    assert bad_base.status_code == 400


def test_html_report_sanitizes_ai_markdown_and_embedded_json():
    issue = Issue(
        id="XSS_TEST", category="test", severity=Severity.WARNING,
        title="payload", summary="payload",
        reproduction="</script><script>alert('repro')</script>",
    )
    report = TestReport(
        url="https://example.com",
        check_results=[CheckResult(name="t", category="test", status=CheckStatus.WARN, issues=[issue])],
        ai_summary="# Heading\n\n<img src=x onerror=alert(1)>\n\n<script>alert(2)</script>",
    ).compute_summary()
    html = generate_html(report)
    # Only the rendered diagnosis block is HTML; the reportData JSON block is a
    # JS string where '<' is inert and '</' is escaped to prevent script breakout.
    diag = html.split('<div class="diag-box">')[1].split("</div>")[0]
    assert "<script>alert(2)</script>" not in diag
    assert "onerror=alert(1)" not in diag
    assert "</script><script>alert('repro')</script>" not in html
    assert "<\\/script><script>alert('repro')<\\/script>" in html
    assert "alert('repro')" in html  # text is preserved, not executable
