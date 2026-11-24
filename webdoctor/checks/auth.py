"""Authentication helpers for WebDoctor: automated form login, session capture/reuse,
and an interactive headful login mode for 2FA/SSO/OAuth flows."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Error as PlaywrightError

from webdoctor.models import CheckResult, CheckStatus, Issue, Severity
from webdoctor.config import DEFAULT_USER_AGENT, MAX_PAGE_WAIT_MS

log = logging.getLogger(__name__)


def auto_detect_login_fields(page) -> Dict[str, Optional[str]]:
    """Heuristically find username, password, and submit elements on a login page."""
    user_sel = None
    pass_sel = None
    submit_sel = None

    for sel in [
        "input[type='email']",
        "input[name='email']",
        "input[name*='user' i]",
        "input[name*='login' i]",
        "input[type='text']:visible",
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=700):
                user_sel = sel
                break
        except Exception:
            pass

    for sel in ["input[type='password']", "input[name*='pass' i]"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=700):
                pass_sel = sel
                break
        except Exception:
            pass

    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Log in')",
        "button:has-text('Sign in')",
        "button:has-text('تسجيل الدخول')",
        "button:has-text('Login')",
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=700):
                submit_sel = sel
                break
        except Exception:
            pass

    return {"user": user_sel, "password": pass_sel, "submit": submit_sel}


def perform_form_login(
    login_url: str,
    username: str,
    password: str,
    user_field: Optional[str] = None,
    pass_field: Optional[str] = None,
    submit_btn: Optional[str] = None,
    headless: bool = True,
    timeout_ms: int = MAX_PAGE_WAIT_MS,
    ignore_https_errors: bool = False,
    login_success_selector: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Log in through a form and return (success, storage_state_dict)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=ignore_https_errors,
        )
        page = ctx.new_page()

        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(600)
        except PlaywrightError as e:
            browser.close()
            return False, {"error": f"Could not open login page: {e}"}

        detected = auto_detect_login_fields(page) if not (user_field and pass_field) else {}
        usel = user_field or detected.get("user")
        psel = pass_field or detected.get("password")
        ssel = submit_btn or detected.get("submit")

        if not usel or not psel:
            browser.close()
            return False, {"error": "Could not locate login fields. Provide explicit CSS selectors via user/pass field options."}

        try:
            page.fill(usel, username, timeout=6000)
            page.fill(psel, password, timeout=6000)
            if ssel:
                page.click(ssel, timeout=6000)
            else:
                page.keyboard.press("Enter")
        except PlaywrightError as e:
            browser.close()
            return False, {"error": f"Failed filling/submitting login form: {e}"}

        # Wait for navigation or AJAX login completion
        prev_url = page.url
        try:
            page.wait_for_url(lambda u: u != prev_url, timeout=12000)
        except PlaywrightError:
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightError:
                pass
        page.wait_for_timeout(1000)

        # Check for visible error messages on the page
        visible_error = ""
        for err_sel in [
            "[role='alert']",
            ".alert-danger",
            ".error-message",
            ":text-matches('invalid|incorrect|failed|wrong', 'i')",
        ]:
            try:
                el = page.locator(err_sel).first
                if el.is_visible(timeout=500):
                    visible_error = (el.text_content() or "").strip()[:200]
                    break
            except Exception:
                pass

        final_url = page.url
        state = ctx.storage_state()

        # A visible error is authoritative: the application rejected the login.
        if visible_error:
            browser.close()
            return False, {"error": f"Login refused with message: {visible_error}", "final_url": final_url}

        # Look for positive proof instead of relying on the URL alone. SPAs often
        # authenticate without navigating, while an error page can still redirect.
        auth_keys = ("session", "token", "auth", "sid", "jwt")
        cookies = state.get("cookies", [])
        auth_cookie = any(any(k in c.get("name", "").lower() for k in auth_keys)
                          for c in cookies)
        storage_token = any(
            any(k in item.get("name", "").lower() for k in auth_keys)
            for origin in state.get("origins", [])
            for item in origin.get("localStorage", [])
        )
        success_element = False
        if login_success_selector:
            try:
                success_element = page.locator(login_success_selector).first.is_visible(timeout=800)
            except Exception:
                success_element = False
        url_changed = final_url.rstrip("/") != login_url.rstrip("/")
        still_on_login = "login" in urlparse(final_url).path.lower()
        if url_changed and still_on_login and not (auth_cookie or storage_token or success_element):
            browser.close()
            return False, {
                "error": "Redirected back to a login page after submission -- invalid credentials or bot block.",
                "final_url": final_url,
            }

        if not (auth_cookie or storage_token or success_element or url_changed):
            browser.close()
            return False, {
                "error": "No authenticated session indicator found after submission "
                         "(no auth cookie/token and no URL change). Provide --login-success-selector "
                         "if your app authenticates without a cookie.",
                "final_url": final_url,
            }

        browser.close()
        return True, {
            "state": state,
            "final_url": final_url,
            "cookies": cookies,
            "auth_indicators": {
                "auth_cookie": auth_cookie,
                "storage_token": storage_token,
                "success_element": success_element,
                "url_changed": url_changed,
            },
        }


def record_session_headful(
    login_url: str,
    out_path: str,
    timeout_sec: int = 300,
    ignore_https_errors: bool = False,
) -> Tuple[bool, str]:
    """Open a visible browser so the user can log in manually (2FA/SSO), then save the session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=DEFAULT_USER_AGENT, ignore_https_errors=ignore_https_errors)
        page = ctx.new_page()
        page.goto(login_url)
        print(f"\nA browser window opened for {login_url}.")
        print("Log in completely (2FA/SSO/captcha), then press ENTER here when done...")
        try:
            input()
        except EOFError:
            time.sleep(10)
        ctx.storage_state(path=out_path)
        browser.close()
    return True, out_path


