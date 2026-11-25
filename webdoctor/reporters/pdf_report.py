"""PDF report generation with a WeasyPrint-first, Chromium-fallback renderer.

WeasyPrint remains the preferred renderer because it produces stable,
server-side output. If WeasyPrint or its native libraries are unavailable,
WebDoctor falls back to the already-installed Playwright Chromium. If neither
renderer can produce a non-empty file, PDF generation is skipped with a clear
warning and the other report formats are unaffected.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Tuple

from webdoctor.models import TestReport

log = logging.getLogger(__name__)


def _render_with_weasyprint(html: str, output: Path) -> Tuple[bool, str]:
    """Render with WeasyPrint. Return (success, error_message)."""
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        return False, f"WeasyPrint is not installed ({e})"
    except Exception as e:
        # Native library failures happen during import on Windows.
        return False, str(e)

    try:
        HTML(string=html).write_pdf(str(output))
    except Exception as e:  # native library failures are not ImportError
        return False, str(e)
    return True, ""


def _render_with_chromium(html: str, output: Path) -> Tuple[bool, str]:
    """Render with headless Chromium. Return (success, error_message)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return False, f"Playwright is not installed ({e})"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.emulate_media(media="print")
                page.pdf(
                    path=str(output),
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "12mm",
                        "bottom": "12mm",
                        "left": "12mm",
                        "right": "12mm",
                    },
                )
            finally:
                browser.close()
    except Exception as e:
        return False, str(e)
    return True, ""


def _nonempty(output: Path) -> bool:
    return output.exists() and output.stat().st_size > 0


def generate_pdf(report: TestReport, output_path: str) -> bool:
    """Write a PDF report; return False only when no renderer can produce one."""
    output = Path(os.path.abspath(output_path))
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        from webdoctor.reporters.html_report import generate_html

        html = generate_html(report)
    except Exception as e:
        log.warning("PDF generation skipped: could not render report HTML (%s)", e)
        return False

    ok, error = _render_with_weasyprint(html, output)
    if ok and _nonempty(output):
        return True

    reason = error or "WeasyPrint produced no output"
    log.warning("WeasyPrint unavailable (%s); trying Chromium PDF fallback.", reason)
    ok, chromium_error = _render_with_chromium(html, output)
    if not ok or not _nonempty(output):
        reason = chromium_error or "Chromium produced no output"
        log.warning(
            "PDF generation skipped: no renderer succeeded (%s). "
            "Use the HTML/Markdown/JSON reports instead.",
            reason,
        )
        return False
    return True
