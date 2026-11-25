"""The crimson dark theme and original emblem must be present in reports/UI."""
from pathlib import Path

from webdoctor.models import TestReport
from webdoctor.reporters.html_report import generate_html


ROOT = Path(__file__).resolve().parents[1]


def test_html_report_embeds_original_emblem_and_dark_theme():
    html = generate_html(TestReport(url="https://example.com").compute_summary())
    assert "<svg" in html
    assert "WebDoctor crimson diagnostic emblem" in html
    assert "--bg:#040407" in html
    assert "--accent:#ff2d55" in html
    assert "/static/webdoctor-emblem.svg" not in html


def test_dashboard_uses_emblem_asset():
    template = (ROOT / "webdoctor" / "server" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    css = (ROOT / "webdoctor" / "server" / "static" / "dashboard.css").read_text(encoding="utf-8")
    emblem = ROOT / "webdoctor" / "server" / "static" / "webdoctor-emblem.svg"
    assert "webdoctor-emblem.svg" in template
    assert "brand-center" in template
    assert "brand-mark" in css
    assert "justify-content:center" in css
    assert "font-size:54px" in css
    assert "width:76px" in css
    assert "--bg:#040407" in css
    assert emblem.exists() and "<svg" in emblem.read_text(encoding="utf-8")
