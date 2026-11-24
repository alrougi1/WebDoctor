"""WebDoctor: developer-first website testing & root-cause diagnostics."""
from webdoctor.runner import run_diagnostics
from webdoctor.models import TestReport, CheckResult, Issue, Severity, CheckStatus
from webdoctor.config import TOOL_VERSION

__version__ = TOOL_VERSION
__all__ = ["run_diagnostics", "TestReport", "CheckResult", "Issue", "Severity", "CheckStatus"]
