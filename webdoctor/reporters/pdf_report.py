"""PDF report generator: markdown -> manus-md-to-pdf, with weasyprint fallback."""
from __future__ import annotations

import os
import subprocess
import tempfile
import logging
from typing import Optional

from webdoctor.models import TestReport
from webdoctor.reporters.markdown_report import generate_markdown

log = logging.getLogger(__name__)


def generate_pdf(report: TestReport, output_path: str) -> bool:
    """Generate a PDF report. Tries manus-md-to-pdf first, then weasyprint."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    md_content = generate_markdown(report)

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(md_content)
        tmp_md = f.name

    try:
        # Method 1: manus-md-to-pdf CLI
        res = subprocess.run(
            ["manus-md-to-pdf", tmp_md, output_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return True
        log.warning("manus-md-to-pdf failed: %s", res.stderr)
    except FileNotFoundError:
        log.info("manus-md-to-pdf not found, trying weasyprint")
    except Exception as e:
        log.warning("manus-md-to-pdf error: %s", e)
    finally:
        try:
            os.unlink(tmp_md)
        except Exception:
            pass

    # Method 2: weasyprint fallback
    try:
        from weasyprint import HTML  # type: ignore
        from webdoctor.reporters.html_report import generate_html
        html = generate_html(report)
        HTML(string=html).write_pdf(output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        log.error("weasyprint fallback failed: %s", e)
        return False
