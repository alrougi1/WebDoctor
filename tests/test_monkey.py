"""Deterministic hard monkey test: safety, reproducibility and integration."""
from __future__ import annotations

import http.server
import json
import socketserver
import threading
from types import SimpleNamespace

import pytest

from webdoctor import runner
from webdoctor.checks.monkey import (
    build_action_plan,
    is_safe_target,
    run_monkey_check,
    validate_monkey_options,
    _browser_error_signatures,
)
from webdoctor.cli import build_parser, cmd_check
from webdoctor.models import CheckResult, CheckStatus, Issue, Severity, TestReport
from webdoctor.reporters.html_report import generate_html
from webdoctor.reporters.markdown_report import generate_markdown
from webdoctor.server import app as server_app


BASE = "https://example.com/app"


def _candidate(selector, **kwargs):
    data = {"selector": selector, "tag": "button", "label": "", "href": "",
            "clickable": True, "editable": False}
    data.update(kwargs)
    return data


def test_unsafe_targets_are_rejected():
    assert is_safe_target(_candidate("#safe"), BASE)[0] is True
    assert is_safe_target(_candidate("#logout", label="Log out"), BASE)[1] == "destructive label"
    assert is_safe_target(_candidate("#delete", label="Delete account"), BASE)[1] == "destructive label"
    assert is_safe_target(_candidate("#submit", label="Submit"), BASE)[0] is False
    assert is_safe_target(_candidate("#ext", href="https://evil.example/"), BASE)[1] == "off-origin URL"
    assert is_safe_target(_candidate("#js", href="javascript:alert(1)"), BASE)[1] == "dangerous URL scheme"
    assert is_safe_target(_candidate("#next", href="/next"), BASE, allow_navigation=False)[1] == "navigation disabled"
    assert is_safe_target(_candidate("#frag", href="#section"), BASE, allow_navigation=False)[0] is True


def test_action_plan_is_deterministic():
    candidates = [
        _candidate(f"button#{index}", label=f"Safe action {index}", clickable=True)
        for index in range(10)
    ] + [
        _candidate(f"input#field{index}", tag="input", type="text", label=f"Field {index}",
                   clickable=False, editable=True)
        for index in range(5)
    ]
    plan_a, unsafe_a = build_action_plan(4242, 40, candidates, BASE, allow_navigation=True)
    plan_b, unsafe_b = build_action_plan(4242, 40, candidates, BASE, allow_navigation=True)
    plan_c, _ = build_action_plan(4243, 40, candidates, BASE, allow_navigation=True)
    assert plan_a == plan_b
    assert unsafe_a == unsafe_b
    assert plan_a != plan_c
    assert len(plan_a) == 40
    assert all(step["type"] in ("click", "type", "scroll", "key", "resize") for step in plan_a)
    assert not any("Enter" in str(step) for step in plan_a)


def test_validate_monkey_options_bounds():
    options = validate_monkey_options(7, 25, 10)
    assert (options.seed, options.actions, options.delay_ms) == (7, 25, 10)
    with pytest.raises(ValueError):
        validate_monkey_options(7, 0, 10)
    with pytest.raises(ValueError):
        validate_monkey_options(7, 25, 99999)


def test_browser_error_signatures_suppress_duplicates():
    prior = CheckResult(
        name="Browser", category="browser", status=CheckStatus.FAILED,
        issues=[Issue(
            id="JS_RUNTIME_1", category="browser", severity=Severity.ERROR,
            title="boom", summary="boom",
            detail="Error message: TypeError: a is undefined\nStack: at x",
        )],
    )
    signatures = _browser_error_signatures(prior)
    assert len(signatures) == 1
    signature = next(iter(signatures))
    assert len(signature) == 8 and all(c in "0123456789ABCDEF" for c in signature)


def _monkey_report() -> TestReport:
    monkey = CheckResult(
        name="Hard Monkey Test", category="monkey", status=CheckStatus.FAILED,
        duration_ms=1500,
        issues=[Issue(
            id="MONKEY_JS_EABCD1234", category="monkey", severity=Severity.ERROR,
            title="Monkey JavaScript error", summary="boom",
            how_it_happened="A click handler failed.", reproduction="webdoctor check --monkey",
            fix="Fix the handler.",
        )],
        metrics={
            "seed": 42, "planned_actions": 20, "actions_executed": 19,
            "actions_skipped": 1, "js_error_count": 1, "console_error_count": 0,
            "dialog_count": 1, "page_crash_count": 0,
            "blocked_submit_count": 2, "blocked_mutation_count": 1,
            "off_origin_navigation_attempts": ["https://evil.example/"],
            "config_fingerprint": "abc123",
            "trace": [{"step": 1, "type": "click", "selector": "button#safe",
                       "result": "executed", "url": "https://example.com/app"}],
        },
        notes="seed=42",
    )
    return TestReport(url=BASE, check_results=[monkey]).compute_summary()


def test_monkey_section_rendered_in_markdown_and_html():
    report = _monkey_report()
    md = generate_markdown(report)
    assert "## Hard Monkey Test" in md
    assert "Seed:** `42`" in md
    assert "button#safe" in md
    assert "19/20 executed" in md

    document = generate_html(report)
    assert "Hard Monkey Test — Deterministic Browser Chaos" in document
    assert "abc123" in document
    assert "button#safe" in document


