"""WebDoctor CLI: developer-friendly website testing & diagnostics."""
from __future__ import annotations

import argparse
import sys
import os
import traceback
from typing import Optional, List, Dict

from webdoctor.models import CheckStatus, Severity
from webdoctor.runner import run_diagnostics, normalize_url
from webdoctor.config import TOOL_VERSION, AI_MODEL_FAST
from webdoctor.reporters.markdown_report import generate_markdown
from webdoctor.reporters.html_report import generate_html
from webdoctor.reporters.json_report import generate_json
from webdoctor.reporters.pdf_report import generate_pdf
from webdoctor.diagnostics.ai_adapters import list_available_models
from webdoctor.checks.auth import record_session_headful


SEV_CONSOLE_STYLE = {
    Severity.CRITICAL: ("bold white on red", "CRITICAL"),
    Severity.ERROR:    ("bold red", "ERROR"),
    Severity.WARNING:  ("bold yellow", "WARN"),
    Severity.INFO:     ("dim", "INFO"),
}

GRADE_STYLE = {
    "A": "bold green",
    "B": "bold cyan",
    "C": "bold yellow",
    "D": "bold red",
    "F": "bold white on red",
}


def _try_rich():
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.progress import Progress, SpinnerColumn, TextColumn
        return Console(), Panel, Table, Progress, SpinnerColumn, TextColumn
    except Exception:
        return None, None, None, None, None, None


def _parse_compare(s: Optional[str]) -> Optional[List[Dict[str, str]]]:
    if not s:
        return None
    out = []
    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        prov, model = (item.split(":", 1) if ":" in item else ("sandbox", item))
        out.append({"provider": prov, "model": model, "label": f"{model} ({prov})"})
    return out or None


