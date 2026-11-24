"""HTTP & network health checks: status, response time, SSL, redirects, compression."""
from __future__ import annotations

import time
import requests
from urllib.parse import urlparse
from typing import Optional, List

from webdoctor.models import CheckResult, CheckStatus, Issue, Severity
from webdoctor.config import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT


def run_http_check(url: str, timeout: int = DEFAULT_TIMEOUT, verify: bool = True) -> CheckResult:
    issues: List[Issue] = []
    metrics = {}
    start = time.perf_counter()

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }

    session = requests.Session()
    session.max_redirects = 10

    resp = None
    try:
        t0 = time.perf_counter()
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True, verify=verify)
        response_time_ms = int((time.perf_counter() - t0) * 1000)
    except requests.exceptions.SSLError as e:
        response_time_ms = int((time.perf_counter() - start) * 1000)
        issues.append(Issue(
            id="HTTP_SSL_ERROR",
            category="http",
            severity=Severity.CRITICAL,
            title="SSL/TLS Certificate Error",
            summary=f"The site's SSL certificate could not be validated: {e}",
            detail=str(e),
            how_it_happened="The browser or HTTP client refused the HTTPS connection because the certificate is expired, self-signed, invalid for this domain, or issued by an untrusted Certificate Authority. As a result, users will see a prominent browser security warning and modern browsers may block access entirely.",
            reproduction=f"curl -Iv '{url}'",
            fix="Renew the certificate or issue a new valid certificate (e.g., via Let's Encrypt / certbot). Ensure the certificate's Common Name (CN) and Subject Alternative Names (SAN) match the exact hostname, including both www and non-www variants if both are used.",
        ))
        return CheckResult(name="HTTP & Network Health", category="http", status=CheckStatus.FAILED,
                           duration_ms=response_time_ms, issues=issues, metrics={"url": url})
    except requests.exceptions.ConnectionError as e:
        response_time_ms = int((time.perf_counter() - start) * 1000)
        issues.append(Issue(
            id="HTTP_CONNECTION_ERROR",
            category="http",
            severity=Severity.CRITICAL,
            title="Connection Failed",
            summary=f"Could not connect to {url}: {e}",
            detail=str(e),
            how_it_happened="The connection failed at the TCP/DNS level. Common causes: the DNS domain does not resolve, the server port is closed/unreachable, the web server (nginx/apache/caddy) is not running, or a firewall/security group is dropping traffic.",
            reproduction=f"curl -Iv '{url}'",
            fix="Verify DNS records point to the correct IP (`dig +short hostname`), confirm the web server is running (`systemctl status nginx`), and check firewall / security-group inbound rules for ports 80 and 443.",
        ))
        return CheckResult(name="HTTP & Network Health", category="http", status=CheckStatus.FAILED,
                           duration_ms=response_time_ms, issues=issues, metrics={"url": url})
    except requests.exceptions.Timeout as e:
        response_time_ms = int((time.perf_counter() - start) * 1000)
        issues.append(Issue(
            id="HTTP_TIMEOUT",
            category="http",
            severity=Severity.CRITICAL,
            title=f"Request Timed Out (>{timeout}s)",
            summary=f"The server took longer than {timeout} seconds to respond.",
            detail=str(e),
            how_it_happened="The HTTP client sent the request but received no response headers within the allowed time window. This usually indicates the application server is overwhelmed, deadlocked on a database query, or stuck waiting for a downstream service.",
            reproduction=f"curl -m {timeout} -w '\\nTime: %{{time_total}}s\\n' -o /dev/null -s '{url}'",
            fix="Profile slow server-side endpoints, optimize database queries, add server-side caching (Redis, CDN), and configure proper application timeouts.",
        ))
        return CheckResult(name="HTTP & Network Health", category="http", status=CheckStatus.FAILED,
                           duration_ms=response_time_ms, issues=issues, metrics={"url": url, "timeout_s": timeout})
    except requests.exceptions.TooManyRedirects as e:
        response_time_ms = int((time.perf_counter() - start) * 1000)
        issues.append(Issue(
            id="HTTP_REDIRECT_LOOP",
            category="http",
            severity=Severity.CRITICAL,
            title="Too Many Redirects (Redirect Loop)",
            summary="The URL repeatedly redirects without reaching a final destination.",
            detail=str(e),
            how_it_happened="The client followed successive HTTP 3xx responses until the limit was exceeded. This typically happens when http->https and https->http rules conflict, or when authentication redirects bounce back and forth.",
            reproduction=f"curl -sIL '{url}' | grep -iE 'HTTP/|location:'",
            fix="Inspect redirect rules in your reverse proxy (nginx/cloudflare) and application middleware. Remove conflicting rules so the URL resolves to exactly one canonical destination in 1–2 hops.",
        ))
        return CheckResult(name="HTTP & Network Health", category="http", status=CheckStatus.FAILED,
                           duration_ms=response_time_ms, issues=issues, metrics={"url": url})

    status_code = resp.status_code
    metrics = {
        "status_code": status_code,
        "response_time_ms": response_time_ms,
        "final_url": resp.url,
        "redirect_count": len(resp.history),
        "content_length": len(resp.content),
        "content_type": resp.headers.get("Content-Type", ""),
        "encoding": resp.encoding,
        "http_version": getattr(resp.raw, "version", None),
    }

    # Status code analysis
    if status_code >= 500:
        issues.append(Issue(
            id="HTTP_5XX",
            category="http",
            severity=Severity.CRITICAL,
            title=f"Server Error {status_code}",
            summary=f"The server returned an internal error response (HTTP {status_code}).",
            detail=f"Status: {status_code} ({resp.reason}). Final URL: {resp.url}.",
            how_it_happened=_explain_5xx(status_code),
            reproduction=f"curl -i '{url}'",
            fix=_fix_5xx(status_code),
            evidence=(resp.text[:600] if "text/html" in resp.headers.get("Content-Type", "") else None),
        ))
    elif status_code >= 400:
        issues.append(Issue(
            id="HTTP_4XX",
            category="http",
            severity=Severity.ERROR,
            title=f"Client Error {status_code} ({resp.reason})",
            summary=f"The URL returned an error response (HTTP {status_code}).",
            detail=f"Status: {status_code} ({resp.reason}). Final URL: {resp.url}.",
            how_it_happened=_explain_4xx(status_code),
            reproduction=f"curl -i '{url}'",
            fix=_fix_4xx(status_code),
        ))

    # Response time
    if response_time_ms > 3000:
        issues.append(Issue(
            id="HTTP_SLOW_RESPONSE",
            category="http",
            severity=Severity.WARNING if response_time_ms < 6000 else Severity.ERROR,
            title=f"Slow Server Response Time ({response_time_ms} ms)",
            summary=f"The origin took {response_time_ms} ms to return the first response.",
            detail=f"Response time: {response_time_ms} ms (recommended: < 600 ms).",
            how_it_happened="The delay occurs between the client completing its request and the server fully returning the response. This includes server think time, database lookups, rendering, and content transfer. A slow origin severely impacts Core Web Vitals (TTFB).",
            reproduction=f"curl -w 'TTFB: %{{time_starttransfer}}s | Total: %{{time_total}}s\\n' -o /dev/null -s '{url}'",
            fix="Enable server-side page caching or a CDN (Cloudflare, Fastly), optimize database queries and ORM calls, implement gzip/brotli compression, and reduce payload size.",
        ))

    # Redirect count
    if len(resp.history) > 3:
        chain = " -> ".join([r.url for r in resp.history] + [resp.url])
        issues.append(Issue(
            id="HTTP_EXCESSIVE_REDIRECTS",
            category="http",
            severity=Severity.WARNING,
            title=f"Excessive Redirect Chain ({len(resp.history)} hops)",
            summary="The URL passes through more than 3 redirect hops before the final page.",
            detail=f"Chain: {chain}",
            how_it_happened="Each HTTP redirect forces the client to negotiate a new connection and request cycle, compounding latency. Chained redirects are usually left behind after site migrations or inconsistent internal linking.",
            reproduction=f"curl -sIL '{url}' | grep -iE 'HTTP/|location:'",
            fix="Consolidate redirects into a single direct rule (A -> C instead of A -> B -> C) and update internal links to point directly to the final canonical URL.",
        ))

    # Compression check for text/html
    ct = resp.headers.get("Content-Type", "").lower()
    ce = resp.headers.get("Content-Encoding", "").lower()
    if ("text/html" in ct or "javascript" in ct or "json" in ct or "xml" in ct) and ce not in ("gzip", "br", "deflate") and len(resp.content) > 1024:
        issues.append(Issue(
            id="HTTP_NO_COMPRESSION",
            category="http",
            severity=Severity.WARNING,
            title="Text Responses Not Compressed (gzip/brotli missing)",
            summary="The response body was served without Content-Encoding compression.",
            detail=f"Content-Type: {ct}, Content-Encoding: {ce or 'none'}, body size: {len(resp.content)} bytes.",
            how_it_happened="The origin web server did not apply gzip or Brotli compression to compressible text content, causing unnecessarily large wire transfer sizes and slower load times on mobile connections.",
            reproduction=f"curl -sI -H 'Accept-Encoding: gzip, br' '{url}' | grep -i content-encoding",
            fix="Enable gzip/brotli in your web server. In nginx: `gzip on; gzip_types text/plain text/css application/json application/javascript text/xml;` — or let your CDN handle compression at the edge.",
        ))

    # HTTP/1.1 vs HTTP/2
    proto = resp.raw.version if hasattr(resp.raw, "version") else None
    if proto and proto < 20:
        issues.append(Issue(
            id="HTTP_LEGACY_PROTOCOL",
            category="http",
            severity=Severity.INFO,
            title="Site Served Over HTTP/1.1",
            summary="HTTP/2 or HTTP/3 multiplexing is not enabled on this connection.",
            detail=f"Protocol detected: HTTP/{proto/10 if proto >= 10 else proto}.",
            how_it_happened="HTTP/1.1 requires a separate TCP connection per resource, which increases latency from head-of-line blocking compared to the single multiplexed connection of HTTP/2.",
            reproduction=f"curl -sI --http2 '{url}' | head -5",
            fix="Enable HTTP/2 in your web server config (e.g., `listen 443 ssl http2;` in nginx) or terminate traffic at a modern CDN that supports HTTP/2 and HTTP/3.",
        ))

    has_crit = any(i.severity == Severity.CRITICAL for i in issues)
    has_err = any(i.severity == Severity.ERROR for i in issues)
    has_warn = any(i.severity == Severity.WARNING for i in issues)
    status = CheckStatus.FAILED if (has_crit or has_err) else (CheckStatus.WARN if has_warn else CheckStatus.PASSED)

    return CheckResult(
        name="HTTP & Network Health",
        category="http",
        status=status,
        duration_ms=response_time_ms,
        issues=issues,
        metrics=metrics,
    )


