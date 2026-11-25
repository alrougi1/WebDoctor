"""HTTP check labels and classifications must match what was measured."""
from types import SimpleNamespace

import webdoctor.checks.http_check as http_check


class _Response:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.reason = "Unauthorized" if status == 401 else "Server Error"
        self.url = "https://example.com/"
        self.history = []
        self.headers = headers or {}
        self.content = text.encode("utf-8")
        self.text = text
        self.encoding = "utf-8"
        self.raw = SimpleNamespace(version=11)


def _run(monkeypatch, response):
    monkeypatch.setattr(http_check.requests.Session, "get", lambda *_a, **_k: response)
    return http_check.run_http_check("https://example.com")


def test_401_is_warning_not_error(monkeypatch):
    result = _run(monkeypatch, _Response(401))
    issue = next(i for i in result.issues if i.id == "HTTP_4XX")
    assert issue.severity.value == "warning"
    assert result.status.value == "warn"


def test_total_time_metric_is_available_and_labeled_correctly(monkeypatch):
    result = _run(monkeypatch, _Response(200))
    assert "total_response_ms" in result.metrics
    assert result.metrics["total_response_ms"] == result.metrics["response_time_ms"]


def test_5xx_evidence_is_redacted(monkeypatch):
    body = "token=SUPERSECRET123 user@example.com\nTraceback line"
    result = _run(monkeypatch, _Response(500, body, {"Content-Type": "text/html"}))
    issue = next(i for i in result.issues if i.id == "HTTP_5XX")
    assert "SUPERSECRET123" not in issue.evidence
    assert "user@example.com" not in issue.evidence
    assert "[REDACTED" in issue.evidence


def test_redirect_chain_is_structured(monkeypatch):
    hop = SimpleNamespace(
        status_code=301, url="http://example.com/", headers={"Location": "https://example.com/"}
    )
    response = _Response(200)
    response.history = [hop]
    result = _run(monkeypatch, response)
    assert result.metrics["redirect_chain"] == [
        {"status": 301, "url": "http://example.com/", "location": "https://example.com/"}
    ]
