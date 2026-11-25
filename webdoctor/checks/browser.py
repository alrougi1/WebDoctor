"""Real browser testing via Playwright: JS errors, console logs, failed requests, performance timing, screenshot, and popup/survey dismissal."""
from __future__ import annotations

import os
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Error as PlaywrightError

from webdoctor.models import CheckResult, CheckStatus, Issue, Severity
from webdoctor.config import DEFAULT_USER_AGENT, MAX_PAGE_WAIT_MS

SURVEY_TRACKER_SIGNATURES = [
    ("Hotjar (user feedback & surveys)", "hotjar.com"),
    ("Survicate (surveys)", "survicate.com"),
    ("Qualtrics (surveys & CX)", "qualtrics.com"),
    ("Usabilla/GetFeedback (feedback)", "usabilla.com"),
    ("Pendo (guides & feedback)", "pendo.io"),
    ("SurveyMonkey (surveys)", "surveymonkey.com"),
    ("Google Forms (embedded survey)", "docs.google.com/forms"),
    ("Appcues (product adoption/surveys)", "appcues.com"),
    ("UserTesting (usability)", "usertesting.com"),
    ("Refiner (survey platform)", "refiner.io"),
]

# Only dismiss controls inside real dialogs/consent containers or known vendor
# roots. A bare "Accept"/"Close" button elsewhere can be a form submit and must
# never be clicked by the diagnostic runner.
OVERLAY_DISMISS_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#onetrust-consent-sdk button[id*='accept' i]",
    "[role='dialog'] button:has-text('Accept')",
    "[role='dialog'] button:has-text('I agree')",
    "[role='dialog'] button:has-text('Got it')",
    "[role='dialog'] button[aria-label*='close' i]",
    "[role='dialog'] [data-testid='modal-close-button']",
    ".modal button.close",
    ".cookie-banner button:has-text('Accept')",
    "#cookie-consent button:has-text('Accept')",
    ".survey-modal .close",
    "#hotjar-feedback button[aria-label*='close' i]",
]


def _dismiss_overlays(page) -> bool:
    """Click at most one clearly-scoped overlay control; return whether it clicked."""
    for selector in OVERLAY_DISMISS_SELECTORS:
        try:
            element = page.locator(selector).first
            if not element.is_visible(timeout=500):
                continue
            # Extra guard: never activate a form submit button, even inside a dialog.
            if element.evaluate("(node) => node.type === 'submit' && !!node.closest('form')"):
                continue
            element.click(timeout=1500)
            page.wait_for_timeout(250)
            return True
        except Exception:
            continue
    return False


