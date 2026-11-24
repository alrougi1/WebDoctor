"""Deterministic PDF report generation via WeasyPrint.

WeasyPrint is the only supported renderer: the previous ``manus-md-to-pdf``
path depended on a Manus-only CLI and produced environment-specific output.
If WeasyPrint or its system libraries are unavailable, PDF generation is
skipped with a clear warning and the other report formats are unaffected.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from webdoctor.models import TestReport

log = logging.getLogger(__name__)


def generate_pdf(report: TestReport, output_path: str) -> bool:
    """Write a PDF report with WeasyPrint; return False when PDF is unavailable."""
    output = Path(os.path.abspath(output_path))
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        from weasyprint import HTML  # type: ignore
        from webdoctor.reporters.html_report import generate_html

        HTML(string=generate_html(report)).write_pdf(str(output))
    except ImportError as e:
        log.warning("PDF generation skipped: WeasyPrint is not installed (%s)", e)
        return False
    except Exception as e:
        log.warning(
            "PDF generation skipped: WeasyPrint could not render this report (%s). "
            "Install WeasyPrint's system libraries or use the HTML/Markdown/JSON reports.",
            e,
        )
        return False

    if not output.exists() or output.stat().st_size == 0:
        log.warning("PDF generation skipped: WeasyPrint produced no output")
        return False
    return True
