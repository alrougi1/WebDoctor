"""Example: run WebDoctor inside a pytest test suite."""
import pytest
from webdoctor import run_diagnostics, Severity, CheckStatus

SITE = "https://example.com"


@pytest.fixture(scope="session")
def report():
    return run_diagnostics(
        SITE,
        browser_mode=True,
        check_links=True,
        ai=False,  # free, rule-based; no API credits needed
        timeout=25,
    )


def test_site_reachable(report):
    assert report.metrics.get("status_code") == 200


def test_no_critical_issues(report):
    criticals = [i for i in report.issues if i.severity == Severity.CRITICAL]
    assert criticals == [], [(c.id, c.title, c.fix) for c in criticals]


def test_health_grade(report):
    assert report.score >= 70, f"Score {report.score}/100 below threshold"
    assert report.overall_grade in ("A", "B", "C")


def test_no_javascript_runtime_errors(report):
    assert report.metrics.get("js_error_count", 0) == 0


def test_no_broken_links(report):
    broken = report.metrics.get("broken_count", 0)
    assert broken < 3, f"{broken} broken links found"