def _explain_5xx(code: int) -> str:
    explanations = {
        500: "The server encountered an unhandled exception or crash in application code, a fatal database error, or a misconfiguration that prevented completing the request. The server could not return a valid response.",
        502: "The reverse proxy (nginx/Apache/Cloudflare) received an invalid, malformed, or empty response from the upstream application server. The upstream likely crashed, is not listening, or timed out before headers arrived.",
        503: "The server is temporarily unable to handle the request — typically due to overload, maintenance mode, or rate limiting. All worker threads may be saturated.",
        504: "The reverse proxy timed out waiting for the upstream application server to respond. The upstream was either too slow or completely hung.",
    }
    return explanations.get(code, f"The server failed to fulfill an apparently valid request with status {code}.")


def _fix_5xx(code: int) -> str:
    fixes = {
        500: "Check application error logs and exception trackers (Sentry/Datadog) for the unhandled exception stack trace. Verify database connectivity, environment variables, and filesystem permissions.",
        502: "Verify that the upstream application service is running (`systemctl status <app>` or `docker ps`), listening on the expected port/socket, and not failing silently during startup. Check proxy error logs for the upstream connect failure reason.",
        503: "Scale the application instances, increase worker concurrency, review queue lengths, and check whether a maintenance mode banner was left enabled.",
        504: "Identify slow database queries or slow external API calls, increase proxy read timeouts where justified, and add caching or background-job processing for slow operations.",
    }
    return fixes.get(code, "Inspect server application logs for the corresponding timestamp to identify the failing handler.")


