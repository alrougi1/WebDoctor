"""JSON reporter for CI/CD integration."""
from __future__ import annotations

import json
from webdoctor.models import TestReport


def generate_json(report: TestReport, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, default=str)