def cmd_check(args: argparse.Namespace) -> int:
    console, Panel, Table, Progress, SpinnerColumn, TextColumn = _try_rich()
    ci = getattr(args, "ci", False)

    if console and not ci:
        console.print(f"[bold]WebDoctor[/bold] v{TOOL_VERSION} -- website diagnostics\n", highlight=False)

    progress_cb = None
    stop_spinner = lambda: None
    if Progress and console and not ci:
        progress = Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True)
        task = progress.add_task("Starting...", total=100)
        progress.start()

        def _cb(msg: str, pct: float):
            progress.update(task, description=msg, completed=int(pct * 100))

        progress_cb = _cb
        stop_spinner = progress.stop
    else:
        def _cb(msg: str, _pct: float):
            if not ci:
                print(f"* {msg}", flush=True)
        progress_cb = _cb

    report_dir = os.path.abspath(args.report_dir)
    os.makedirs(report_dir, exist_ok=True)
    screenshot_dir = os.path.join(report_dir, "screenshots")

    username = args.username or os.getenv("WEBDOCTOR_LOGIN_USER")
    password = args.password or os.getenv("WEBDOCTOR_LOGIN_PASS")

    try:
        report = run_diagnostics(
            args.url,
            browser_mode=not args.no_browser,
            check_links=not args.no_links,
            ai=not args.no_ai,
            ai_provider=args.ai_provider,
            ai_model=args.ai_model,
            ai_key=args.ai_key,
            ai_base=args.ai_base,
            ai_compare=_parse_compare(args.ai_compare),
            timeout=args.timeout,
            screenshot_dir=screenshot_dir,
            report_dir=report_dir,
            login=args.login,
            login_url=args.login_url or args.url,
            username=username,
            password=password,
            user_field=args.user_field,
            pass_field=args.pass_field,
            submit_btn=args.submit_btn,
            session_path=args.session,
            progress=progress_cb,
        )
    finally:
        stop_spinner()

    counts = report.issue_counts()
    authed = bool(args.login or args.session)

    if console and not ci:
        st = GRADE_STYLE.get(report.overall_grade, "bold")
        badge = "  [bold green]* AUTHENTICATED RUN[/bold green]" if authed else ""
        console.print()
        console.print(Panel(
            f"[bold]GRADE[/bold] [{st}]{report.overall_grade}[/]  *  Score {report.score}/100  *  {report.overall_status.value.upper()}{badge}\n"
            f"[bold]{report.final_url or report.url}[/]\n\n"
            f"[red]Critical: {counts['critical']}[/]  [orange3]Errors: {counts['error']}[/]  "
            f"[yellow]Warnings: {counts['warning']}[/]  Info: {counts['info']}  *  {report.duration_ms/1000:.1f}s",
            title=f"WebDoctor Report -- {report.timestamp} UTC",
            expand=False,
        ))

        tbl = Table(title="Check Suites", show_edge=False)
        tbl.add_column("Suite")
        tbl.add_column("Status", justify="center")
        tbl.add_column("Duration", justify="right")
        tbl.add_column("Issues", justify="right")
        for cr in report.check_results:
            color = {"passed": "green", "warn": "yellow", "failed": "red", "skipped": "dim"}[cr.status.value]
            tbl.add_row(cr.name, f"[{color}]{cr.status.value.upper()}[/]", f"{cr.duration_ms}ms", str(len(cr.issues)))
        console.print(tbl)

        if report.issues:
            console.print("\n[bold]Issues & Root Causes:[/bold]")
            for k, it in enumerate(report.issues[:12], 1):
                style, label = SEV_CONSOLE_STYLE.get(it.severity, ("dim", "INFO"))
                console.print(f"[{style}][{label}][/{style}] {k}. [bold]{it.title}[/bold] ({it.category})")
                console.print(f"   [dim]{it.summary}[/dim]")
                if it.how_it_happened:
                    console.print(f"   [cyan]How it happened:[/cyan] {it.how_it_happened[:220]}...")
                if it.reproduction:
                    console.print(f"   [magenta]Reproduce:[/magenta] {it.reproduction}")
                if it.fix:
                    console.print(f"   [green]Fix:[/green] {it.fix[:180]}")
                console.print()
            if len(report.issues) > 12:
                console.print(f"[dim]... plus {len(report.issues)-12} more issue(s). See the full HTML/MD report.[/dim]\n")

        if report.ai_summary:
            console.print(Panel(report.ai_summary[:1600], title="Executive Diagnosis (AI)", border_style="cyan"))
    else:
        print(f"\nWebDoctor -- {report.url}")
        print(f"Grade: {report.overall_grade} ({report.score}/100) -- {report.overall_status.value.upper()}")
        print(f"Issues: critical={counts['critical']} error={counts['error']} warning={counts['warning']} info={counts['info']}")
        for it in report.issues[:8]:
            print(f"- [{it.severity.value.upper()}] {it.title}")

    # Write report files: PDF FIRST, then HTML/MD/JSON
    md_path = args.out_md or os.path.join(report_dir, "report.md")
    html_path = args.out_html or os.path.join(report_dir, "report.html")
    json_path = args.out_json or os.path.join(report_dir, "report.json")

    pdf_path = None
    if args.pdf:
        pdf_path = args.out_pdf or os.path.join(report_dir, "report.pdf")
        ok = generate_pdf(report, pdf_path)
        if not ok:
            pdf_path = None

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(generate_markdown(report))
    try:
        hcontent = generate_html(report)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(hcontent)
    except Exception as e:
        print(f"[error] failed to write HTML report: {e}", file=sys.stderr)
        traceback.print_exc()
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(generate_json(report))
    except Exception as e:
        print(f"[error] failed to write JSON report: {e}", file=sys.stderr)

    if not ci:
        print(f"\nReports written:")
        print(f"  Markdown : {md_path}")
        print(f"  HTML     : {html_path}")
        print(f"  JSON     : {json_path}")
        if pdf_path:
            print(f"  PDF      : {pdf_path}")

    return 0 if report.overall_status != CheckStatus.FAILED else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from webdoctor.server.app import app
    print(f"Starting WebDoctor dashboard at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_models(_args: argparse.Namespace) -> int:
    models = list_available_models()
    print("WebDoctor -- AI Model Catalog\n")
    print(f"{'PROVIDER':11} {'MODEL ID':22} {'DESCRIPTION'}")
    print("-" * 72)
    for m in models:
        print(f"{m['provider']:11} {m['id']:22} {m['label']}")
    print("\nUse with: webdoctor check https://example.com --ai-model <id>")
    print("Sandbox models (gpt-6-luna, gpt-6.1-sol, gemini-3.8-flash) are preconfigured.")
    print("For Claude/Gemini direct, pass --ai-provider anthropic|gemini and --ai-key <KEY>.")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    url = normalize_url(args.url)
    out = os.path.abspath(args.save_session)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        record_session_headful(url, out)
        print(f"\nSaved session to: {out}")
        print(f"Now run authenticated tests with:")
        print(f"  webdoctor check \"{url}\" --session {out} --pdf")
        return 0
    except Exception as e:
        print(f"Login helper failed: {e}", file=sys.stderr)
        return 1


def cmd_demo(args: argparse.Namespace) -> int:
    import threading
    import socketserver
    import http.server

    demo_html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Demo Broken Site</title>
<link rel="stylesheet" href="/assets/missing-style.css"><link rel="icon" href="/favicon.ico"></head>
<body><h1>Demo Broken Site</h1><p>Deliberate developer errors.</p>
<img src="/img/nonexistent-logo.png"><a href="/about-us">About</a>
<script src="/js/broken.js"></script>
<script>const u=null; setTimeout(()=>{ u.profile.name; },400); console.error("widget error");</script>
</body></html>"""

    class DemoHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Server", "DemoServer/2.4"); self.send_header("X-Powered-By", "PHP/8.2"); self.end_headers()
                self.wfile.write(demo_html.encode("utf-8"))
            elif self.path == "/js/broken.js":
                self.send_response(200); self.send_header("Content-Type", "application/javascript"); self.end_headers()
                self.wfile.write(b"window.bad = JSON.parse('bad{');")
            else:
                self.send_response(404); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"Not Found")

    class ReuseTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = ReuseTCPServer(("127.0.0.1", args.demo_port), DemoHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    demo_url = f"http://127.0.0.1:{args.demo_port}"
    print(f"Broken demo site running at {demo_url}\nRunning WebDoctor diagnostics against it...\n")

    check_args = argparse.Namespace(
        url=demo_url, report_dir=args.report_dir, no_browser=False, no_links=False,
        no_ai=args.no_ai, ai_provider="sandbox", ai_model=args.ai_model, ai_key=None, ai_base=None,
        ai_compare=None, timeout=args.timeout, pdf=args.pdf, out_md=None, out_html=None,
        out_json=None, out_pdf=None, ci=False, login=False, login_url=None, username=None,
        password=None, user_field=None, pass_field=None, submit_btn=None, session=None,
    )
    code = cmd_check(check_args)
    httpd.shutdown()
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="webdoctor", description="Automated website testing & root-cause diagnostics for developers.")
    p.add_argument("--version", action="version", version=f"WebDoctor {TOOL_VERSION}")
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("check", help="Run full diagnostic suite against a URL")
    c.add_argument("url", help="Target URL (e.g. https://mysite.com or localhost:3000)")
    c.add_argument("--no-browser", action="store_true", help="Skip headless browser tests (HTTP/security/links/SEO only)")
    c.add_argument("--no-links", action="store_true", help="Skip broken-link crawling")
    c.add_argument("--no-ai", action="store_true", help="Skip AI diagnosis; use rule-based summary only")
    c.add_argument("--ai-provider", default="sandbox", choices=["sandbox", "openai", "anthropic", "gemini"], help="AI provider (default: sandbox)")
    c.add_argument("--ai-model", default=AI_MODEL_FAST, help=f"LLM model (default: {AI_MODEL_FAST}; see 'webdoctor models')")
    c.add_argument("--ai-key", help="API key for anthropic/gemini/openai providers (or env ANTHROPIC_API_KEY/GEMINI_API_KEY)")
    c.add_argument("--ai-base", help="Custom OpenAI-compatible base URL")
    c.add_argument("--ai-compare", help="Compare multiple models: provider:model,provider:model")
    c.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds (default: 20)")
    c.add_argument("--pdf", action="store_true", help="Also generate a PDF report")
    c.add_argument("--report-dir", default="./reports", help="Directory for generated reports (default: ./reports)")
    c.add_argument("--out-md", help="Custom markdown report output path")
    c.add_argument("--out-html", help="Custom HTML report output path")
    c.add_argument("--out-json", help="Custom JSON report output path")
    c.add_argument("--out-pdf", help="Custom PDF report output path")
    c.add_argument("--ci", action="store_true", help="CI mode: concise output, exit code 0=pass / 1=fail")
    # Authenticated testing
    c.add_argument("--login", action="store_true", help="Log in before testing (fills credentials then tests as authenticated user)")
    c.add_argument("--login-url", help="Login page URL (defaults to the target URL)")
    c.add_argument("--username", help="Login username (or env WEBDOCTOR_LOGIN_USER)")
    c.add_argument("--password", help="Login password (or env WEBDOCTOR_LOGIN_PASS)")
    c.add_argument("--user-field", help="CSS selector of username input (auto-detected if omitted)")
    c.add_argument("--pass-field", help="CSS selector of password input (auto-detected if omitted)")
    c.add_argument("--submit-btn", help="CSS selector of submit button (auto-detected if omitted)")
    c.add_argument("--session", help="Path to saved Playwright storageState JSON (reuse logged-in session)")

    m = sub.add_parser("models", help="List available AI models and providers")
    m.set_defaults(func=cmd_models)

    l = sub.add_parser("login", help="Open a visible browser, log in manually (2FA/SSO), and save the session")
    l.add_argument("url", help="Login URL")
    l.add_argument("--save-session", default="./session.json", help="Output path for the saved session (default: ./session.json)")

    s = sub.add_parser("serve", help="Launch the interactive web dashboard")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)

    d = sub.add_parser("demo", help="Run diagnostics against a built-in broken demo site")
    d.add_argument("--demo-port", type=int, default=8765)
    d.add_argument("--report-dir", default="./reports")
    d.add_argument("--no-ai", action="store_true")
    d.add_argument("--ai-model", default=AI_MODEL_FAST)
    d.add_argument("--timeout", type=int, default=20)
    d.add_argument("--pdf", action="store_true")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "models":
        return cmd_models(args)
    if args.command == "login":
        return cmd_login(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
