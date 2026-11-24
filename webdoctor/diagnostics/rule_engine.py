"""Rule-based diagnostic engine: enhances issues and produces an actionable quick-fix checklist."""
from __future__ import annotations

from typing import List, Dict, Any
from webdoctor.models import TestReport, Issue, Severity


SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.ERROR: 1,
    Severity.WARNING: 2,
    Severity.INFO: 3,
}


def build_quickfix_checklist(report: TestReport) -> List[Dict[str, Any]]:
    """Return issues sorted critical-first, de-duplicated and prioritized."""
    sorted_issues = sorted(
        report.issues,
        key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), -len(i.detail)),
    )
    items = []
    for issue in sorted_issues:
        items.append({
            "id": issue.id,
            "severity": issue.severity.value,
            "title": issue.title,
            "category": issue.category,
            "fix": issue.fix or "Review detailed findings in the report.",
            "reproduction": issue.reproduction,
            "how": issue.how_it_happened,
        })
    return items


def generate_rule_summary(report: TestReport) -> str:
    counts = report.issue_counts()
    crit = counts["critical"]
    err = counts["error"]
    warn = counts["warning"]

    lines = []
    if crit == 0 and err == 0 and warn == 0:
        lines.append(
            f"**Site health: excellent.** All automated checks passed for `{report.url}`. "
            f"The site responded with grade {report.overall_grade} (score {report.score}/100) and no issues were detected."
        )
        return "\n\n".join(lines)

    lines.append(
        f"**Site health: grade {report.overall_grade} (score {report.score}/100)**. "
        f"WebDoctor found **{crit} critical**, **{err} error(s)**, **{warn} warning(s)**, "
        f"and {counts['info']} informational item(s) for `{report.url}`."
    )

    if crit > 0:
        crit_titles = [i.title for i in report.issues if i.severity == Severity.CRITICAL][:3]
        lines.append(
            f"**Immediate action required:** " + ", ".join(f"`{t}`" for t in crit_titles) +
            ". These failures prevent users or automated clients from reaching or using the site reliably."
        )

    if err > 0:
        err_titles = [i.title for i in report.issues if i.severity == Severity.ERROR][:4]
        lines.append(
            f"**Errors to fix next:** " + ", ".join(f"`{t}`" for t in err_titles) +
            ". Each issue below includes a root-cause explanation ('how it happened') and exact reproduction commands."
        )

    return "\n\n".join(lines)
