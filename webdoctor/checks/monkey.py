"""Deterministic hard monkey test for browser-driven sites.

This suite performs seeded, bounded, random user-like interactions in a real
Chromium page (clicks, typing, scrolling, keyboard input and viewport changes)
and reports instability it causes. It is deliberately safe by default:

* only same-origin, non-destructive targets are chosen;
* hidden, disabled, password, file and form-submit controls are ignored;
* external/off-origin document navigation is blocked by a route guard;
* no Enter/submit key is pressed.

Every run is reproducible from ``(url, seed, actions, delay_ms)``. The full
action trace is stored in ``CheckResult.metrics['trace']`` and rendered by the
HTML/Markdown reporters.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, Error as PlaywrightError

from webdoctor.checks.browser import (
    _analyze_js_error,
    _dismiss_overlays,
    _explain_resource_failure,
    _fix_resource_failure,
)
from webdoctor.config import (
    DEFAULT_USER_AGENT,
    MAX_PAGE_WAIT_MS,
    MONKEY_DEFAULT_ACTIONS,
    MONKEY_DEFAULT_DELAY_MS,
    MONKEY_DEFAULT_SEED,
    MONKEY_MAX_ACTIONS,
    MONKEY_MAX_DELAY_MS,
    MONKEY_MIN_ACTIONS,
)
from webdoctor.models import CheckResult, CheckStatus, Issue, Severity


VIEWPORTS: Tuple[Tuple[int, int], ...] = (
    (1440, 900),
    (1024, 768),
    (768, 1024),
    (390, 844),
)

# Words that imply data loss, money movement, account changes or irreversible
# state. Matching is intentionally conservative: a monkey must never "test"
# destructive controls on a user's live account.
DESTRUCTIVE_RE = re.compile(
    r"(?:delete|remove|logout|log[\s_-]*out|sign[\s_-]*out|submit|send|pay|"
    r"payment|purchase|buy|checkout|cancel|unsubscribe|reset|destroy|deactivate|"
    r"clear|erase|close\s+account|confirm|accept|agree|install|upload|download|"
    r"approve|authorize|publish|archive)",
    re.IGNORECASE,
)

DANGEROUS_SCHEME_RE = re.compile(r"^(?:javascript|mailto|tel|data|blob):", re.IGNORECASE)


@dataclass(frozen=True)
class MonkeyOptions:
    """Validated, JSON-friendly settings for one monkey run."""

    seed: int = MONKEY_DEFAULT_SEED
    actions: int = MONKEY_DEFAULT_ACTIONS
    delay_ms: int = MONKEY_DEFAULT_DELAY_MS
    allow_navigation: bool = True


def validate_monkey_options(seed: int, actions: int, delay_ms: int) -> MonkeyOptions:
    """Raise ``ValueError`` for out-of-range monkey settings."""
    try:
        seed_i = int(seed)
        actions_i = int(actions)
        delay_i = int(delay_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("Monkey seed, actions and delay must be integers.") from exc
    if not MONKEY_MIN_ACTIONS <= actions_i <= MONKEY_MAX_ACTIONS:
        raise ValueError(
            f"--monkey-actions must be between {MONKEY_MIN_ACTIONS} and {MONKEY_MAX_ACTIONS}."
        )
    if not 0 <= delay_i <= MONKEY_MAX_DELAY_MS:
        raise ValueError(f"--monkey-delay-ms must be between 0 and {MONKEY_MAX_DELAY_MS}.")
    return MonkeyOptions(seed=seed_i, actions=actions_i, delay_ms=delay_i)


# JavaScript keeps candidate selection inside the DOM so selectors are always
# derived from the page as it exists at run time. Python applies the final
# safety filter so the same policy is covered by unit tests without a browser.
_CANDIDATE_SCRIPT = r"""() => {
  const esc = (s) => (window.CSS && CSS.escape)
    ? CSS.escape(String(s))
    : String(s).replace(/[^A-Za-z0-9_-]/g, '\\$&');
  const cssPath = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + esc(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        parts.unshift('#' + esc(node.id));
        break;
      }
      const cls = (typeof node.className === 'string' ? node.className : '')
        .trim().split(/\s+/)
        .filter((c) => /^[A-Za-z_][A-Za-z0-9_-]*$/.test(c))
        .slice(0, 2).map((c) => '.' + esc(c)).join('');
      if (cls) part += cls;
      const parent = node.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter((x) => x.tagName === node.tagName);
        if (sameTag.length > 1) part += ':nth-of-type(' + (sameTag.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  const labelFor = (el) => {
    const raw = el.getAttribute('aria-label') || el.getAttribute('title') ||
      (el.tagName === 'INPUT' ? el.value : '') || el.textContent || el.getAttribute('href') || '';
    return String(raw).replace(/\s+/g, ' ').trim().slice(0, 160);
  };
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
      style.visibility !== 'hidden' && Number(style.opacity) > 0 &&
      !el.disabled && el.getAttribute('aria-hidden') !== 'true';
  };
  const excludedInputs = ['password', 'file', 'checkbox', 'radio', 'hidden',
    'submit', 'button', 'image', 'reset', 'range', 'color', 'date',
    'datetime-local', 'month', 'time', 'week'];
  const selector = 'a[href], button, input, textarea, select, summary, ' +
    '[role="button"], [role="textbox"], [contenteditable="true"], [tabindex]';
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    if (!visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const href = el.getAttribute('href') || '';
    if (/^(javascript|mailto|tel|data|blob):/i.test(href) || el.hasAttribute('download')) continue;
    if (tag === 'input' && excludedInputs.includes(type)) continue;
    const clickable = ['a', 'button', 'summary'].includes(tag) || role === 'button' || type === 'button';
    const editable = ['input', 'textarea', 'select'].includes(tag) ||
      role === 'textbox' || el.isContentEditable === true;
    if (!clickable && !editable) continue;
    const selectorText = cssPath(el);
    if (!selectorText) continue;
    out.push({
      selector: selectorText, tag: tag, type: type, role: role,
      label: labelFor(el), href: href,
      clickable: clickable, editable: editable,
    });
  }
  return out.slice(0, 300);
}"""

_HEALTH_SCRIPT = r"""() => ({
  url: location.href,
  title: document.title || '',
  html_len: document.body ? document.body.innerHTML.length : 0,
  nodes: document.querySelectorAll('body *').length,
})"""

# A monkey must not mutate user data. Block every form submission even if a
# scripted event handler tries to submit a form as a side effect of a click.
_SUBMIT_GUARD_SCRIPT = r"""(() => {
  if (window.__webdoctorMonkeyGuardInstalled) return;
  window.__webdoctorMonkeyGuardInstalled = true;
  window.__webdoctorMonkeyBlockedSubmits = 0;
  document.addEventListener('submit', (event) => {
    window.__webdoctorMonkeyBlockedSubmits++;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  const blocked = () => { window.__webdoctorMonkeyBlockedSubmits++; return false; };
  if (typeof HTMLFormElement !== 'undefined') {
    if (HTMLFormElement.prototype.requestSubmit) HTMLFormElement.prototype.requestSubmit = blocked;
    HTMLFormElement.prototype.submit = blocked;
  }
})()"""


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _error_signature(message: str) -> str:
    """Stable short hash for an error message; used to group repeated faults."""
    normalized = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    normalized = re.sub(r"[^a-z0-9 ]", "", normalized)[:240]
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8].upper()


def _config_fingerprint(url: str, options: MonkeyOptions, allow_navigation: bool) -> str:
    payload = (
        f"{url}|{options.seed}|{options.actions}|{options.delay_ms}|{allow_navigation}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _detail_message(issue: Issue, prefix: str) -> str:
    for line in (issue.detail or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return issue.summary or ""


def _browser_error_signatures(prior: Optional[CheckResult]) -> set:
    """Error signatures already reported by the ordinary browser suite."""
    signatures = set()
    if not prior:
        return signatures
    for issue in prior.issues or []:
        if issue.id.startswith("JS_RUNTIME_"):
            signatures.add(_error_signature(_detail_message(issue, "Error message: ")))
        elif issue.id.startswith("JS_CONSOLE_"):
            signatures.add(_error_signature(_detail_message(issue, "Message: ")))
    return signatures


def is_safe_target(
    candidate: Dict[str, Any], base_url: str, *, allow_navigation: bool = True
) -> Tuple[bool, str]:
    """Return whether a DOM candidate may be touched by the monkey."""
    label = str(candidate.get("label") or "")
    if DESTRUCTIVE_RE.search(label):
        return False, "destructive label"
    href = str(candidate.get("href") or "")
    if DANGEROUS_SCHEME_RE.match(href):
        return False, "dangerous URL scheme"
    if candidate.get("download"):
        return False, "download link"
    if href:
        target = urlparse(urljoin(base_url, href))
        base = urlparse(base_url)
        if target.scheme not in ("http", "https"):
            return False, "non-HTTP URL"
        if target.netloc.lower() != base.netloc.lower():
            return False, "off-origin URL"
        if not allow_navigation and (target.path != base.path or target.query != base.query):
            return False, "navigation disabled"
        if DESTRUCTIVE_RE.search(target.path) or DESTRUCTIVE_RE.search(target.query):
            return False, "destructive URL"
    return True, "safe"


def build_action_plan(
    seed: int,
    action_count: int,
    candidates: List[Dict[str, Any]],
    base_url: str,
    *,
    allow_navigation: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Build a deterministic action plan and the list of rejected targets."""
    import random

    rng = random.Random(seed)
    safe: List[Dict[str, Any]] = []
    unsafe: List[Dict[str, str]] = []
    for item in sorted(candidates, key=lambda c: str(c.get("selector") or "")):
        ok, reason = is_safe_target(item, base_url, allow_navigation=allow_navigation)
        if ok:
            safe.append(item)
        else:
            unsafe.append({"selector": str(item.get("selector") or ""), "reason": reason})

    clickable = [c for c in safe if c.get("clickable")]
    editable = [c for c in safe if c.get("editable")]
    plan: List[Dict[str, Any]] = []

    for index in range(action_count):
        roll = rng.random()
        if roll < 0.42 and clickable:
            kind = "click"
        elif roll < 0.66 and editable:
            kind = "type"
        elif roll < 0.80:
            kind = "scroll"
        elif roll < 0.91:
            kind = "key"
        else:
            kind = "resize"
        # Deterministic fallbacks when a page has no eligible targets.
        if kind in ("click", "type") and not (clickable if kind == "click" else editable):
            kind = "scroll"

        action: Dict[str, Any] = {"step": index + 1, "type": kind}
        if kind == "click":
            target = clickable[rng.randrange(len(clickable))]
            action.update({
                "selector": target["selector"],
                "tag": target.get("tag", ""),
                "label": target.get("label", ""),
                "href": target.get("href", ""),
            })
        elif kind == "type":
            target = editable[rng.randrange(len(editable))]
            input_type = str(target.get("type") or "").lower()
            if input_type == "number":
                value = str(index % 1000)
            elif input_type == "email":
                value = f"wd{seed}{index}@example.invalid"
            elif input_type == "url":
                value = f"https://example.invalid/wd-{seed}-{index}"
            else:
                value = f"webdoctor-{seed}-{index}"
            action.update({
                "selector": target["selector"],
                "tag": target.get("tag", ""),
                "label": target.get("label", ""),
                "value": value[:80],
            })
        elif kind == "scroll":
            action["dy"] = rng.choice((240, -180, 420, -320))
        elif kind == "key":
            action["key"] = rng.choice(("Tab", "Escape", "ArrowDown", "ArrowUp", "PageDown", "Home", "End"))
        else:
            width, height = VIEWPORTS[rng.randrange(len(VIEWPORTS))]
            action.update({"width": width, "height": height})
        plan.append(action)

    return plan, unsafe


def _monkey_issue(
    issue_id: str,
    title: str,
    summary: str,
    detail: str,
    how: str,
    reproduction: str,
    fix: str,
    severity: Severity = Severity.ERROR,
    category: str = "monkey",
    source: str = "rule",
) -> Issue:
    return Issue(
        id=issue_id,
        category=category,
        severity=severity,
        title=title,
        summary=summary,
        detail=detail,
        how_it_happened=how,
        reproduction=reproduction,
        fix=fix,
        source=source,
    )


def _failed_result(name: str, message: str, issue_id: str, how: str, fix: str) -> CheckResult:
    return CheckResult(
        name=name,
        category="monkey",
        status=CheckStatus.FAILED,
        issues=[_monkey_issue(
            issue_id, "Monkey Test Could Not Run", message, message, how,
            "Re-run with the same seed after fixing the browser/environment problem.", fix,
            Severity.CRITICAL,
            source="environment",
        )],
    )


def _status_for(issues: List[Issue]) -> CheckStatus:
    if any(i.severity in (Severity.CRITICAL, Severity.ERROR) for i in issues):
        return CheckStatus.FAILED
    if any(i.severity == Severity.WARNING for i in issues):
        return CheckStatus.WARN
    return CheckStatus.PASSED


def run_monkey_check(
    url: str,
    *,
    seed: int = MONKEY_DEFAULT_SEED,
    actions: int = MONKEY_DEFAULT_ACTIONS,
    delay_ms: int = MONKEY_DEFAULT_DELAY_MS,
    allow_navigation: bool = True,
    session_path: Optional[str] = None,
    ignore_https_errors: bool = False,
    timeout_ms: int = MAX_PAGE_WAIT_MS,
    screenshot_dir: Optional[str] = None,
    prior_browser_result: Optional[CheckResult] = None,
) -> CheckResult:
    """Run a seeded browser chaos suite and return one ``CheckResult``."""
    name = "Hard Monkey Test"
    started = time.perf_counter()
    try:
        options = validate_monkey_options(seed, actions, delay_ms)
    except ValueError as exc:
        return _failed_result(
            name, str(exc), "MONKEY_INVALID_OPTIONS",
            "The requested monkey settings were outside the safe, bounded range.",
            "Use --monkey-actions 1-500 and --monkey-delay-ms 0-5000.",
        )

    js_errors: List[Dict[str, Any]] = []
    console_errors: List[Dict[str, Any]] = []
    request_failures: List[Dict[str, Any]] = []
    dialogs: List[Dict[str, Any]] = []
    crashes: List[Dict[str, Any]] = []
    offsite_attempts: List[str] = []
    blocked_mutations: List[str] = []
    blocked_submits = 0
    duplicate_browser_errors = 0
    trace: List[Dict[str, Any]] = []
    issues: List[Issue] = []
    candidates: List[Dict[str, Any]] = []
    unsafe: List[Dict[str, str]] = []
    executed = 0
    skipped = 0
    counts = {"click": 0, "type": 0, "scroll": 0, "key": 0, "resize": 0}
    final_url = url
    final_viewport = VIEWPORTS[0]
    screenshot_path: Optional[str] = None
    blank_reported = False

    reproduction = (
        f"webdoctor check {url} --monkey --monkey-seed {options.seed} "
        f"--monkey-actions {options.actions} --monkey-delay-ms {options.delay_ms}"
        + ("" if allow_navigation else " --monkey-no-navigation")
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": final_viewport[0], "height": final_viewport[1]},
                ignore_https_errors=ignore_https_errors,
                accept_downloads=False,
                storage_state=session_path if session_path and os.path.exists(session_path) else None,
            )
            allowed_origin = _origin(url)

            def _guard_route(route, request):
                try:
                    is_document = request.resource_type == "document"
                    if is_document and request.is_navigation_request():
                        if _origin(request.url) != allowed_origin:
                            if len(offsite_attempts) < 20:
                                offsite_attempts.append(request.url)
                            route.abort()
                            return
                    # Never let random interactions POST/PUT/PATCH/DELETE.
                    if (request.method not in ("GET", "HEAD", "OPTIONS")
                            and request.resource_type in ("document", "xhr", "fetch")):
                        if len(blocked_mutations) < 20:
                            blocked_mutations.append(f"{request.method} {request.url}")
                        route.abort()
                        return
                except Exception:
                    pass
                try:
                    route.continue_()
                except Exception:
                    pass

            context.route("**/*", _guard_route)
            page = context.new_page()
            # Register before navigation so scripts running during the initial
            # page load cannot submit a form before the guard exists.
            page.add_init_script(_SUBMIT_GUARD_SCRIPT)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PlaywrightError as exc:
                context.close()
                browser.close()
                return _failed_result(
                    name, f"The monkey test could not open {url}: {exc}",
                    "MONKEY_NAVIGATION_FAILED",
                    "Chromium could not begin navigating to the target before the timeout.",
                    "Verify the site is reachable and raise --timeout if it loads slowly.",
                )

            final_url = page.url
            # Idempotent re-injection for the current document; the init script
            # above already covers the initial load and every later navigation.
            try:
                page.evaluate(_SUBMIT_GUARD_SCRIPT)
            except Exception:
                pass

            def _on_page_error(exc):
                if len(js_errors) < 20:
                    js_errors.append({
                        "message": str(exc),
                        "stack": getattr(exc, "stack", None) or str(exc),
                        "step": len(trace) + 1,
                    })

            def _on_console(msg):
                if msg.type in ("error", "assert") and len(console_errors) < 20:
                    console_errors.append({
                        "type": msg.type,
                        "text": msg.text,
                        "location": f"{msg.location.get('url', '')}:{msg.location.get('lineNumber', '')}",
                        "step": len(trace) + 1,
                    })

            def _on_request_failed(req):
                if len(request_failures) < 20:
                    request_failures.append({
                        "url": req.url,
                        "status": 0,
                        "status_text": req.failure or "request failed",
                        "resource_type": req.resource_type,
                        "step": len(trace) + 1,
                    })

            def _on_dialog(dialog):
                if len(dialogs) < 20:
                    dialogs.append({
                        "type": dialog.type,
                        "message": dialog.message,
                        "step": len(trace) + 1,
                    })
                try:
                    dialog.dismiss()
                except Exception:
                    pass

            def _on_crash(_page=None):
                if len(crashes) < 20:
                    crashes.append({"step": len(trace) + 1})

            # Event listeners are attached after navigation so findings are
            # caused by monkey actions rather than duplicating the browser suite.
            page.on("pageerror", _on_page_error)
            page.on("console", _on_console)
            page.on("requestfailed", _on_request_failed)
            page.on("dialog", _on_dialog)
            page.on("crash", _on_crash)

            _dismiss_overlays(page)
            try:
                candidates = list(page.evaluate(_CANDIDATE_SCRIPT) or [])
            except Exception:
                candidates = []
            plan, unsafe = build_action_plan(
                options.seed, options.actions, candidates, final_url,
                allow_navigation=allow_navigation,
            )

            for action in plan:
                if crashes:
                    break
                step = dict(action)
                step["result"] = "executed"
                try:
                    kind = action["type"]
                    if kind == "click":
                        locator = page.locator(action["selector"]).first
                        if locator.count() == 0 or not locator.is_visible(timeout=500):
                            step.update(result="skipped", reason="target no longer visible")
                        else:
                            locator.click(timeout=1500)
                            counts["click"] += 1
                    elif kind == "type":
                        locator = page.locator(action["selector"]).first
                        if locator.count() == 0 or not locator.is_visible(timeout=500):
                            step.update(result="skipped", reason="target no longer visible")
                        elif str(action.get("tag", "")).lower() == "select":
                            locator.select_option(index=0, timeout=1500)
                            counts["type"] += 1
                        elif locator.evaluate("(n) => n.isContentEditable === true"):
                            locator.click(timeout=1500)
                            page.keyboard.insert_text(str(action.get("value", "")))
                            counts["type"] += 1
                        else:
                            locator.fill(str(action.get("value", "")), timeout=1500)
                            counts["type"] += 1
                    elif kind == "scroll":
                        page.evaluate("(dy) => window.scrollBy(0, dy)", action["dy"])
                        counts["scroll"] += 1
                    elif kind == "key":
                        page.keyboard.press(action["key"])
                        counts["key"] += 1
                    else:
                        width, height = action["width"], action["height"]
                        page.set_viewport_size({"width": width, "height": height})
                        final_viewport = (width, height)
                        counts["resize"] += 1
                except (PlaywrightError, Exception) as exc:  # per-action isolation
                    step.update(result="skipped", reason=str(exc)[:200])

                if step["result"] == "executed":
                    executed += 1
                else:
                    skipped += 1

                if options.delay_ms:
                    page.wait_for_timeout(options.delay_ms)
                try:
                    health = page.evaluate(_HEALTH_SCRIPT)
                    step["url"] = health.get("url", "")
                    if health.get("html_len") == 0 and health.get("nodes") == 0 and not blank_reported:
                        blank_reported = True
                        issues.append(_monkey_issue(
                            "MONKEY_BLANK_PAGE", "Page Went Blank During Monkey Test",
                            f"After monkey step {step['step']} the document body had no rendered content.",
                            f"Action: {step.get('type')} {step.get('selector', step.get('key', ''))}\n"
                            f"URL: {step.get('url', '')}",
                            "An event handler or client-side route change removed all body content without rendering a fallback, or the app crashed client-side.",
                            reproduction,
                            "Add an error boundary and a fallback view; verify client-side routes always render a non-empty root element.",
                        ))
                except Exception:
                    step["url"] = ""
                trace.append(step)

            try:
                final_url = page.url
                blocked_submits = int(page.evaluate("window.__webdoctorMonkeyBlockedSubmits || 0"))
            except Exception:
                pass
            if screenshot_dir:
                os.makedirs(screenshot_dir, exist_ok=True)
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", urlparse(final_url).netloc or "page")
                screenshot_path = os.path.join(screenshot_dir, f"monkey_{safe_name}_{options.seed}.png")
                try:
                    page.screenshot(path=screenshot_path, full_page=True, timeout=10000)
                except Exception:
                    screenshot_path = None

            context.close()
            browser.close()
    except Exception as exc:
        issues.append(_monkey_issue(
            "MONKEY_RUNNER_ERROR", "Monkey Test Runner Error",
            f"The browser chaos runner failed: {exc}",
            str(exc),
            "Chromium or Playwright failed while setting up or tearing down the isolated chaos run.",
            "Re-run `playwright install chromium`, then retry with the same seed.",
            "Reinstall browser binaries and check available memory/disk space.",
            Severity.CRITICAL,
            source="environment",
        ))

    # Suppress faults the ordinary browser suite already reported, then group
    # repeated identical faults into one issue with a stable hashed ID. This
    # avoids both double penalties and accidental merging of different errors.
    prior_signatures = _browser_error_signatures(prior_browser_result)
    js_groups: Dict[str, List[Dict[str, Any]]] = {}
    for err in js_errors:
        message = err.get("message", "Unknown JavaScript error")
        signature = _error_signature(message)
        if signature in prior_signatures:
            duplicate_browser_errors += 1
            continue
        js_groups.setdefault(signature, []).append(err)

    for signature, items in list(js_groups.items())[:8]:
        first = items[0]
        message = first.get("message", "Unknown JavaScript error")
        analysis = _analyze_js_error(message, first.get("stack", ""))
        steps = ", ".join(str(item.get("step", "?")) for item in items[:6])
        issues.append(_monkey_issue(
            f"MONKEY_JS_E{signature}",
            f"Monkey-Triggered JavaScript Exception: {message[:95]}",
            "Random interactions caused an uncaught JavaScript exception.",
            f"Seen {len(items)} time(s) after step(s) {steps}.\nMessage: {message}\n\n"
            f"Stack:\n{first.get('stack', '')[:1400]}",
            analysis["how"],
            f"{reproduction}\nObserve the exception recorded after step(s) {steps}.",
            analysis["fix"],
            Severity.ERROR,
        ))

    console_groups: Dict[str, List[Dict[str, Any]]] = {}
    for msg in console_errors:
        signature = _error_signature(msg.get("text", ""))
        if signature in prior_signatures:
            duplicate_browser_errors += 1
            continue
        console_groups.setdefault(signature, []).append(msg)

    for signature, items in list(console_groups.items())[:6]:
        first = items[0]
        steps = ", ".join(str(item.get("step", "?")) for item in items[:6])
        issues.append(_monkey_issue(
            f"MONKEY_CONSOLE_E{signature}",
            f"Monkey-Triggered Console Error: {first.get('text', '')[:85]}",
            "Random interactions caused application JavaScript to log an error.",
            f"Seen {len(items)} time(s) after step(s) {steps}.\n"
            f"Message: {first.get('text', '')}\nLocation: {first.get('location', '')}",
            "The chaos run exercised an event handler that caught a failure and only logged it with console.error(), or an async callback rejected without a user-visible fallback.",
            f"{reproduction}\nOpen DevTools Console and repeat step(s) {steps}.",
            "Handle the error in that handler, show a user-visible fallback, and replace console.error() with structured error reporting.",
            Severity.WARNING,
        ))

    for index, failed in enumerate(request_failures[:8], 1):
        status = int(failed.get("status") or 0)
        resource_type = failed.get("resource_type", "resource")
        failed_url = failed.get("url", "")
        severity = Severity.ERROR if resource_type in ("script", "stylesheet", "document") else Severity.WARNING
        issues.append(_monkey_issue(
            f"NET_FAIL_{index + 1}",
            f"Monkey-Triggered Failed Request: HTTP {status or 'FAILED'} on {resource_type}",
            f"A random interaction requested a resource that could not be loaded: {failed_url[:100]}",
            f"Triggered after monkey step {failed.get('step', '?')}.\nURL: {failed_url}\nStatus: {status} ({failed.get('status_text', '')})",
            _explain_resource_failure(status, failed_url),
            f"curl -sSI '{failed_url}'",
            _fix_resource_failure(status, resource_type),
            severity,
            "browser",
        ))

    if dialogs:
        first = dialogs[0]
        issues.append(_monkey_issue(
            "MONKEY_DIALOG",
            f"Native Dialog Interrupted the Monkey Test ({len(dialogs)} seen)",
            f"The page opened a blocking {first.get('type', 'javascript')} dialog during random interactions: {first.get('message', '')[:100]}",
            "Dialogs captured: " + "; ".join(
                f"step {d.get('step')}: {d.get('type')}: {d.get('message', '')[:80]}" for d in dialogs[:5]
            ),
            "An interaction branch used alert()/confirm()/prompt(), which blocks the UI thread and stalls automation until dismissed.",
            reproduction,
            "Replace blocking dialogs with accessible in-page notifications and non-blocking confirmation flows.",
            Severity.ERROR if len(dialogs) > 1 else Severity.WARNING,
        ))

    if crashes:
        issues.append(_monkey_issue(
            "MONKEY_PAGE_CRASH",
            f"Browser Renderer Crashed During Monkey Test ({len(crashes)} crash(es))",
            "The page's renderer process crashed while random interactions were running.",
            f"Crashes after steps: {', '.join(str(c.get('step')) for c in crashes[:8])}",
            "Unbounded DOM mutation loops, runaway JavaScript, or a renderer-level browser bug killed the tab.",
            reproduction,
            "Profile the interaction paths that mutate the DOM, add loop bounds and memoization, and test with DevTools performance tooling.",
            Severity.CRITICAL,
        ))

    if offsite_attempts:
        issues.append(_monkey_issue(
            "MONKEY_OFF_ORIGIN_BLOCKED",
            f"Off-Origin Navigation Blocked ({len(offsite_attempts)} attempt(s))",
            "A random interaction tried to leave the tested origin; WebDoctor blocked the navigation.",
            "Blocked targets:\n" + "\n".join(offsite_attempts[:10]),
            "An in-page control navigated to an external origin. The monkey safety guard cancels document navigation outside the tested host.",
            reproduction,
            "If this is expected (for example a payment provider link), document it; otherwise ensure controls keep users inside the intended origin.",
            Severity.INFO,
        ))

    if blocked_submits:
        issues.append(_monkey_issue(
            "MONKEY_SUBMIT_BLOCKED",
            f"Form Submission Blocked by Monkey Safety Guard ({blocked_submits})",
            "A page script attempted to submit a form during random interactions; WebDoctor prevented it.",
            f"Blocked submit attempts: {blocked_submits}. No form data was sent.",
            "A click handler called form.submit()/requestSubmit() or dispatched a submit event. The monkey installs a capture-phase guard so tests never mutate user data.",
            reproduction,
            "If this is expected app behaviour, exercise it with a dedicated integration test on a disposable account instead of the monkey suite.",
            Severity.INFO,
        ))

    if blocked_mutations:
        issues.append(_monkey_issue(
            "MONKEY_MUTATION_BLOCKED",
            f"Non-Idempotent Request Blocked ({len(blocked_mutations)})",
            "A random interaction tried to send a POST/PUT/PATCH/DELETE request; WebDoctor aborted it.",
            "Blocked requests:\n" + "\n".join(blocked_mutations[:10]),
            "A UI handler issued a write request. The monkey route guard aborts all non-idempotent document/XHR/fetch requests to keep the tested account unchanged.",
            reproduction,
            "Test write paths separately with seeded data and an isolated account; keep the monkey suite for read-only UI stability.",
            Severity.INFO,
        ))

    duration_ms = int((time.perf_counter() - started) * 1000)
    metrics: Dict[str, Any] = {
        "seed": options.seed,
        "config_fingerprint": _config_fingerprint(url, options, allow_navigation),
        "planned_actions": options.actions,
        "actions_executed": executed,
        "actions_skipped": skipped,
        "action_counts": counts,
        "candidates_found": len(candidates),
        "unsafe_targets_skipped": len(unsafe),
        "unsafe_targets": unsafe[:40],
        "js_error_count": len(js_errors),
        "console_error_count": len(console_errors),
        "request_failure_count": len(request_failures),
        "dialog_count": len(dialogs),
        "page_crash_count": len(crashes),
        "off_origin_navigation_attempts": offsite_attempts,
        "blocked_submit_count": blocked_submits,
        "blocked_mutation_count": len(blocked_mutations),
        "duplicate_browser_error_events": duplicate_browser_errors,
        "final_url": final_url,
        "final_viewport": list(final_viewport),
        "screenshot": screenshot_path,
        "trace": trace,
    }

    return CheckResult(
        name=name,
        category="monkey",
        status=_status_for(issues),
        duration_ms=duration_ms,
        issues=issues,
        metrics=metrics,
        notes=(f"seed={options.seed}; actions={executed}/{options.actions}; "
               f"skipped={skipped}; deterministic=True; allow_navigation={allow_navigation}"),
    )
