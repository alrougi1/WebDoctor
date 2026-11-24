"""Test runner: orchestrates check suites and produces a unified TestReport."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional, List, Dict, Any
from urllib.parse import urlparse

import requests

from webdoctor.models import TestReport, CheckStatus
from webdoctor.config import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    AI_MODEL_FAST,
    TOOL_VERSION,
)
from webdoctor.checks.http_check import run_http_check
from webdoctor.checks.security import run_security_check
from webdoctor.checks.seo_a11y import run_seo_a11y_check
from webdoctor.checks.links import run_links_check
from webdoctor.checks.browser import run_browser_check
from webdoctor.checks.auth import perform_form_login, run_auth_audit_check
from webdoctor.diagnostics.ai_engine import generate_ai_diagnosis
from webdoctor.diagnostics.rule_engine import generate_rule_summary

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str, float], None]]


def _noop_progress(_msg: str, _pct: float) -> None:
    pass


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def run_diagnostics(
    url: str,
    *,
    browser_mode: bool = True,
    check_links: bool = True,
    ai: bool = True,
    ai_provider: str = "sandbox",
    ai_model: str = AI_MODEL_FAST,
    ai_key: Optional[str] = None,
    ai_base: Optional[str] = None,
    ai_compare: Optional[List[Dict[str, str]]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    screenshot_dir: Optional[str] = None,
    report_dir: Optional[str] = None,
    # Authenticated testing
    login: bool = False,
    login_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    user_field: Optional[str] = None,
    pass_field: Optional[str] = None,
    submit_btn: Optional[str] = None,
    session_path: Optional[str] = None,
    progress: ProgressCb = None,
) -> TestReport:
    """Run all diagnostic suites against a URL and return a TestReport.

    Supports anonymous and authenticated testing, and any configured AI provider.
    """
    url = normalize_url(url)
    cb = progress or _noop_progress
    start = time.perf_counter()

    report = TestReport(url=url, tool_version=TOOL_VERSION)

    # 1. HTTP health check (anonymous probe first)
    cb("Probing HTTP & SSL health...", 0.05)
    http_result = run_http_check(url, timeout=timeout)
    report.check_results.append(http_result)
    report.metrics.update(http_result.metrics)

    if http_result.issues and any(
        i.id in ("HTTP_CONNECTION_ERROR", "HTTP_SSL_ERROR", "HTTP_TIMEOUT") for i in http_result.issues
    ):
        report.overall_status = CheckStatus.FAILED
        report.duration_ms = int((time.perf_counter() - start) * 1000)
        report.compute_summary()
        report.ai_summary = generate_rule_summary(report)
        cb("Site unreachable -- diagnostics complete.", 1.0)
        return report

    # 2. Optional authentication: log in and capture session
    auth_state: Optional[Dict[str, Any]] = None
    if login and username and password and login_url:
        cb("Logging in to run tests as an authenticated user...", 0.12)
        ok, res = perform_form_login(
            login_url=login_url,
            username=username,
            password=password,
            user_field=user_field,
            pass_field=pass_field,
            submit_btn=submit_btn,
            timeout_ms=timeout * 1000,
        )
        if ok:
            auth_state = res.get("state")
            sess_file = session_path or (
                os.path.join(report_dir or "/tmp", "storage_state.json")
            )
            try:
                os.makedirs(os.path.dirname(os.path.abspath(sess_file)), exist_ok=True)
                with open(sess_file, "w") as f:
                    json.dump(auth_state, f)
                session_path = sess_file
            except Exception as e:
                log.warning("Could not persist session: %s", e)
            report.check_results.append(run_auth_audit_check(auth_state or {}, res.get("final_url", url)))
            report.metrics["logged_in"] = True
            report.metrics["post_login_url"] = res.get("final_url")
        else:
            from webdoctor.models import Issue, Severity, CheckResult as CR
            report.check_results.append(CR(
                name="Authentication",
                category="auth",
                status=CheckStatus.FAILED,
                issues=[Issue(
                    id="AUTH_LOGIN_FAILED",
                    category="auth",
                    severity=Severity.CRITICAL,
                    title="Automated Login Failed",
                    summary=res.get("error", "Unknown login failure"),
                    detail=f"Login URL: {login_url}\nResult: {res}",
                    how_it_happened="The tester filled and submitted the login form, but the server either refused the credentials, showed a bot challenge, or the form fields could not be located.",
                    reproduction=f"Open {login_url} in a browser and try the credentials manually.",
                    fix="Verify credentials, check if bot protection blocks automation, or provide explicit field selectors (user_field / pass_field / submit_btn).",
                )],
            ))
            report.metrics["logged_in"] = False

    # 3. Fetch page HTML once for HTML-based checks (use session cookies if authenticated)
    cb("Fetching page HTML...", 0.20)
    html = ""
    sess = requests.Session()
    if auth_state and auth_state.get("cookies"):
        for c in auth_state["cookies"]:
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain", urlparse(url).hostname or ""))
    try:
        r = sess.get(url, headers={"User-Agent": DEFAULT_USER_AGENT},
                     timeout=timeout, allow_redirects=True, verify=False)
        html = r.text
        report.final_url = r.url
    except Exception as e:
        log.warning("HTML fetch failed: %s", e)

    # 4. Security headers check
    cb("Auditing security headers & cookies...", 0.28)
    sec_result = run_security_check(url, timeout=timeout)
    report.check_results.append(sec_result)

    # 5. SEO & accessibility fundamentals
    cb("Checking SEO & accessibility fundamentals...", 0.40)
    seo_result = run_seo_a11y_check(report.final_url or url, html)
    report.check_results.append(seo_result)

    # 6. Broken links & resources
    if check_links:
        cb("Crawling and testing page links...", 0.55)
        links_result = run_links_check(report.final_url or url, html)
        report.check_results.append(links_result)
        report.metrics.update(links_result.metrics)

    # 7. Real browser testing via Playwright (runs authenticated if session is set)
    if browser_mode:
        cb("Running headless browser (JS errors, console, performance)...", 0.70)
        browser_result = run_browser_check(url, screenshot_dir=screenshot_dir, session_path=session_path)
        report.check_results.append(browser_result)
        bm = browser_result.metrics or {}
        report.metrics.update(bm)
        if bm.get("screenshot"):
            report.screenshot_path = bm["screenshot"]
        if bm.get("page_title"):
            report.target_title = bm["page_title"]
        if bm.get("final_url"):
            report.final_url = bm["final_url"]

    # Finalize issues & score
    report.compute_summary()
    report.duration_ms = int((time.perf_counter() - start) * 1000)

    # 8. AI root-cause diagnosis (multi-provider)
    if ai:
        cb("Generating AI root-cause diagnosis...", 0.90)
        report.ai_summary = generate_ai_diagnosis(
            report,
            model=ai_model,
            provider=ai_provider,
            api_key=ai_key,
            api_base=ai_base,
            compare=ai_compare,
        )
    else:
        report.ai_summary = generate_rule_summary(report)

    cb("Report ready.", 1.0)
    return report
