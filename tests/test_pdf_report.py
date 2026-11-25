"""PDF generation must degrade gracefully and never break a diagnosis run."""
import builtins
import sys
from types import SimpleNamespace

from webdoctor.models import CheckResult, CheckStatus, TestReport
from webdoctor.reporters import pdf_report
from webdoctor.reporters.pdf_report import generate_pdf


def _report():
    return TestReport(url="https://example.com").compute_summary()


def _block_weasyprint(monkeypatch):
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise ImportError("WeasyPrint not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)


def _block_chromium(monkeypatch):
    monkeypatch.setattr(
        pdf_report,
        "_render_with_chromium",
        lambda html, output: (False, "Chromium fallback deliberately blocked"),
    )


def test_pdf_skips_when_no_renderer_is_available(tmp_path, monkeypatch):
    _block_weasyprint(monkeypatch)
    _block_chromium(monkeypatch)
    output = tmp_path / "nested" / "report.pdf"
    assert generate_pdf(_report(), str(output)) is False
    assert not output.exists()


def test_pdf_skips_when_weasyprint_libraries_are_unavailable(tmp_path, monkeypatch):
    class _BrokenHTML:
        def __init__(self, *args, **kwargs):
            pass

        def write_pdf(self, *args, **kwargs):
            raise OSError("cannot load library 'libgobject-2.0-0'")

    monkeypatch.setitem(sys.modules, "weasyprint", SimpleNamespace(HTML=_BrokenHTML))
    _block_chromium(monkeypatch)
    output = tmp_path / "report.pdf"
    assert generate_pdf(_report(), str(output)) is False
    assert not output.exists()


def test_pdf_returns_true_only_for_nonempty_output(tmp_path, monkeypatch):
    class _WorkingHTML:
        def __init__(self, *args, **kwargs):
            pass

        def write_pdf(self, target, *args, **kwargs):
            with open(target, "wb") as f:
                f.write(b"%PDF-1.7 deterministic bytes")

    monkeypatch.setitem(sys.modules, "weasyprint", SimpleNamespace(HTML=_WorkingHTML))
    output = tmp_path / "report.pdf"
    assert generate_pdf(_report(), str(output)) is True
    assert output.read_bytes() == b"%PDF-1.7 deterministic bytes"


def test_pdf_uses_chromium_when_weasyprint_is_missing(tmp_path, monkeypatch):
    _block_weasyprint(monkeypatch)
    captured = {}

    def _fake_chromium(html, output):
        captured["html"] = html
        output.write_bytes(b"%PDF-1.7 chromium fallback")
        return True, ""

    monkeypatch.setattr(pdf_report, "_render_with_chromium", _fake_chromium)
    output = tmp_path / "report.pdf"
    assert generate_pdf(_report(), str(output)) is True
    assert output.read_bytes() == b"%PDF-1.7 chromium fallback"
    assert "WebDoctor Report" in captured["html"]


def test_pdf_skips_when_chromium_fallback_produces_no_file(tmp_path, monkeypatch):
    _block_weasyprint(monkeypatch)
    monkeypatch.setattr(
        pdf_report,
        "_render_with_chromium",
        lambda html, output: (True, ""),
    )
    output = tmp_path / "report.pdf"
    assert generate_pdf(_report(), str(output)) is False
    assert not output.exists()


def test_pdf_falls_back_when_weasyprint_native_import_fails(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def _broken_native_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken_native_import)

    def _fake_chromium(html, output):
        output.write_bytes(b"%PDF-1.7 native-import fallback")
        return True, ""

    monkeypatch.setattr(pdf_report, "_render_with_chromium", _fake_chromium)
    output = tmp_path / "report.pdf"
    assert generate_pdf(_report(), str(output)) is True
    assert output.read_bytes() == b"%PDF-1.7 native-import fallback"
