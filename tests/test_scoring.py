"""Scoring accuracy: root-cause de-duplication and capped penalties."""
import json

from webdoctor.models import (
    CheckResult, CheckStatus, Issue, Severity, TestReport,
)


def _issue(issue_id, category, severity, summary="Synthetic issue", detail="", url=""):
    return Issue(
        id=issue_id,
        category=category,
        severity=severity,
        title="Synthetic issue",
        summary=summary,
        detail=detail or f"Target: {url}",
    )


def _report(groups):
    """groups: list of (name, category, [issues])"""
    results = [
        CheckResult(name=name, category=category, status=CheckStatus.FAILED, issues=issues)
        for name, category, issues in groups
    ]
    return TestReport(url="https://example.com", check_results=results).compute_summary()


def test_same_404_from_three_suites_counts_once():
    url = "https://example.com/missing"
    http = _issue("HTTP_4XX", "http", Severity.ERROR,
                  "The URL returned an error response (HTTP 404).",
                  f"Status: 404 (Not Found). Final URL: {url}.", url)
    link = _issue("LINK_BROKEN_1", "links", Severity.ERROR,
                  "The link on this page returns HTTP 404.",
                  f"Source tag: <a>\nTarget: {url}\nResult: status=404, error=None", url)
    browser = _issue("NET_FAIL_1", "browser", Severity.ERROR,
                     f"The browser could not load a script resource: {url}",
                     f"Resource type: script\nURL: {url}\nStatus: 404 (Not Found)", url)
    report = _report([
        ("HTTP", "http", [http]),
        ("Links", "links", [link]),
        ("Browser", "browser", [browser]),
    ])

    assert len(report.issues) == 1
    assert report.issues[0].extra["occurrences"] == 3
    assert report.score == 85
    assert report.score_breakdown["duplicates_collapsed"] == 2
    # Details are retained in the per-suite lists.
    assert sum(len(cr.issues) for cr in report.check_results) == 3


def test_twenty_warnings_in_one_category_do_not_zero_the_score():
    warnings = [
        _issue("SEO_SUBOPTIMAL_DESCRIPTION", "seo", Severity.WARNING, url=f"https://example.com/p{i}")
        for i in range(20)
    ]
    report = _report([("SEO", "seo", warnings)])
    assert report.score_breakdown["capped_warnings_by_category"] == {"seo": 5}
    assert report.score == 80
    assert report.overall_grade == "B"
    assert report.overall_status == CheckStatus.WARN


def test_critical_issue_always_fails():
    report = _report([("HTTP", "http", [_issue("HTTP_5XX", "http", Severity.CRITICAL)])])
    assert report.score == 70
    assert report.overall_status == CheckStatus.FAILED

    many = _report([("HTTP", "http", [
        _issue("HTTP_5XX", "http", Severity.CRITICAL, detail=f"status {code}")
        for code in (500, 502, 503, 504)
    ])])
    assert many.score == 0
    assert many.overall_status == CheckStatus.FAILED


def test_score_breakdown_is_exported_to_json():
    report = _report([("SEO", "seo", [
        _issue("SEO_NO_H1", "seo", Severity.WARNING, url=f"https://example.com/{i}")
        for i in range(2)
    ])])
    data = json.loads(report.to_json())
    breakdown = data["score_breakdown"]
    assert breakdown["penalties"]["warning"] == 8
    assert breakdown["raw_counts"]["warning"] == 2
    assert breakdown["deduplicated_counts"]["warning"] == 2