def run_browser_check(
    url: str,
    screenshot_dir: Optional[str] = None,
    timeout_ms: int = MAX_PAGE_WAIT_MS,
    session_path: Optional[str] = None,
    ignore_https_errors: bool = False,
) -> CheckResult:
    issues: List[Issue] = []
    metrics: Dict[str, Any] = {}
    js_errors: List[Dict[str, str]] = []
    console_messages: List[Dict[str, str]] = []
    failed_requests: List[Dict[str, Any]] = []
    response_stats: List[Dict[str, Any]] = []
    seen_urls: List[str] = []
    screenshot_path: Optional[str] = None
    final_url = url
    page_title = ""
    dismissed_overlay = False

    t_start = time.perf_counter()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=ignore_https_errors,
                storage_state=session_path if session_path and os.path.exists(session_path) else None,
            )
            page = context.new_page()

            # Event: uncaught JS runtime errors
            page.on(
                "pageerror",
                lambda exc: js_errors.append({
                    "message": str(exc),
                    "stack": getattr(exc, "stack", None) or str(exc),
                }),
            )

            # Event: console messages
            page.on(
                "console",
                lambda msg: console_messages.append({
                    "type": msg.type,
                    "text": msg.text,
                    "location": f"{msg.location.get('url','')}:{msg.location.get('lineNumber','')}",
                }),
            )

            # Event: network responses
            def on_response(resp):
                try:
                    seen_urls.append(resp.url)
                    if resp.status >= 400:
                        failed_requests.append({
                            "url": resp.url,
                            "status": resp.status,
                            "status_text": resp.status_text,
                            "resource_type": resp.request.resource_type,
                        })
                    size = 0
                    try:
                        size = len(resp.body())
                    except Exception:
                        size = int(resp.headers.get("content-length", 0) or 0)
                    response_stats.append({
                        "url": resp.url,
                        "status": resp.status,
                        "type": resp.request.resource_type,
                        "size": size,
                    })
                except Exception:
                    pass

            page.on("response", on_response)

            # Event: requests that failed before receiving a response
            def on_request_failed(req):
                try:
                    failed_requests.append({
                        "url": req.url,
                        "status": 0,
                        "status_text": req.failure or "request failed",
                        "resource_type": req.resource_type,
                    })
                except Exception:
                    pass

            page.on("requestfailed", on_request_failed)

            # Navigate
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except PlaywrightError:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except PlaywrightError as e:
                    return CheckResult(
                        name="Browser & Runtime Health",
                        category="browser",
                        status=CheckStatus.FAILED,
                        issues=[
                            Issue(
                                id="BROWSER_NAVIGATION_FAILED",
                                category="browser",
                                severity=Severity.CRITICAL,
                                source="environment",
                                title="Browser Navigation Failed",
                                summary=f"Headless browser could not load the page: {e}",
                                detail=str(e),
                                how_it_happened="The browser attempted to open the URL but the navigation aborted before any page content could render. This can happen when the site refuses connections, an interstitial (bot-detection) blocks automation, DNS fails, or the page takes longer than the timeout to begin responding.",
                                reproduction=f"Open {url} in an incognito Chromium browser with DevTools Network tab recording.",
                                fix="Ensure the site is publicly reachable, no captcha walls or geo-blocks are denying the automated tester's IP, and server response times stay within timeouts.",
                            )
                        ],
                    )

            final_url = page.url
            try:
                page_title = page.title()
            except Exception:
                page_title = ""

            # Dismiss intrusive survey / feedback / cookie popups so the test sees real content
            page.wait_for_timeout(400)
            dismissed_overlay = _dismiss_overlays(page)

            # Performance timing via browser PerformanceNavigationTiming API
            perf = {}
            try:
                perf = page.evaluate("""() => {
                    const nav = performance.getEntriesByType('navigation')[0];
                    const paints = performance.getEntriesByType('paint');
                    const fp = paints.find(p => p.name === 'first-paint');
                    const fcp = paints.find(p => p.name === 'first-contentful-paint');
                    return nav ? {
                        dns_ms: Math.round(nav.domainLookupEnd - nav.domainLookupStart),
                        tcp_ms: Math.round(nav.connectEnd - nav.connectStart),
                        ttfb_ms: Math.round(nav.responseStart - nav.requestStart),
                        download_ms: Math.round(nav.responseEnd - nav.responseStart),
                        dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd - nav.startTime),
                        load_ms: Math.round(nav.loadEventEnd - nav.startTime),
                        first_paint_ms: fp ? Math.round(fp.startTime) : null,
                        first_contentful_paint_ms: fcp ? Math.round(fcp.startTime) : null,
                    } : {};
                }""")
            except Exception:
                perf = {}

            # Screenshot
            if screenshot_dir:
                os.makedirs(screenshot_dir, exist_ok=True)
                slug = urlparse(url).netloc.replace(":", "_").replace("/", "") or "page"
                stamp = int(time.time())
                screenshot_path = os.path.join(screenshot_dir, f"{slug}_{stamp}.png")
                try:
                    page.screenshot(path=screenshot_path, full_page=True, timeout=10000)
                except Exception:
                    screenshot_path = None

            context.close()
            browser.close()

    except Exception as e:
        return CheckResult(
            name="Browser & Runtime Health",
            category="browser",
            status=CheckStatus.FAILED,
            issues=[
                Issue(
                    id="BROWSER_LAUNCH_FAILED",
                    category="browser",
                    severity=Severity.CRITICAL,
                    source="environment",
                    title="Browser Test Runner Error",
                    summary=f"Failed to execute browser test: {e}",
                    detail=str(e),
                    how_it_happened="The headless Chromium runner encountered an internal failure (missing binary, memory exhaustion, or IPC failure).",
                    reproduction="Verify playwright: `playwright install chromium` then rerun.",
                    fix="Reinstall browser binaries with `playwright install chromium`.",
                )
            ],
        )

    duration_ms = int((time.perf_counter() - t_start) * 1000)

    # 1. Uncaught JavaScript runtime errors -> CRITICAL / ERROR issues with stack
    for k, err in enumerate(js_errors[:8]):
        stack = err.get("stack", "")
        message = err.get("message", "Unknown JavaScript error")
        analysis = _analyze_js_error(message, stack)
        issues.append(Issue(
            id=f"JS_RUNTIME_{k+1}",
            category="browser",
            severity=Severity.ERROR,
            title=f"JavaScript Exception: {message[:110]}",
            summary=f"Uncaught exception stopped a script from running correctly on the page.",
            detail=f"Error message: {message}\n\nStack trace:\n{stack[:1400]}",
            how_it_happened=analysis["how"],
            reproduction=analysis["repro"],
            fix=analysis["fix"],
            source="rule",
        ))

    # 2. Console errors (distinct from pageerror: logged via console.error)
    console_errors = [m for m in console_messages if m.get("type") in ("error", "assert")]
    for k, cm in enumerate(console_errors[:6]):
        issues.append(Issue(
            id=f"JS_CONSOLE_{k+1}",
            category="browser",
            severity=Severity.WARNING,
            title=f"Console Error: {cm['text'][:100]}",
            summary="A script explicitly logged an error to console.error().",
            detail=f"Message: {cm['text']}\nLocation: {cm.get('location','')}",
            how_it_happened="Application JavaScript (or a third-party script) executed `console.error()`. This often signals a caught-but-unhandled exception in an async handler, a rejected Promise that had no `.catch()`, or a failed analytics/widget initialization.",
            reproduction=f"Open {url} in DevTools, go to Console, reload the page.",
            fix="Locate the source file and line from the location above, add proper error handling, and ensure fallback UI is rendered when non-critical integrations fail.",
        ))

    # 3. Failed sub-resource requests
    for k, fr in enumerate(failed_requests[:10]):
        status = fr.get("status", 0)
        rtype = fr.get("resource_type", "resource")
        rurl = fr.get("url", "")
        sev = Severity.ERROR if rtype in ("script", "stylesheet", "document") else Severity.WARNING
        issues.append(Issue(
            id=f"NET_FAIL_{k+1}",
            category="browser",
            severity=sev,
            title=f"Failed Sub-Resource: HTTP {status or 'FAILED'} on {rtype}",
            summary=f"The browser could not load a {rtype} resource: {rurl[:100]}",
            detail=f"Resource type: {rtype}\nURL: {rurl}\nStatus: {status} ({fr.get('status_text','')})",
            how_it_happened=_explain_resource_failure(status, rurl),
            reproduction=f"curl -sI '{rurl}'",
            fix=_fix_resource_failure(status, rtype),
        ))

    # 4. Page weight & heavy assets
    total_bytes = sum(r["size"] for r in response_stats)
    heavy = [r for r in response_stats if r["size"] > 1_000_000]
    for r in heavy[:3]:
        issues.append(Issue(
            id="PERF_HEAVY_ASSET",
            category="performance",
            severity=Severity.WARNING,
            title=f"Heavy Asset ({r['size']//1024} KB): {os.path.basename(r['url'].split('?')[0])[:40]}",
            summary=f"A single {r['type']} asset exceeds 1 MB and slows initial render on mobile.",
            detail=f"URL: {r['url']}\nSize: {r['size']:,} bytes\nType: {r['type']}",
            how_it_happened="Unoptimized images, unminified bundles, or embedded video files push payload sizes beyond recommended budgets, directly degrading First Contentful Paint and Largest Contentful Paint.",
            reproduction=f"curl -s '{r['url']}' -o /tmp/asset && ls -lh /tmp/asset",
            fix="Compress and convert images to WebP/AVIF, lazy-load below-the-fold media, split JavaScript bundles, and serve pre-compressed assets via CDN.",
        ))

    if total_bytes > 5_000_000:
        issues.append(Issue(
            id="PERF_PAGE_WEIGHT",
            category="performance",
            severity=Severity.WARNING,
            title=f"Large Total Page Weight ({total_bytes//1_048_576} MB)",
            summary=f"The page loaded {total_bytes:,} bytes across {len(response_stats)} requests.",
            detail=f"Total resources: {len(response_stats)}, total bytes: {total_bytes:,}.",
            how_it_happened="Accumulating unoptimized assets, third-party trackers, and full-resolution media creates a payload that causes slow page interaction on metered or slower mobile networks.",
            reproduction=f"Check DevTools Network tab: total transferred bytes for {url}.",
            fix="Set a 1.5–2 MB performance budget, audit third-party scripts, lazy-load media, and enable HTTP compression.",
        ))

    # 5. Core performance thresholds
    dcl = perf.get("dom_content_loaded_ms")
    if dcl and dcl > 4000:
        issues.append(Issue(
            id="PERF_SLOW_DCL",
            category="performance",
            severity=Severity.WARNING,
            title=f"Slow DOMContentLoaded ({dcl} ms)",
            summary=f"The browser took {dcl} ms before the DOM was fully parsed and scripts finished.",
            detail=f"DOMContentLoaded: {dcl} ms (good: < 1500 ms).",
            how_it_happened="Render-blocking CSS, synchronous scripts in the <head>, or deeply nested DOM trees hold up parsing of the document.",
            reproduction=f"Run Lighthouse or check DevTools Performance panel for {url}.",
            fix="Add `async`/`defer` to non-critical script tags, inline critical CSS, and remove render-blocking third-party tags.",
        ))

    # 6. Overlay / survey popup detection
    if dismissed_overlay:
        issues.append(Issue(
            id="UX_OVERLAY_DISMISSED",
            category="browser",
            severity=Severity.INFO,
            title="A Cookie/Survey Overlay Was Detected and Auto-Dismissed",
            summary="A consent or feedback modal covered page content on load; the tester closed it to continue tests.",
            detail="The headless runner detected and dismissed a popup overlay before measuring page health.",
            how_it_happened="First-visit interstitials (cookie consent, survey prompts, newsletter modals) block access until dismissed. While often required for compliance, intrusive overlays increase bounce rates and may interfere with automated QA and SEO renderers.",
            reproduction=f"Open {url} in a fresh incognito window and check the first-view popup.",
            fix="Delay survey/feedback prompts until the user engages; keep consent banners minimal and accessible with Esc-to-close.",
        ))

    # 7. Survey & feedback tracker inventory
    found_trackers = []
    seen_all = " ".join(seen_urls)
    for name, sig in SURVEY_TRACKER_SIGNATURES:
        if sig in seen_all:
            found_trackers.append(name)
    if found_trackers:
        issues.append(Issue(
            id="UX_SURVEY_TRACKERS",
            category="browser",
            severity=Severity.INFO,
            title=f"Survey/Feedback Tools Detected ({len(found_trackers)})",
            summary=f"The page loads user feedback tooling: {', '.join(found_trackers)}.",
            detail="Detected via network requests:\n" + "\n".join(f"- {t}" for t in found_trackers),
            how_it_happened="Third-party survey and feedback SDKs inject popup widgets into the page. Multiple concurrent feedback tools can slow load times and create conflicting modal popups for visitors.",
            reproduction=f"Inspect Network tab for these hosts: " + ", ".join(t.split()[0] for t in found_trackers),
            fix="Consolidate to a single feedback vendor where possible, load the SDK async/deferred, and suppress surveys on critical conversion flows.",
        ))

    metrics = {
        "final_url": final_url,
        "page_title": page_title,
        "authenticated": bool(session_path and os.path.exists(session_path)),
        "js_error_count": len(js_errors),
        "console_error_count": len(console_errors),
        "console_total": len(console_messages),
        "failed_request_count": len(failed_requests),
        "resource_count": len(response_stats),
        "total_bytes": total_bytes,
        "survey_overlay_dismissed": dismissed_overlay,
        "survey_trackers": found_trackers,
        "perf": perf,
        "screenshot": screenshot_path,
    }

    has_crit = any(i.severity == Severity.CRITICAL for i in issues)
    has_err = any(i.severity == Severity.ERROR for i in issues)
    has_warn = any(i.severity == Severity.WARNING for i in issues)
    status = CheckStatus.FAILED if (has_crit or has_err) else (CheckStatus.WARN if has_warn else CheckStatus.PASSED)

    return CheckResult(
        name="Browser & Runtime Health",
        category="browser",
        status=status,
        duration_ms=duration_ms,
        issues=issues,
        metrics=metrics,
    )


