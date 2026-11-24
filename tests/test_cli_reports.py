"""CLI report-write resilience tests."""
import json

from webdoctor.cli import build_parser, cmd_check
from webdoctor.models import TestReport


def _args(tmp_path, *extra):
    return build_parser().parse_args([
        "check", "https://example.com", "--no-browser", "--no-links",
        "--no-ai", "--ci", "--report-dir", str(tmp_path / "reports"), *extra,
    ])


def _fake_diagnostics(*_args, **_kwargs):
    return TestReport(url="https://example.com").compute_summary()


def test_markdown_failure_does_not_stop_other_formats(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("webdoctor.cli.run_diagnostics", _fake_diagnostics)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr("webdoctor.cli.generate_markdown", _boom)
    code = cmd_check(_args(tmp_path))
    reports = tmp_path / "reports"
    assert code == 0
    assert not (reports / "report.md").exists()
    assert (reports / "report.html").exists()
    assert json.loads((reports / "report.json").read_text(encoding="utf-8"))["url"] == "https://example.com"
    assert "failed to write Markdown report" in capsys.readouterr().err


def test_pdf_failure_does_not_stop_other_formats(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("webdoctor.cli.run_diagnostics", _fake_diagnostics)
    monkeypatch.setattr("webdoctor.cli.generate_pdf", lambda *_a, **_k: False)
    code = cmd_check(_args(tmp_path, "--pdf"))
    reports = tmp_path / "reports"
    assert code == 0
    assert (reports / "report.html").exists()
    assert (reports / "report.json").exists()
    assert not (reports / "report.pdf").exists()
    assert "PDF generation skipped" in capsys.readouterr().err
