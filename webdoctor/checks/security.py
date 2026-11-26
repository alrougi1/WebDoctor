"""Security headers & cookie flags check."""
from __future__ import annotations

import re
import requests
from typing import List
from webdoctor.models import CheckResult, CheckStatus, Issue, Severity
from webdoctor.config import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from webdoctor.shell import quote


def _set_cookie_for(name: str, raw_cookies: List[str]) -> str:
    """Return the raw Set-Cookie header that belongs to ``name``."""
    for raw in raw_cookies:
        first = raw.split(";", 1)[0].strip()
        if first.startswith(f"{name}="):
            return raw
    return ""


def _has_attr(raw_cookie: str, pattern: str) -> bool:
    return bool(raw_cookie) and bool(re.search(pattern, raw_cookie, re.IGNORECASE))


def run_security_check(url: str, timeout: int = DEFAULT_TIMEOUT, verify: bool = True) -> CheckResult:
    issues: List[Issue] = []
    headers_seen = {}

    req_headers = {"User-Agent": DEFAULT_USER_AGENT}
    try:
        resp = requests.get(url, headers=req_headers, timeout=timeout, allow_redirects=True, verify=verify)
    except Exception as e:
        return CheckResult(
            name="Security Headers",
            category="security",
            status=CheckStatus.SKIPPED,
            issues=[Issue(
                id="SEC_CHECK_SKIPPED",
                category="security",
                severity=Severity.WARNING,
                source="environment",
                title="Security Header Audit Could Not Run",
                summary=f"The security header audit was skipped because the site could not be reached: {e}",
                detail=str(e),
                how_it_happened="The HTTP client could not fetch the target, so response headers could not be inspected. This is a blind spot, not a pass: the site may still be missing HSTS, CSP, and other hardening headers.",
                reproduction=f"curl -sI {quote(url)} | head -20",
                fix="Confirm the site is reachable, then rerun WebDoctor. If the site blocks automated clients, allow the tester's IP or provide a staging URL.",
            )],
            notes=f"Skipped because the site could not be reached: {e}",
        )

    headers = {k.lower(): v for k, v in resp.headers.items()}
    is_https = resp.url.startswith("https://")

    # 1. HSTS (only meaningful on HTTPS)
    if is_https:
        hsts = headers.get("strict-transport-security")
        if not hsts:
            issues.append(Issue(
                id="SEC_HSTS_MISSING",
                category="security",
                severity=Severity.WARNING,
                title="Missing Strict-Transport-Security (HSTS) Header",
                summary="The browser cannot enforce HTTPS for future visits to this origin.",
                detail="Header: Strict-Transport-Security not present.",
                how_it_happened="Without HSTS, a user who types the HTTP URL first can be intercepted by an attacker performing a SSL-strip/man-in-the-middle attack before the HTTPS redirect happens.",
                reproduction=f"curl -sI {quote(resp.url)} | grep -i strict-transport-security",
                fix="Add to your web server: `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`. Enable only after confirming all subdomains support HTTPS.",
            ))
        else:
            max_age = re.search(r"(?:^|;)\s*max-age\s*=\s*(\d+)", hsts, re.IGNORECASE)
            if not max_age or int(max_age.group(1)) < 31536000:
                issues.append(Issue(
                    id="SEC_HSTS_WEAK",
                    category="security",
                    severity=Severity.WARNING,
                    title="Weak Strict-Transport-Security (HSTS) Policy",
                    summary="HSTS is present but max-age is missing or too short to protect users.",
                    detail=f"Header: {hsts}",
                    how_it_happened="A short or missing max-age tells browsers to stop enforcing HTTPS quickly, leaving a window where an SSL-strip attack can still succeed.",
                    reproduction=f"curl -sI {quote(resp.url)} | grep -i strict-transport-security",
                    fix="Use `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` once all subdomains are HTTPS-ready.",
                ))

    # 2. Content-Security-Policy
    csp = headers.get("content-security-policy", "")
    has_frame_ancestors = bool(re.search(r"\bframe-ancestors\s+", csp, re.IGNORECASE))
    if not csp:
        issues.append(Issue(
            id="SEC_CSP_MISSING",
            category="security",
            severity=Severity.WARNING,
            title="Missing Content-Security-Policy (CSP) Header",
            summary="No CSP is defined to restrict where scripts, styles, and data can be loaded from.",
            detail="Header: Content-Security-Policy not present.",
            how_it_happened="CSP is the primary defense-in-depth against Cross-Site Scripting (XSS). Without it, an attacker who injects JavaScript can freely exfiltrate session data and user input to attacker-controlled origins.",
            reproduction=f"curl -sI {quote(resp.url)} | grep -i content-security-policy",
            fix="Deploy a CSP such as `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self';`. Start with `Content-Security-Policy-Report-Only` to catch breaks before enforcing.",
        ))

    # 3. X-Frame-Options
    xfo = headers.get("x-frame-options", "").upper()
    if not xfo and not has_frame_ancestors:
        issues.append(Issue(
            id="SEC_XFO_MISSING",
            category="security",
            severity=Severity.WARNING,
            title="Missing X-Frame-Options (Clickjacking Protection)",
            summary="This page can be embedded in an <iframe> on external sites.",
            detail="Header: X-Frame-Options not present.",
            how_it_happened="An attacker can embed your page inside a transparent iframe overlay on a malicious site, tricking authenticated users into clicking buttons or submitting forms without realizing it.",
            reproduction=f"curl -sI {quote(resp.url)} | grep -i x-frame-options",
            fix="Set `X-Frame-Options: DENY` (or `SAMEORIGIN`), or specify `frame-ancestors 'self'` inside your Content-Security-Policy.",
        ))

    # 4. X-Content-Type-Options
    xcto = headers.get("x-content-type-options", "").lower()
    if xcto != "nosniff":
        issues.append(Issue(
            id="SEC_XCTO_MISSING",
            category="security",
            severity=Severity.WARNING,
            title="Missing X-Content-Type-Options: nosniff",
            summary="Browsers may MIME-sniff response bodies and execute them as scripts.",
            detail=f"Header: X-Content-Type-Options = '{headers.get('x-content-type-options', 'missing')}'.",
            how_it_happened="When MIME sniffing is not disabled, browsers can re-interpret a non-executable file (like a user-uploaded image) as JavaScript if its content looks like script, creating an XSS vector.",
            reproduction=f"curl -sI {quote(resp.url)} | grep -i x-content-type-options",
            fix="Add the header `X-Content-Type-Options: nosniff` to all responses in your server or framework middleware.",
        ))

    # 5. Referrer-Policy
    rp = headers.get("referrer-policy")
    if not rp:
        issues.append(Issue(
            id="SEC_REFERRER_MISSING",
            category="security",
            severity=Severity.INFO,
            title="Missing Referrer-Policy Header",
            summary="Full URLs (including query parameters) may leak in the Referer header to external origins.",
            detail="Header: Referrer-Policy not present.",
            how_it_happened="Without an explicit policy, browsers fall back to default behavior and may send full page URLs containing tokens, email addresses, or internal IDs to third-party domains linked from the page.",
            reproduction=f"curl -sI {quote(resp.url)} | grep -i referrer-policy",
            fix="Set `Referrer-Policy: strict-origin-when-cross-origin` (or `no-referrer`) in server response headers.",
        ))

    # 6. Permissions-Policy
    pp = headers.get("permissions-policy")
    if not pp:
        issues.append(Issue(
            id="SEC_PERMISSIONS_POLICY_MISSING",
            category="security",
            severity=Severity.INFO,
            title="Missing Permissions-Policy Header",
            summary="Browser features (camera, microphone, geolocation) are not explicitly restricted.",
            detail="Header: Permissions-Policy not present.",
            how_it_happened="Permissions-Policy restricts which powerful web APIs embedded frames and third-party scripts may access, reducing the blast radius of compromised dependencies.",
            reproduction=f"curl -sI {quote(resp.url)} | grep -i permissions-policy",
            fix="Add `Permissions-Policy: camera=(), microphone=(), geolocation=()` to restrict unneeded browser capabilities.",
        ))

    # 7. Cookie security flags
    cookies = resp.cookies
    raw_cookies = [
        v for k, v in resp.raw.headers.items() if k.lower() == "set-cookie"
    ]
    for c in cookies:
        problems = []
        raw = _set_cookie_for(c.name, raw_cookies)
        if is_https and not c.secure:
            problems.append("missing 'Secure' flag (can be sent over plaintext HTTP)")
        has_httponly = (
            c.has_nonstandard_attr("httponly")
            or bool(getattr(c, "_rest", {}).get("HttpOnly"))
            or bool(getattr(c, "_rest", {}).get("httponly"))
            or _has_attr(raw, r"(?:^|;)\s*httponly(?:\s*=|;|$)")
        )
        if raw and not has_httponly:
            problems.append("missing 'HttpOnly' flag (accessible to JavaScript / XSS exfiltration)")
        if raw and not _has_attr(raw, r"(?:^|;)\s*samesite\s*=\s*(?:lax|strict)(?:\s*;|$)"):
            problems.append("missing 'SameSite=Lax/Strict' flag (CSRF exposure)")
        if problems:
            issues.append(Issue(
                id="SEC_INSECURE_COOKIE",
                category="security",
                severity=Severity.WARNING,
                title=f"Insecure Cookie Flag: '{c.name}'",
                summary=f"Cookie '{c.name}' lacks hardening flags: {', '.join(problems)}.",
                detail=f"Cookie name={c.name}, domain={c.domain}, secure={c.secure}.",
                how_it_happened="When session/authentication cookies lack 'Secure', they can be leaked over plaintext HTTP. Without 'HttpOnly' they are readable by JavaScript after XSS; without 'SameSite' they can be sent on cross-site requests.",
                reproduction=f"curl -sI {quote(resp.url)} | grep -i set-cookie",
                fix="Set `Secure; HttpOnly; SameSite=Lax` on every authentication/session cookie in your application's session configuration.",
            ))

    # 8. Server version disclosure
    server = headers.get("server", "")
    xpow = headers.get("x-powered-by", "")
    if any(ch.isdigit() for ch in server) or xpow:
        issues.append(Issue(
            id="SEC_VERSION_DISCLOSURE",
            category="security",
            severity=Severity.INFO,
            title="Server / Technology Version Disclosure",
            summary="HTTP response headers reveal specific server technology and version numbers.",
            detail=f"Server: {server or 'n/a'} | X-Powered-By: {xpow or 'n/a'}",
            how_it_happened="Detailed version banners make it trivial for attackers to look up known CVEs for that exact software release and automate targeted exploits.",
            reproduction=f"curl -sI {quote(resp.url)} | grep -iE 'server:|x-powered-by:'",
            fix="Disable banners: in nginx add `server_tokens off;`, in Apache `ServerTokens Prod`, in Express remove `app.disable('x-powered-by')`, in PHP set `expose_php = Off`.",
        ))

    has_crit = any(i.severity == Severity.CRITICAL for i in issues)
    has_err = any(i.severity == Severity.ERROR for i in issues)
    has_warn = any(i.severity == Severity.WARNING for i in issues)
    status = CheckStatus.FAILED if (has_crit or has_err) else (CheckStatus.WARN if has_warn else CheckStatus.PASSED)

    return CheckResult(
        name="Security Headers & Cookies",
        category="security",
        status=status,
        issues=issues,
        metrics={"headers_checked": len(headers), "cookies_checked": len(cookies)},
    )