def _analyze_js_error(msg: str, stack: str) -> Dict[str, str]:
    m = msg.lower()
    if "cannot read properties of undefined" in m or "cannot read property" in m:
        return {
            "how": f"JavaScript attempted to read a property on a value that is `undefined`. This happens when an API response is shaped differently than expected (missing a key), when an async `await` returned before data resolved, or when an optional field is accessed without null-safety checks.",
            "repro": "In DevTools Console, check the failing function in the stack trace; set a breakpoint just before the property access and inspect the parent object's value.",
            "fix": "Use optional chaining (`obj?.property`), provide default values (`(arr ?? []).map(...)`), and add schema validation (zod/joi) at API boundaries.",
        }
    if "is not a function" in m:
        return {
            "how": "Code attempted to invoke something as a function that is actually `undefined`, `null`, a boolean, or not yet initialized (e.g. an import that failed to resolve, or a library method called before the library loaded).",
            "repro": "Inspect the line from the stack trace; verify the variable being called is imported correctly and initialized before the call site.",
            "fix": "Verify import paths and bundler configuration; ensure the function is defined before invocation; guard call sites with `typeof fn === 'function'` where necessary.",
        }
    if "is not defined" in m:
        name = msg.split("is not defined")[0].strip().split()[-1] if msg.split("is not defined")[0].strip() else "identifier"
        return {
            "how": f"The identifier `{name}` was referenced in code but never declared in the current scope. This typically stems from a typo in a variable name, an un-imported module, or a script order problem where a global is used before its defining script runs.",
            "repro": f"Search the codebase for `{name}`; check whether it is imported or declared before its first usage.",
            "fix": f"Declare or import `{name}`, fix any typo, and ensure scripts load in correct dependency order (or merge them via a bundler).",
        }
    if "cors" in m or "has been blocked by cors policy" in m:
        return {
            "how": "The browser's Same-Origin Policy blocked a cross-origin fetch/xhr request because the destination server did not include the required `Access-Control-Allow-Origin` response header (or the preflight OPTIONS request failed).",
            "repro": f"Open DevTools -> Network, filter by fetch/XHR, click the red request and view the Headers tab for missing Access-Control-* headers.",
            "fix": "On the API server, add CORS headers: `Access-Control-Allow-Origin: <your-origin>`, `Access-Control-Allow-Methods: GET,POST,PUT,DELETE,OPTIONS`, and handle `OPTIONS` preflight requests returning 204.",
        }
    if "unexpected token" in m or "syntaxerror" in m:
        return {
            "how": "The JavaScript engine encountered syntax it cannot parse — often an HTML 404/500 error page was served where JavaScript was expected (e.g. a broken script src returning HTML), or modern syntax is being executed in a context that does not support it.",
            "repro": "Check DevTools Network tab for script files whose Content-Type is `text/html` instead of `application/javascript`.",
            "fix": "Verify <script src> paths point to real files, configure the build/transpilation target correctly, and ensure SPA fallback rules don't serve index.html for missing .js files.",
        }
    return {
        "how": f"An unhandled JavaScript exception (`{msg[:100]}`) bubbled up to `window.onerror`. When uncaught, the executing script terminates, which can break interactive widgets, form submissions, or entire client-rendered pages.",
        "repro": "Open DevTools -> Sources, enable 'Pause on uncaught exceptions', then reload to land exactly on the failing line.",
        "fix": "Wrap error-prone sections in try/catch, add a global error boundary (e.g. React ErrorBoundary), and send stack traces to an exception tracking service like Sentry.",
    }


def _explain_resource_failure(status: int, url: str) -> str:
    if status == 404:
        return "The browser requested a referenced asset (script, stylesheet, font, or image) that does not exist at that path. The link in the HTML/CSS is broken, the file was not deployed, or there is a casing/typo mismatch."
    if status == 500:
        return "The asset request hit an application endpoint (or dynamic image/font handler) that crashed while generating the response."
    if status == 403:
        return "The server or CDN refused permission to the asset — likely a misconfigured hotlink protection rule, missing CORS credentials, or bucket permission issue."
    if status == 0:
        return "The request never received a response: DNS lookup failed, the server refused the connection, an ad blocker intercepted it, or an SSL error aborted it."
    return f"The sub-resource request completed with HTTP status {status}."


def _fix_resource_failure(status: int, rtype: str) -> str:
    if status == 404:
        return f"Verify the {rtype} file exists in your public build folder, fix the referencing URL/import path, and ensure case-sensitive file naming matches."
    if status == 500:
        return "Check the application handler serving this dynamic resource and fix the crash."
    if status == 403:
        return "Check CDN/bucket permissions, CORS configuration, and hotlinking rules."
    return "Check DNS, TLS configuration, and ensure the asset host is reachable."