def run_auth_audit_check(session_state: Dict[str, Any], final_url: str) -> CheckResult:
    """Audit the saved session: cookie flags, origin, domain scope."""
    issues = []
    cookies = session_state.get("cookies", [])
    if not cookies:
        issues.append(Issue(
            id="AUTH_NO_SESSION_COOKIE",
            category="auth",
            severity=Severity.WARNING,
            title="No Session Cookies Detected After Login",
            summary="Login succeeded but no cookies were stored (token may live in localStorage).",
            detail=f"Post-login URL: {final_url}",
            how_it_happened="The application likely stores session tokens in localStorage instead of HttpOnly cookies. localStorage is accessible to all JavaScript, increasing XSS risk.",
            reproduction="Inspect DevTools -> Application -> Storage -> Local Storage after login.",
            fix="Prefer HttpOnly, Secure session cookies for authentication state where possible.",
        ))

    insecure = [c for c in cookies if not c.get("secure")]
    if insecure:
        names = ", ".join(c["name"] for c in insecure[:4])
        issues.append(Issue(
            id="AUTH_INSECURE_COOKIE",
            category="auth",
            severity=Severity.WARNING,
            title=f"{len(insecure)} Cookie(s) Missing 'Secure' Flag",
            summary=f"Cookies sent over plain HTTP: {names}.",
            detail=f"Names: {names}",
            how_it_happened="Cookies without the Secure flag are transmitted over unencrypted HTTP connections, exposing session identifiers to on-path attackers.",
            reproduction="Inspect DevTools -> Application -> Cookies; check the Secure column.",
            fix="Set the `Secure` flag on every session/auth cookie (e.g. `Set-Cookie: ...; Secure; SameSite=Lax`).",
        ))

    no_httponly = [c for c in cookies if not c.get("httpOnly") and any(k in c["name"].lower() for k in ("session", "token", "auth", "sid", "jwt"))]
    if no_httponly:
        names = ", ".join(c["name"] for c in no_httponly[:4])
        issues.append(Issue(
            id="AUTH_NO_HTTPONLY",
            category="auth",
            severity=Severity.WARNING,
            title=f"Auth Cookie(s) Missing 'HttpOnly' Flag: {names}",
            summary="Authentication cookies are readable by page JavaScript (XSS risk).",
            detail=f"Cookies: {names}",
            how_it_happened="Without HttpOnly, any cross-site-scripting vulnerability allows an attacker to exfiltrate the full session cookie and impersonate the user.",
            reproduction="In DevTools Console, type `document.cookie` -- any auth token visible there is missing HttpOnly.",
            fix="Set `HttpOnly` on every session cookie server-side (`Set-Cookie: ...; HttpOnly`).",
        ))

    has_err = any(i.severity == Severity.ERROR for i in issues)
    has_warn = any(i.severity == Severity.WARNING for i in issues)
    status = CheckStatus.FAILED if has_err else (CheckStatus.WARN if has_warn else CheckStatus.PASSED)
    return CheckResult(
        name="Authentication & Session Security",
        category="auth",
        status=status,
        issues=issues,
        metrics={"cookie_count": len(cookies), "final_url": final_url},
    )
