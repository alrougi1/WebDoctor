"""CLI report-write resilience and dispatch tests."""
import json

import pytest

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


def test_version_flag_uses_package_version(capsys):
    from webdoctor.config import TOOL_VERSION

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert TOOL_VERSION in capsys.readouterr().out


def test_main_dispatches_every_subcommand(monkeypatch):
    from webdoctor import cli

    dispatched = []

    def fake(name):
        def _run(_args):
            dispatched.append(name)
            return 0
        return _run

    for name in ("cmd_check", "cmd_serve", "cmd_demo", "cmd_models", "cmd_login"):
        monkeypatch.setattr(cli, name, fake(name))

    argv = [
        ["check", "https://example.com", "--no-ai"],
        ["serve"],
        ["demo", "--no-ai"],
        ["models"],
        ["login", "https://example.com"],
    ]
    for args in argv:
        assert cli.main(args) == 0
    assert dispatched == ["cmd_check", "cmd_serve", "cmd_demo", "cmd_models", "cmd_login"]


def test_demo_uses_os_assigned_loopback_port(tmp_path, monkeypatch):
    from webdoctor import cli

    args = cli.build_parser().parse_args([
        "demo", "--no-ai", "--report-dir", str(tmp_path / "reports"),
    ])
    assert args.demo_port == 0

    seen = {}

    def fake_cmd_check(check_args):
        seen["url"] = check_args.url
        return 0

    monkeypatch.setattr(cli, "cmd_check", fake_cmd_check)
    assert cli.cmd_demo(args) == 0
    url = seen["url"]
    assert url.startswith("http://127.0.0.1:")
    assert int(url.rsplit(":", 1)[1]) > 0
