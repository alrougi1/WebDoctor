"""Data models for WebDoctor website testing & diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import json
import re


class Severity(str, Enum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CheckStatus(str, Enum):
    PASSED = "passed"
    WARN = "warn"
    FAILED = "failed"
    SKIPPED = "skipped"


SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.ERROR: 1,
    Severity.WARNING: 2,
    Severity.INFO: 3,
}

# Scoring policy (documented in README): critical issues are never capped;
# errors count at most 3 per category and warnings at most 5 per category.
SCORE_WEIGHTS = {Severity.CRITICAL: 30, Severity.ERROR: 15, Severity.WARNING: 4}
MAX_ERRORS_PER_CATEGORY = 3
MAX_WARNINGS_PER_CATEGORY = 5

_STATUS_RE = re.compile(r"\b([45]\d{2})\b")
_INDEX_SUFFIX_RE = re.compile(r"_\d+$")
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")


def _issue_target_url(issue: "Issue") -> str:
    explicit = issue.extra.get("url")
    if explicit:
        return str(explicit).rstrip(".,);")
    match = _URL_RE.search(f"{issue.summary} {issue.detail}")
    return match.group(0).rstrip(".,);") if match else ""


def _root_cause_key(issue: "Issue") -> Tuple[str, str, str]:
    """Stable fingerprint: cause category + normalized id + target URL.

    Status-based issues from different suites (HTTP_4XX, LINK_BROKEN_n,
    NET_FAIL_n) collapse to the same root cause when they describe the same
    HTTP status on the same URL.
    """
    base = _INDEX_SUFFIX_RE.sub("", issue.id.upper())
    status_match = _STATUS_RE.search(f"{issue.summary} {issue.detail}")
    status = status_match.group(1) if status_match else None
    if status and base.startswith(("HTTP_", "LINK_", "NET_")):
        return ("http_status", f"HTTP_{status}", _issue_target_url(issue))
    return (issue.category, base, _issue_target_url(issue))


@dataclass
class Issue:
    id: str
    category: str
    severity: Severity
    title: str
    summary: str
    detail: str = ""
    how_it_happened: str = ""
    reproduction: str = ""
    fix: str = ""
    source: str = "rule"
    evidence: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class CheckResult:
    name: str
    category: str
    status: CheckStatus
    duration_ms: int = 0
    issues: List[Issue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["issues"] = [i.to_dict() if isinstance(i, Issue) else i for i in self.issues]
        return d


@dataclass
class TestReport:
    # Imported into several test modules; pytest must not try to collect it.
    __test__ = False

    url: str
    target_title: str = ""
    final_url: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    duration_ms: int = 0
    overall_grade: str = "A"
    overall_status: CheckStatus = CheckStatus.PASSED
    score: int = 100
    check_results: List[CheckResult] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    screenshot_path: Optional[str] = None
    ai_summary: Optional[str] = None
    tool_version: str = "1.0.0"
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
    # Runner/environment failures (browser crash, HTML fetch failure, unreachable
    # security probe) are kept for visibility but never penalize the target site.
    environment_issues: List[Issue] = field(default_factory=list)

    def compute_summary(self) -> "TestReport":
        """De-duplicate root causes, then compute score, grade, and status."""
        all_issues = [i for cr in self.check_results for i in cr.issues]
        environment_issues = [
            i for i in all_issues if i.source == "environment"
        ]
        raw_issues = [
            i for i in all_issues if i.source != "environment"
        ]
        self.environment_issues = environment_issues
        raw_counts = {
            sev.value: sum(1 for i in raw_issues if i.severity == sev)
            for sev in Severity
        }

        # Collapse repeated root causes; every occurrence stays in the detailed
        # check_results lists, while self.issues holds one representative each.
        grouped: Dict[Tuple[str, str, str], Issue] = {}
        order: List[Tuple[str, str, str]] = []
        for issue in raw_issues:
            key = _root_cause_key(issue)
            if key not in grouped:
                fingerprint = "|".join(key)
                grouped[key] = replace(issue, extra=dict(issue.extra))
                grouped[key].extra["fingerprint"] = fingerprint
                grouped[key].extra["occurrences"] = 0
                order.append(key)
            representative = grouped[key]
            representative.extra["occurrences"] += 1
            if SEVERITY_RANK[issue.severity] < SEVERITY_RANK[representative.severity]:
                occurrences = representative.extra["occurrences"]
                fingerprint = representative.extra["fingerprint"]
                grouped[key] = replace(
                    issue,
                    extra={**issue.extra, "occurrences": occurrences, "fingerprint": fingerprint},
                )
        self.issues = [grouped[key] for key in order]

        crit = sum(1 for i in self.issues if i.severity == Severity.CRITICAL)
        errors_by_category: Dict[str, int] = {}
        warnings_by_category: Dict[str, int] = {}
        for issue in self.issues:
            if issue.severity == Severity.ERROR:
                errors_by_category[issue.category] = errors_by_category.get(issue.category, 0) + 1
            elif issue.severity == Severity.WARNING:
                warnings_by_category[issue.category] = warnings_by_category.get(issue.category, 0) + 1
        capped_errors = {
            cat: min(count, MAX_ERRORS_PER_CATEGORY)
            for cat, count in errors_by_category.items()
        }
        capped_warnings = {
            cat: min(count, MAX_WARNINGS_PER_CATEGORY)
            for cat, count in warnings_by_category.items()
        }
        penalties = {
            "critical": crit * SCORE_WEIGHTS[Severity.CRITICAL],
            "error": sum(capped_errors.values()) * SCORE_WEIGHTS[Severity.ERROR],
            "warning": sum(capped_warnings.values()) * SCORE_WEIGHTS[Severity.WARNING],
            "info": 0,
        }
        score = max(0, min(100, 100 - sum(penalties.values())))
        self.score = score
        eff_err = sum(capped_errors.values())

        if score >= 90:
            self.overall_grade = "A"
            self.overall_status = CheckStatus.PASSED
        elif score >= 75:
            self.overall_grade = "B"
            self.overall_status = CheckStatus.WARN if eff_err == 0 else CheckStatus.FAILED
        elif score >= 60:
            self.overall_grade = "C"
            self.overall_status = CheckStatus.FAILED
        elif score >= 40:
            self.overall_grade = "D"
            self.overall_status = CheckStatus.FAILED
        else:
            self.overall_grade = "F"
            self.overall_status = CheckStatus.FAILED

        if crit > 0:
            self.overall_status = CheckStatus.FAILED

        self.score_breakdown = {
            "formula": (
                "100 - 30*critical - 15*errors (max 3/category) "
                "- 4*warnings (max 5/category); info never penalized"
            ),
            "raw_counts": raw_counts,
            "deduplicated_counts": self.issue_counts(),
            "duplicates_collapsed": len(raw_issues) - len(self.issues),
            "environment_issue_count": len(environment_issues),
            "penalties": penalties,
            "capped_errors_by_category": capped_errors,
            "capped_warnings_by_category": capped_warnings,
        }
        return self

    def issue_counts(self) -> Dict[str, int]:
        counts = {Severity.CRITICAL.value: 0, Severity.ERROR.value: 0,
                  Severity.WARNING.value: 0, Severity.INFO.value: 0}
        for i in self.issues:
            counts[i.severity.value] += 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["overall_status"] = self.overall_status.value if hasattr(self.overall_status, "value") else str(self.overall_status)
        d["check_results"] = [cr.to_dict() if isinstance(cr, CheckResult) else cr for cr in self.check_results]
        d["issues"] = [i.to_dict() if isinstance(i, Issue) else i for i in self.issues]
        d["environment_issues"] = [
            i.to_dict() if isinstance(i, Issue) else i for i in self.environment_issues
        ]
        d["issue_counts"] = self.issue_counts()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
