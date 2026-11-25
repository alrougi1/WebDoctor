"""Runner/environment failures must be visible but never lower the site score."""
from webdoctor.diagnostics.rule_engine import generate_rule_summary
from webdoctor.models import CheckResult, CheckStatus, Issue, Severity, TestReport


def _report_with(*issues):
    report = TestReport(url="https://example.com")
    report.check_results.append(
        CheckResult(name="Suite", category="test", status=CheckStatus.FAILED, issues=list(issues))
    )
    return report.compute_summary()


def test_environment_issue_is_not_scored():
    report = _report_with(
        Issue(id="SITE_ERROR", category="http", severity=Severity.ERROR,
              title="Site error", summary="Real defect"),
        Issue(id="BROWSER_LAUNCH_FAILED", category="browser", severity=Severity.CRITICAL,
              source="environment", title="Browser crash", summary="Runner failed"),
    )
    assert report.score == 85
    assert report.issue_counts()["critical"] == 0
    assert report.issue_counts()["error"] == 1
    assert len(report.environment_issues) == 1
    assert report.score_breakdown["environment_issue_count"] == 1
    assert "environment_issues" in report.to_dict()


def test_only_environment_issues_do_not_claim_all_checks_passed():
    report = _report_with(
        Issue(id="HTML_FETCH_FAILED", category="http", severity=Severity.ERROR,
              source="environment", title="Fetch failed", summary="Runner failed")
    )
    assert report.score == 100
    summary = generate_rule_summary(report)
    assert "Runner note: 1 not scored" in summary
    assert "All automated checks passed" not in summary