def test_cli_forwards_monkey_flags(tmp_path, monkeypatch):
    captured = {}

    def _fake_diagnostics(*_args, **kwargs):
        captured.update(kwargs)
        return TestReport(url=BASE).compute_summary()

    monkeypatch.setattr("webdoctor.cli.run_diagnostics", _fake_diagnostics)
    args = build_parser().parse_args([
        "check", BASE, "--no-ai", "--ci", "--monkey", "--monkey-seed", "77",
        "--monkey-actions", "12", "--monkey-delay-ms", "5", "--monkey-no-navigation",
        "--report-dir", str(tmp_path / "reports"),
    ])
    assert cmd_check(args) == 0
    assert captured["monkey"] is True
    assert captured["monkey_seed"] == 77
    assert captured["monkey_actions"] == 12
    assert captured["monkey_delay_ms"] == 5
    assert captured["monkey_navigation"] is False


def test_cli_rejects_monkey_without_browser(tmp_path, monkeypatch, capsys):
    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("diagnostics must not run for an invalid flag combination")

    monkeypatch.setattr("webdoctor.cli.run_diagnostics", _should_not_run)
    args = build_parser().parse_args([
        "check", BASE, "--no-browser", "--no-ai", "--ci", "--monkey",
        "--report-dir", str(tmp_path / "reports"),
    ])
    assert cmd_check(args) == 1
    assert "--monkey requires" in capsys.readouterr().err


def _patch_suites(monkeypatch):
    monkeypatch.setattr(
        runner, "run_http_check",
        lambda *a, **k: CheckResult(name="HTTP", category="http", status=CheckStatus.PASSED,
                                    metrics={"status_code": 200, "response_time_ms": 1}),
    )
    monkeypatch.setattr(
        runner, "run_security_check",
        lambda *a, **k: CheckResult(name="Security", category="security", status=CheckStatus.PASSED),
    )
    monkeypatch.setattr(
        "webdoctor.runner.requests.Session.get",
        lambda self, *a, **k: SimpleNamespace(
            text="<html><head><title>x</title></head><body>ok</body></html>", url=BASE
        ),
    )


def test_runner_skips_monkey_when_browser_disabled(monkeypatch):
    _patch_suites(monkeypatch)
    report = runner.run_diagnostics(
        BASE, browser_mode=False, check_links=False, ai=False, monkey=True
    )
    monkey = next(cr for cr in report.check_results if cr.category == "monkey")
    assert monkey.status == CheckStatus.SKIPPED
    assert monkey.metrics["skipped_reason"] == "browser_mode_disabled"


def test_runner_passes_prior_browser_result_to_monkey(monkeypatch):
    _patch_suites(monkeypatch)
    browser = CheckResult(name="Browser", category="browser", status=CheckStatus.PASSED,
                          metrics={"screenshot": None, "page_title": "x", "final_url": BASE})
    monkeypatch.setattr(runner, "run_browser_check", lambda *a, **k: browser)
    captured = {}

    def _fake_monkey(url, **kwargs):
        captured.update(kwargs)
        return CheckResult(
            name="Hard Monkey Test", category="monkey", status=CheckStatus.PASSED,
            metrics={"seed": 5, "actions_executed": 7, "planned_actions": 7,
                     "actions_skipped": 0, "screenshot": None, "trace": []},
        )

    monkeypatch.setattr(runner, "run_monkey_check", _fake_monkey)
    report = runner.run_diagnostics(
        BASE, browser_mode=True, check_links=False, ai=False, monkey=True, monkey_seed=5
    )
    assert captured["prior_browser_result"] is browser
    assert captured["seed"] == 5
    assert report.metrics["monkey"]["actions_executed"] == 7
    assert report.metrics["monkey_actions_executed"] == 7


def test_dashboard_monkey_request_is_sanitized_and_validated():
    request = server_app.DiagnoseRequest(
        url=BASE, monkey=True, monkey_seed=9, monkey_actions=30, monkey_delay_ms=5,
        monkey_navigation=False,
    )
    config = server_app._sanitize_request(request)
    assert config["monkey"] is True
    assert config["monkey_seed"] == 9
    assert config["monkey_actions"] == 30
    assert "password" not in config and "ai_key" not in config

    with pytest.raises(ValueError):
        validate_monkey_options(request.monkey_seed, 0, request.monkey_delay_ms)


def test_dashboard_rejects_invalid_monkey_actions():
    from fastapi.testclient import TestClient

    with TestClient(server_app.app) as client:
        response = client.post("/api/diagnose", json={
            "url": BASE, "browser": False, "links": False, "ai": False,
            "monkey": True, "monkey_actions": 0,
        })
    assert response.status_code == 400
    assert "monkey-actions" in response.json()["detail"]


MONKEY_FIXTURE_HTML = """<!doctype html><html lang="en"><head><title>Monkey Fixture</title></head>
<body>
<h1>Monkey Fixture</h1>
<button id="safe" type="button">Safe action</button>
<input id="q" type="text" aria-label="Search">
<a id="logout" href="/logout">Log out</a>
<script>
setTimeout(() => { throw new Error('monkey fixture error'); }, 200);
</script>
</body></html>"""


def test_real_browser_monkey_run_is_bounded_and_captures_errors():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = MONKEY_FIXTURE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    target = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        result = run_monkey_check(
            target, seed=11, actions=16, delay_ms=30, allow_navigation=False
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert result.category == "monkey"
    metrics = result.metrics
    assert metrics["planned_actions"] == 16
    assert len(metrics["trace"]) == 16
    assert metrics["actions_executed"] + metrics["actions_skipped"] == 16
    assert metrics["candidates_found"] >= 2
    assert metrics["js_error_count"] >= 1
    assert any(i.id.startswith("MONKEY_JS_E") for i in result.issues)
    assert not any(i.severity == Severity.CRITICAL for i in result.issues)
    # The destructive link must never enter the action plan.
    assert all("logout" not in json.dumps(step).lower() for step in metrics["trace"])