def _explain_4xx(code: int) -> str:
    explanations = {
        400: "The server could not understand the request due to malformed syntax, invalid query parameters, or an unsupported HTTP method.",
        401: "The resource requires authentication and either no credentials were provided or they were invalid.",
        403: "The server understood the request but refuses to authorize it — file permission issues, IP allowlist blocks, bot protection, or CORS preflight failures.",
        404: "No resource was found at this URL. The route may not be registered, the file is missing, or the URL contains a typo.",
        405: "The HTTP method used is not allowed on this endpoint (e.g., GET on a POST-only webhook).",
        410: "The resource existed previously but has been intentionally and permanently removed.",
        429: "The client sent too many requests in a given amount of time and hit rate limits.",
    }
    return explanations.get(code, f"The server rejected the client request with status {code}.")


def _fix_4xx(code: int) -> str:
    fixes = {
        400: "Verify request headers, query parameters, and encoding match what the endpoint expects.",
        401: "Ensure authorization headers (Bearer token, basic auth, cookies) are being attached correctly.",
        403: "Check web-root file permissions, WAF rules, IP allowlists, and that your server configuration grants access to the requested path.",
        404: "Verify routing configuration, filename casing, and ensure static assets are placed in the expected public folder.",
        405: "Allow the required method in your route definition or router configuration.",
        429: "Review rate-limit thresholds; whitelist trusted IPs if this was a known automated caller.",
    }
    return fixes.get(code, "Consult endpoint documentation and server logs for more context.")
