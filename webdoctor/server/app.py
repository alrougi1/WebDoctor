"""Web dashboard for WebDoctor: run tests, watch live progress, pick AI providers,
and run authenticated tests logged in as a real user."""
from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from webdoctor.config import (
    TOOL_VERSION,
    MONKEY_DEFAULT_ACTIONS,
    MONKEY_DEFAULT_DELAY_MS,
    MONKEY_DEFAULT_SEED,
)
from webdoctor.models import TestReport, CheckStatus
from webdoctor.runner import run_diagnostics, validate_target_url
from webdoctor.reporters.html_report import generate_html
from webdoctor.reporters.markdown_report import generate_markdown
from webdoctor.reporters.json_report import generate_json
from webdoctor.reporters.pdf_report import generate_pdf
from webdoctor.diagnostics.ai_adapters import list_available_models
from webdoctor.checks.auth import perform_form_login
from webdoctor.checks.monkey import validate_monkey_options

log = logging.getLogger(__name__)
REPORT_DIR = os.path.abspath(os.environ.get("WEBDOCTOR_REPORTS", "./reports"))
os.makedirs(REPORT_DIR, exist_ok=True)

# The dashboard is a local-only tool. A per-process token is embedded in the
# rendered page and required on every API call; a custom header cannot be sent
# cross-origin without a CORS preflight, which this app never grants.
DASHBOARD_TOKEN = secrets.token_urlsafe(24)
TOKEN_HEADER = "X-WebDoctor-Token"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


def _host_from_header(value: str) -> str:
    """Extract a bare hostname from a Host header, including IPv6 literals."""
    value = (value or "").strip()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value[1:]
    return value.split(":", 1)[0]


def require_dashboard_token(request: Request) -> None:
    supplied = request.headers.get(TOKEN_HEADER, "")
    if not hmac.compare_digest(supplied, DASHBOARD_TOKEN):
        raise HTTPException(status_code=401, detail="Dashboard token missing or invalid")


app = FastAPI(title="WebDoctor Dashboard", version=TOOL_VERSION)


@app.middleware("http")
async def _local_only_and_harden(request: Request, call_next):
    # Reject DNS-rebinding attempts: an attacker domain pointing at 127.0.0.1
    # still sends its own Host header, which is never loopback.
    if _host_from_header(request.headers.get("host", "")) not in _ALLOWED_HOSTS:
        return JSONResponse(
            {"detail": "Dashboard is available only on loopback (127.0.0.1)."},
            status_code=400,
        )
    response = await call_next(request)
    # Downloaded reports have their own inline content; only the dashboard UI,
    # static assets and JSON APIs receive the strict policy.
    if not request.url.path.startswith("/api/reports/"):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = _DASHBOARD_CSP
    return response

PACKAGE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

# Starlette changed TemplateResponse's signature: newer releases take the
# request first. Support both so the declared `fastapi>=0.110` range works.
_TEMPLATE_RESPONSE_PARAMS = inspect.signature(templates.TemplateResponse).parameters


def _render_dashboard(request: Request):
    if "request" in _TEMPLATE_RESPONSE_PARAMS:
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"tool_version": TOOL_VERSION, "dashboard_token": DASHBOARD_TOKEN},
        )
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "tool_version": TOOL_VERSION, "dashboard_token": DASHBOARD_TOKEN},
    )


jobs: Dict[str, Dict[str, Any]] = {}
MAX_JOBS = 100
JOB_TTL_SECONDS = 3600
_jobs_lock = threading.RLock()
_TERMINAL = ("completed", "failed")


def _prune_jobs_locked(now: float) -> None:
    """Remove expired finished jobs. Caller must hold _jobs_lock."""
    expired = [
        jid for jid, j in jobs.items()
        if j.get("status") in _TERMINAL and now - j.get("created", now) > JOB_TTL_SECONDS
    ]
    for jid in expired:
        jobs.pop(jid, None)


def _reserve_job_slot(now: float) -> bool:
    """Prune old jobs and make room for one new job. Never evict a running job."""
    with _jobs_lock:
        _prune_jobs_locked(now)
        if len(jobs) < MAX_JOBS:
            return True
        finished = sorted(
            (jid for jid, j in jobs.items() if j.get("status") in _TERMINAL),
            key=lambda jid: jobs[jid].get("created", 0),
        )
        if not finished:
            return False
        jobs.pop(finished[0])
        return True


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        return jobs.get(job_id)


def _update_job(job_id: str, **updates) -> None:
    """Safely update a job from a worker thread (it may have been pruned)."""
    with _jobs_lock:
        job = jobs.get(job_id)
        if job is not None:
            job.update(updates)


