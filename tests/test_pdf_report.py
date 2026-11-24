"""PDF generation must degrade gracefully and never break a diagnosis run."""
import builtins
import sys
from types import SimpleNamespace

from webdoctor.models import CheckResult, CheckStatus, TestReport
from webdoctor.reporters.pdf_report import generate_pdf


def _report():
    return TestReport(url="https://example.com").compute_summary()


def test_pdf_skips_when_weasyprint_is_missing(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise ImportError("WeasyPrint not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    output = tmp_path / "nested" / "report.pdf"
    assert generate_pdf(_report(), str(output)) is False
    assert not output.exists()


def test_pdf_skips_when_system_libraries_are_unavailable(tmp_path, monkeypatch):
    class _BrokenHTML:
        def __init__(self, *args, **kwargs):
            pass

        def write_pdf(self, *args, **kwargs):
            raise OSError("cannot load library 'libgobject-2.0-0'")

    monkeypatch.setitem(sys.modules, "weasyprint", SimpleNamespace(HTML=_BrokenHTML))
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
