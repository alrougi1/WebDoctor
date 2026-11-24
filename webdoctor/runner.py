"""Test runner: orchestrates check suites and produces a unified TestReport."""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import tempfile
import time
from typing import Callable, Optional, List, Dict, Any
from urllib.parse import urlparse

import requests

from webdoctor.models import TestReport, CheckStatus, CheckResult, Issue, Severity
from webdoctor.config import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    default_model_for_provider,
    TOOL_VERSION,
    MONKEY_DEFAULT_ACTIONS,
    MONKEY_DEFAULT_DELAY_MS,
    MONKEY_DEFAULT_SEED,
)
from webdoctor.checks.http_check import run_http_check
from webdoctor.checks.security import run_security_check
from webdoctor.checks.seo_a11y import run_seo_a11y_check
from webdoctor.checks.links import run_links_check
from webdoctor.checks.browser import run_browser_check
from webdoctor.checks.monkey import run_monkey_check
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
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"Unsupported URL scheme '{parsed.scheme}'; use http or https."
        )
    if not parsed.scheme:
        url = "https://" + url
    return url


# Hostnames that commonly resolve to cloud instance metadata services.
_METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "169.254.169.254",
}


def validate_target_url(url: str, allow_private: bool = False) -> str:
    """Normalize and validate a target URL.

    Loopback targets are always allowed for the built-in demo and local tests.
    Other private, link-local, reserved, or metadata addresses are rejected
    unless ``allow_private`` is explicitly set, preventing accidental SSRF.
    """
    url = normalize_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{parsed.scheme}'; use http or https.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL has no hostname.")

    ip = None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass

    if ip is not None:
        if ip.is_loopback:
            return url
        if not allow_private and (ip.is_private or ip.is_link_local or ip.is_reserved
                                  or ip.is_multicast or ip.is_unspecified):
            raise ValueError(
                f"Refusing to scan private or link-local address '{host}'. "
                "Use --allow-private only when you own and intentionally test it."
            )
        return url

    if not allow_private and host in _METADATA_HOSTNAMES:
        raise ValueError(f"Refusing to scan metadata hostname '{host}'.")
    return url