class DiagnoseRequest(BaseModel):
    url: str
    browser: bool = True
    links: bool = True
    ai: bool = True
    ai_provider: str = "sandbox"
    ai_model: Optional[str] = None
    ai_key: Optional[str] = None
    ai_base: Optional[str] = None
    ai_compare: bool = False
    timeout: int = 20
    pdf: bool = True
    # Authenticated testing
    login: bool = False
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    user_field: Optional[str] = None
    pass_field: Optional[str] = None
    submit_btn: Optional[str] = None
    login_success_selector: Optional[str] = None
    session_json: Optional[str] = None
    insecure: bool = False
    # Deterministic hard monkey test
    monkey: bool = False
    monkey_seed: int = MONKEY_DEFAULT_SEED
    monkey_actions: int = MONKEY_DEFAULT_ACTIONS
    monkey_delay_ms: int = MONKEY_DEFAULT_DELAY_MS
    monkey_navigation: bool = True


class LoginTestRequest(BaseModel):
    login_url: str
    username: str
    password: str
    user_field: Optional[str] = None
    pass_field: Optional[str] = None
    submit_btn: Optional[str] = None
    login_success_selector: Optional[str] = None


def _monkey_summary(report: TestReport, req: DiagnoseRequest) -> Dict[str, Any]:
    """Small, secret-free monkey summary for dashboard progress responses."""
    result = next((cr for cr in report.check_results if cr.category == "monkey"), None)
    metrics = (result.metrics or {}) if result else {}
    return {
        "enabled": bool(req.monkey),
        "status": result.status.value if result else "not_run",
        "seed": metrics.get("seed", req.monkey_seed),
        "actions_executed": metrics.get("actions_executed", 0),
        "actions_skipped": metrics.get("actions_skipped", 0),
        "planned_actions": metrics.get("planned_actions", req.monkey_actions),
        "skipped_reason": metrics.get("skipped_reason"),
        "js_error_count": metrics.get("js_error_count", 0),
        "console_error_count": metrics.get("console_error_count", 0),
        "page_crash_count": metrics.get("page_crash_count", 0),
        "off_origin_blocked": len(metrics.get("off_origin_navigation_attempts") or []),
    }


def _sanitize_request(req: DiagnoseRequest) -> Dict[str, Any]:
    """Return only non-sensitive configuration for progress/job inspection."""
    return {
        "url": req.url,
        "browser": req.browser,
        "links": req.links,
        "ai": req.ai,
        "ai_provider": req.ai_provider,
        "ai_model": req.ai_model,
        "ai_compare": req.ai_compare,
        "timeout": req.timeout,
        "pdf": req.pdf,
        "login": req.login,
        "insecure": req.insecure,
        "monkey": req.monkey,
        "monkey_seed": req.monkey_seed,
        "monkey_actions": req.monkey_actions,
        "monkey_delay_ms": req.monkey_delay_ms,
        "monkey_navigation": req.monkey_navigation,
    }


def _background_run(job_id: str, req: DiagnoseRequest):
    if _get_job(job_id) is None:
        return

    def _cb(msg: str, pct: float):
        _update_job(job_id, percent=int(pct * 100), message=msg)

    try:
        report_out = os.path.join(REPORT_DIR, job_id)
        os.makedirs(report_out, exist_ok=True)
        shots = os.path.join(report_out, "screenshots")

        session_path = None
        if req.session_json:
            try:
                state = json.loads(req.session_json)
                session_path = os.path.join(report_out, "storage_state.json")
                with open(session_path, "w") as f:
                    json.dump(state, f)
            except Exception as e:
                _update_job(job_id, message=f"Invalid session JSON: {e}")

        compare_list = None
        if req.ai_compare:
            compare_list = [
                {"provider": "sandbox", "model": "gpt-6-luna", "label": "GPT-6 Luna (OpenAI)"},
                {"provider": "sandbox", "model": "gemini-3.8-flash", "label": "Gemini 3.8 Flash (Google)"},
            ]

        report = run_diagnostics(
            req.url,
            browser_mode=req.browser,
            check_links=req.links,
            ai=req.ai,
            ai_provider=req.ai_provider,
            ai_model=req.ai_model,
            ai_key=req.ai_key,
            # Arbitrary custom AI endpoints are intentionally not accepted from
            # the dashboard; the CLI remains the place for that advanced option.
            ai_base=None,
            ai_compare=compare_list,
            timeout=req.timeout,
            screenshot_dir=shots,
            report_dir=report_out,
            login=req.login,
            login_url=req.login_url or req.url,
            username=req.username,
            password=req.password,
            user_field=req.user_field,
            pass_field=req.pass_field,
            submit_btn=req.submit_btn,
            login_success_selector=req.login_success_selector,
            session_path=session_path,
            insecure=req.insecure,
            monkey=req.monkey,
            monkey_seed=req.monkey_seed,
            monkey_actions=req.monkey_actions,
            monkey_delay_ms=req.monkey_delay_ms,
            monkey_navigation=req.monkey_navigation,
            progress=_cb,
        )

        base = os.path.join(report_out, "report")
        html_path = base + ".html"
        md_path = base + ".md"
        json_path = base + ".json"
        pdf_path = base + ".pdf"

        # Each format is written independently, matching the CLI behaviour.
        write_errors = []
        for label, path, render in (
            ("HTML", html_path, lambda: generate_html(report)),
            ("Markdown", md_path, lambda: generate_markdown(report)),
            ("JSON", json_path, lambda: generate_json(report)),
        ):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(render())
            except Exception as e:
                log.warning("Failed to write %s report for job %s: %s", label, job_id, e)
                write_errors.append(label)
        if len(write_errors) == 3:
            raise RuntimeError(f"Could not write any report formats: {', '.join(write_errors)}")

        pdf_ok = False
        if req.pdf:
            try:
                pdf_ok = generate_pdf(report, pdf_path)
            except Exception as e:
                log.warning("Failed to write PDF report for job %s: %s", job_id, e)
            if not pdf_ok:
                write_errors.append("PDF")

        message = "Report ready"
        if write_errors:
            message += " (skipped: " + ", ".join(write_errors) + ")"

        _update_job(
            job_id,
            status="completed",
            message=message,
            percent=100,
            report={
                "overall_grade": report.overall_grade,
                "overall_status": report.overall_status.value,
                "score": report.score,
                "url": report.final_url or report.url,
                "target_title": report.target_title,
                "issue_counts": report.issue_counts(),
                "environment_issue_count": len(getattr(report, "environment_issues", None) or []),
                "duration_ms": report.duration_ms,
                "issues": [i.to_dict() for i in report.issues],
                "ai_summary": report.ai_summary,
                "metrics": report.metrics,
                "pdf_available": pdf_ok,
                "logged_in": bool(req.login or req.session_json),
                "monkey_summary": _monkey_summary(report, req),
            },
        )
    except Exception as e:
        log.exception("Diagnosis job %s failed", job_id)
        _update_job(job_id, status="failed", message=str(e))


