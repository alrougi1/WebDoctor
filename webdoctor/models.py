"""Data models for WebDoctor website testing & diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import json


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

    def compute_summary(self) -> "TestReport":
        """Compute the score, grade, and aggregate issues."""
        self.issues = []
        for cr in self.check_results:
            self.issues.extend(cr.issues)

        # Count penalties
        crit = sum(1 for i in self.issues if i.severity == Severity.CRITICAL)
        err = sum(1 for i in self.issues if i.severity == Severity.ERROR)
        warn = sum(1 for i in self.issues if i.severity == Severity.WARNING)

        score = 100 - (crit * 30) - (err * 15) - (warn * 4)
        score = max(0, min(100, score))
        self.score = score

        if score >= 90:
            self.overall_grade = "A"
            self.overall_status = CheckStatus.PASSED
        elif score >= 75:
            self.overall_grade = "B"
            self.overall_status = CheckStatus.WARN if err == 0 else CheckStatus.FAILED
        elif score >= 60:
            self.overall_grade = "C"
            self.overall_status = CheckStatus.FAILED
        elif score >= 40:
            self.overall_grade = "D"
            self.overall_status = CheckStatus.FAILED
        else:
            self.overall_grade = "F"
            self.overall_status = CheckStatus.FAILED

        # Critical issues force failed status
        if crit > 0:
            self.overall_status = CheckStatus.FAILED

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
        d["issue_counts"] = self.issue_counts()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