def run_diagnostics(
    url: str,
    *,
    browser_mode: bool = True,
    check_links: bool = True,
    ai: bool = True,
    ai_provider: str = "sandbox",
    ai_model: Optional[str] = None,
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
    login_success_selector: Optional[str] = None,
    session_path: Optional[str] = None,
    allow_private: bool = False,
    insecure: bool = False,
    # Deterministic hard monkey test (requires browser_mode=True)
    monkey: bool = False,
    monkey_seed: int = MONKEY_DEFAULT_SEED,
    monkey_actions: int = MONKEY_DEFAULT_ACTIONS,
    monkey_delay_ms: int = MONKEY_DEFAULT_DELAY_MS,
    monkey_navigation: bool = True,
    progress: ProgressCb = None,
) -> TestReport:
    """Run all diagnostic suites against a URL and return a TestReport.

    Supports anonymous and authenticated testing, and any configured AI provider.
    """
    url = validate_target_url(url, allow_private=allow_private)
    ai_provider = (ai_provider or "sandbox").strip().lower()
    ai_model = ai_model or default_model_for_provider(ai_provider)
    cb = progress or _noop_progress
    start = time.perf_counter()

    report = TestReport(url=url, tool_version=TOOL_VERSION)

    # 1. HTTP health check (anonymous probe first)
    cb("Probing HTTP & SSL health...", 0.05)
    http_result = run_http_check(url, timeout=timeout, verify=not insecure)
    report.check_results.append(http_result)
    report.metrics.update(http_result.metrics)

    if http_result.issues and any(
        i.id in ("HTTP_CONNECTION_ERROR", "HTTP_SSL_ERROR", "HTTP_TIMEOUT") for i in http_result.issues
    ):
        if monkey:
            report.check_results.append(CheckResult(
                name="Hard Monkey Test", category="monkey", status=CheckStatus.SKIPPED,
                issues=[], metrics={"skipped_reason": "site_unreachable"},
                notes="The target could not be reached; monkey interactions were not attempted.",
            ))
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
            ignore_https_errors=insecure,
            login_success_selector=login_success_selector,
        )
        if ok:
            auth_state = res.get("state")
            sess_file = session_path or os.path.join(
                report_dir or tempfile.gettempdir(), "storage_state.json"
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
            report.check_results.append(CheckResult(
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
    html_fetch_ok = True
    sess = requests.Session()
    if auth_state and auth_state.get("cookies"):
        for c in auth_state["cookies"]:
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain", urlparse(url).hostname or ""))
    try:
        r = sess.get(url, headers={"User-Agent": DEFAULT_USER_AGENT},
                     timeout=timeout, allow_redirects=True, verify=not insecure)
        html = r.text
        report.final_url = r.url
    except Exception as e:
        html_fetch_ok = False
        log.warning("HTML fetch failed: %s", e)
        # Without a document, SEO/a11y and link checks cannot make valid
        # observations. Record the failure once and skip those suites rather
        # than emitting false "missing tag" findings.
        report.check_results.append(CheckResult(
            name="Page HTML Retrieval", category="http", status=CheckStatus.FAILED,
            issues=[Issue(
                id="HTML_FETCH_FAILED", category="http", severity=Severity.ERROR,
                title="Could Not Retrieve Page HTML",
                summary=f"The HTML document could not be fetched: {e}",
                detail=str(e),
                how_it_happened=(
                    "The HTTP client could not download the page body (network error, timeout, "
                    "or server disconnect), even though the initial request succeeded."
                ),
                reproduction=f"curl -sS '{url}' -o /dev/null -w '%{{http_code}}\\n'",
                fix="Check network connectivity, server stability, and any bot protection that may reset HTML requests.",
            )],
        ))

    # 4. Security headers check
    cb("Auditing security headers & cookies...", 0.28)
    sec_result = run_security_check(url, timeout=timeout, verify=not insecure)
    report.check_results.append(sec_result)

    # 5. SEO & accessibility fundamentals
    cb("Checking SEO & accessibility fundamentals...", 0.40)
    if html_fetch_ok:
        seo_result = run_seo_a11y_check(report.final_url or url, html)
    else:
        seo_result = CheckResult(
            name="SEO & Accessibility Fundamentals", category="seo_a11y",
            status=CheckStatus.SKIPPED, issues=[],
            metrics={"skipped_reason": "html_unavailable"},
        )
    report.check_results.append(seo_result)

    # 6. Broken links & resources
    if check_links:
        cb("Crawling and testing page links...", 0.55)
        if html_fetch_ok:
            links_result = run_links_check(report.final_url or url, html, verify=not insecure)
        else:
            links_result = CheckResult(
                name="Broken Links & Resources", category="links",
                status=CheckStatus.SKIPPED, issues=[],
                metrics={
                    "skipped_reason": "html_unavailable",
                    "links_found": 0, "links_checked": 0,
                    "links_truncated": False, "broken_count": 0, "broken_percent": 0.0,
                },
            )
        report.check_results.append(links_result)
        report.metrics.update(links_result.metrics)

    # 7. Real browser testing via Playwright (runs authenticated if session is set)
    browser_result: Optional[CheckResult] = None
    if browser_mode:
        cb("Running headless browser (JS errors, console, performance)...", 0.70)
        browser_result = run_browser_check(
            url, screenshot_dir=screenshot_dir, session_path=session_path,
            ignore_https_errors=insecure,
        )
        report.check_results.append(browser_result)
        bm = browser_result.metrics or {}
        report.metrics.update(bm)
        if bm.get("screenshot"):
            report.screenshot_path = bm["screenshot"]
        if bm.get("page_title"):
            report.target_title = bm["page_title"]
        if bm.get("final_url"):
            report.final_url = bm["final_url"]

    # 7b. Deterministic hard monkey test (browser chaos). Runs authenticated
    # when a session was captured above, and is always appended before scoring.
    if monkey:
        if not browser_mode:
            cb("Monkey test skipped: browser suite is disabled...", 0.78)
            report.check_results.append(CheckResult(
                name="Hard Monkey Test", category="monkey", status=CheckStatus.SKIPPED,
                issues=[], metrics={"skipped_reason": "browser_mode_disabled"},
                notes="--monkey requires the headless browser suite.",
            ))
        else:
            cb("Running deterministic hard monkey test (seeded browser chaos)...", 0.78)
            monkey_result = run_monkey_check(
                report.final_url or url,
                seed=monkey_seed,
                actions=monkey_actions,
                delay_ms=monkey_delay_ms,
                allow_navigation=monkey_navigation,
                session_path=session_path,
                ignore_https_errors=insecure,
                prior_browser_result=browser_result,
            )
            report.check_results.append(monkey_result)
            # Keep monkey details namespaced so they never overwrite browser
            # metrics such as js_error_count or screenshot.
            report.metrics["monkey"] = monkey_result.metrics
            report.metrics["monkey_actions_executed"] = monkey_result.metrics.get("actions_executed", 0)
            if monkey_result.metrics.get("screenshot") and not report.screenshot_path:
                report.screenshot_path = monkey_result.metrics["screenshot"]

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
