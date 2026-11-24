"""HTML retrieval failures must not produce false SEO/a11y/link findings."""
import requests

import webdoctor.runner as runner
from webdoctor.models import CheckResult, CheckStatus


def test_html_fetch_failure_skips_html_based_suites(monkeypatch):
    def _fake_http(*_args, **_kwargs):
        return CheckResult(name="HTTP & Network Health", category="http", status=CheckStatus.PASSED)

    def _fake_security(*_args, **_kwargs):
        return CheckResult(name="Security Headers", category="security", status=CheckStatus.PASSED)

    def _reset(self, *_args, **_kwargs):
        raise requests.ConnectionError("connection reset by peer")

    monkeypatch.setattr(runner, "run_http_check", _fake_http)
    monkeypatch.setattr(runner, "run_security_check", _fake_security)
    monkeypatch.setattr(runner.requests.Session, "get", _reset)

    report = runner.run_diagnostics(
        "https://example.com", browser_mode=False, check_links=True, ai=False
    )
    ids = {i.id for i in report.issues}
    assert "HTML_FETCH_FAILED" in ids
    assert not any(i.id.startswith(("SEO_", "A11Y_", "LINK_BROKEN")) for i in report.issues)

    statuses = {cr.category: cr.status for cr in report.check_results}
    assert statuses["seo_a11y"] == CheckStatus.SKIPPED
    assert statuses["links"] == CheckStatus.SKIPPED
    assert report.metrics["links_found"] == 0
    assert report.metrics["links_checked"] == 0
