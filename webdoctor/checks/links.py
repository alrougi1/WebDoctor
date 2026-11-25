"""Broken link & resource checker: crawls <a> and <img> references concurrently."""
from __future__ import annotations

import shlex
import time
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple
from urllib.parse import urlparse, urljoin

from webdoctor.models import CheckResult, CheckStatus, Issue, Severity
from webdoctor.config import (
    DEFAULT_USER_AGENT,
    MAX_LINKS_TO_CHECK,
    LINK_CHECK_CONCURRENCY,
)


def _collect_references(html: str, base_url: str) -> Tuple[List[Dict[str, str]], int]:
    soup = BeautifulSoup(html, "html.parser")
    refs: Dict[str, Dict[str, str]] = {}

    def add(raw_url: str, tag: str, text: str):
        if not raw_url:
            return
        raw_url = raw_url.strip()
        if raw_url.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "about:", "sms:")):
            return
        abs_url = urljoin(base_url, raw_url)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            return
        if abs_url not in refs:
            refs[abs_url] = {"url": abs_url, "tag": tag, "text": text[:60].strip()}

    for a in soup.find_all("a", href=True):
        add(a.get("href", ""), "a", a.get_text())

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        add(src, "img", img.get("alt", "") or os_basename(src))

    for link in soup.find_all("link", rel=True, href=True):
        rel = "".join(link.get("rel", []))
        if any(x in rel for x in ("stylesheet", "icon", "preload", "canonical")):
            add(link.get("href", ""), f"link[{rel}]", "")

    all_refs = list(refs.values())
    return all_refs[:MAX_LINKS_TO_CHECK], len(all_refs)


def os_basename(u: str) -> str:
    path = urlparse(u).path
    return path.rstrip("/").split("/")[-1] if path else u[:40]


def _check_one(ref: Dict[str, str], verify: bool = True) -> Dict[str, Any]:
    url = ref["url"]
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    res: Dict[str, Any] = dict(ref)
    try:
        t0 = time.perf_counter()
        r = requests.head(url, headers=headers, timeout=12, allow_redirects=True, verify=verify)
        if r.status_code >= 400:
            # Many servers answer HEAD with 403/404 even though GET works. Only
            # classify the link as broken after the GET fallback also fails.
            r = requests.get(url, headers=headers, timeout=12, allow_redirects=True,
                             verify=verify, stream=True)
            r.close()
        res["status"] = r.status_code
        res["final_url"] = r.url
        res["time_ms"] = int((time.perf_counter() - t0) * 1000)
        res["ok"] = r.status_code < 400
        res["error"] = None
    except requests.exceptions.SSLError as e:
        res["status"] = 0
        res["ok"] = False
        res["error"] = f"SSL error: {e}"
        res["time_ms"] = 0
    except requests.exceptions.Timeout:
        res["status"] = 0
        res["ok"] = False
        res["error"] = "timed out"
        res["time_ms"] = 0
    except requests.exceptions.ConnectionError:
        res["status"] = 0
        res["ok"] = False
        res["error"] = "connection refused / DNS failed"
        res["time_ms"] = 0
    except Exception as e:
        res["status"] = 0
        res["ok"] = False
        res["error"] = str(e)
        res["time_ms"] = 0
    return res


def run_links_check(url: str, html: str, verify: bool = True) -> CheckResult:
    start = time.perf_counter()
    refs, total_refs = _collect_references(html, url)
    results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=LINK_CHECK_CONCURRENCY) as ex:
        futs = [ex.submit(_check_one, r, verify) for r in refs]
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                pass

    issues: List[Issue] = []
    broken = [r for r in results if not r.get("ok")]

    for k, r in enumerate(broken[:15]):
        status = r.get("status", 0)
        err = r.get("error")
        is_server_error = status >= 500
        is_client_error = 400 <= status < 500
        sev = Severity.ERROR if (is_server_error or status == 404) else Severity.WARNING
        ref_kind = r.get("tag", "link")
        link_text = f" ('{r['text']}')" if r.get("text") else ""
        issues.append(Issue(
            id=f"LINK_BROKEN_{k+1}",
            category="links",
            severity=sev,
            title=f"Broken {ref_kind.upper()} Reference: {r['url'][:80]}",
            summary=f"The link{link_text} on this page returns {f'HTTP {status}' if status else err}.",
            detail=f"Source tag: <{ref_kind}> | Link text: '{r.get('text','')}'\nTarget: {r['url']}\nResult: status={status}, error={err}",
            how_it_happened=_explain_link_failure(status, err, r["url"]),
            reproduction=f"curl -sI -o /dev/null -w 'HTTP %{{http_code}}\\n' {shlex.quote(r['url'])}",
            fix=_fix_link(status, r["url"], ref_kind),
        ))

    checked = len(results)
    broken_count = len(broken)
    metrics = {
        "links_checked": checked,
        "links_found": total_refs,
        "links_truncated": total_refs > checked,
        "broken_count": broken_count,
        "broken_percent": round(100 * broken_count / max(checked, 1), 1),
    }

    if broken_count >= 5 or any(i.severity == Severity.ERROR for i in issues):
        status = CheckStatus.FAILED
    elif broken_count > 0:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASSED

    return CheckResult(
        name="Broken Links & Resources",
        category="links",
        status=status,
        duration_ms=int((time.perf_counter() - start) * 1000),
        issues=issues,
        metrics=metrics,
    )


def _explain_link_failure(status: int, err: str | None, url: str) -> str:
    if status == 404:
        return f"The page links to `{url}` but that target no longer exists (HTTP 404). This commonly happens when content is moved/renamed without redirects, after a CMS migration, or due to a typo in the href."
    if status == 410:
        return "The target resource has been intentionally removed; the link was left pointing at a permanently deleted page."
    if status == 500:
        return "The linked destination's server crashed or hit an internal error while fulfilling the request."
    if status == 502 or status == 504:
        return "The linked destination's gateway/proxy timed out or could not reach its upstream app server."
    if status == 403:
        return "The linked server actively forbidden access — check hotlink protection or auth requirements."
    if err == "timed out":
        return "The linked server did not answer within the timeout; it may be down, under heavy load, or behind a slow connection."
    if "SSL" in (err or ""):
        return "The linked site has an invalid, expired, or self-signed TLS certificate; browsers will show a certificate warning if users click this."
    return f"The request to the linked target failed: {err or 'unknown error'}."


def _fix_link(status: int, url: str, kind: str) -> str:
    if status == 404:
        return f"Find the referencing <{kind}> in your templates/CMS and update the href to the new URL, or add a 301 redirect on the destination server."
    if status == 410:
        return "Remove the dead link from your content."
    if status in (500, 502, 503, 504):
        return "The issue is on the destination server; if you control it, check server logs. If third-party, consider replacing or removing the link."
    if status == 403:
        return "Verify whether that destination requires authentication or has hotlink/referrer protection that blocks automated requests."
    return "Verify the destination URL is correct and the server is healthy."