@app.get("/api/models", dependencies=[Depends(require_dashboard_token)])
def api_models():
    return {"models": list_available_models()}


@app.post("/api/diagnose", dependencies=[Depends(require_dashboard_token)])
async def api_diagnose(req: DiagnoseRequest):
    try:
        validate_target_url(req.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.ai_base:
        raise HTTPException(400, "Custom AI base URLs are not allowed via the dashboard.")
    try:
        validate_monkey_options(req.monkey_seed, req.monkey_actions, req.monkey_delay_ms)
    except ValueError as e:
        raise HTTPException(400, str(e))

    now = time.monotonic()
    if not _reserve_job_slot(now):
        raise HTTPException(429, "Too many running diagnosis jobs; try again later")
    jid = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    with _jobs_lock:
        jobs[jid] = {
            "status": "running",
            "percent": 0,
            "message": "Queued",
            "config": _sanitize_request(req),
            "created": now,
        }

    future = loop.run_in_executor(None, _background_run, jid, req)

    def _on_done(done):
        try:
            done.result()
        except Exception as e:
            log.exception("Diagnosis worker for job %s crashed", jid)
            job = _get_job(jid)
            if job and job.get("status") == "running":
                _update_job(jid, status="failed", message=f"Worker crashed: {e}")

    future.add_done_callback(_on_done)
    return {"job_id": jid}


@app.get("/api/progress/{job_id}", dependencies=[Depends(require_dashboard_token)])
def api_progress(job_id: str):
    j = _get_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    # Never return the internal job dict: it used to expose passwords, API keys,
    # and pasted session JSON supplied by the user.
    return {
        "status": j.get("status"),
        "percent": j.get("percent", 0),
        "message": j.get("message", ""),
        "created": j.get("created"),
        "report": j.get("report"),
    }


@app.post("/api/login-test", dependencies=[Depends(require_dashboard_token)])
def api_login_test(req: LoginTestRequest):
    try:
        validate_target_url(req.login_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ok, res = perform_form_login(
        login_url=req.login_url,
        username=req.username,
        password=req.password,
        user_field=req.user_field,
        pass_field=req.pass_field,
        submit_btn=req.submit_btn,
        login_success_selector=req.login_success_selector,
    )
    if ok:
        cookies = res.get("cookies", [])
        return {"ok": True, "final_url": res.get("final_url"), "cookie_count": len(cookies),
                "message": f"Login succeeded -- redirected to {res.get('final_url')} ({len(cookies)} cookies stored)."}
    return {"ok": False, "error": res.get("error", "Login failed")}


@app.get("/api/reports/{job_id}/{fmt}", dependencies=[Depends(require_dashboard_token)])
def api_report_file(job_id: str, fmt: str):
    mapping = {"html": ("report.html", "text/html"), "md": ("report.md", "text/markdown"),
               "json": ("report.json", "application/json"), "pdf": ("report.pdf", "application/pdf")}
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise HTTPException(400, "invalid job id")
    if fmt not in mapping:
        raise HTTPException(400, "unknown format")
    fname, ctype = mapping[fmt]
    path = os.path.join(REPORT_DIR, job_id, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "report file not found")
    return FileResponse(path, media_type=ctype, filename=f"webdoctor-{job_id}.{fmt}")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render_dashboard(request)
