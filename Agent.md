# WebDoctor Agent Playbook & Whole-Project Plan

_Living document. Update it whenever project behavior, verification gates, or plans change. Use `[x]` for completed steps and `[ ]` for planned work. Never remove completed history; append corrections instead._

## 0. Golden rules for every agent

- **Accuracy first.** A finding must describe the target site, not a tester/browser/network hiccup.
- `score = 100` means **zero scored mistakes**. Runner/environment failures must never lower the grade.
- Use only owned/staging targets or targets with explicit written authorization.
- **No active third-party scans** without written permission or bug-bounty scope. `openbsd.org` remains blocked pending that.
- Never commit API keys, passwords, sessions, `storage_state.json`, generated reports, screenshots, or PDFs.
- Never commit or push unless the user explicitly asks in that turn.
- Keep the dashboard bound to `127.0.0.1` unless explicitly authorized and secured.
- Every real change must be appended to `last changes` with files, reason, exact verification commands, and PASS/FAIL.
- Keep `README.md`, tests, and `last changes` in sync with code.
- Prefer `edit` over overwriting unfamiliar files. Generated/ignored artifacts are not source of truth.

## 1. Project snapshot — 2026-11-26

| Item | State |
|---|---|
| Repository | `D:\my Projects\WebDoctor` |
| Remote | `https://github.com/alrougi1/WebDoctor.git` |
| Current branch | `improvement/accuracy-pass` at `cefd0ae` |
| Origin main | `08505b5` — PR #1 merge; current branch content equals `origin/main` |
| Local `main` | `6c98700` — stale; branch new work from `origin/main`, not local `main` |
| Tests | **100 collected**: 22 legacy + 78 focused, including 15 monkey tests |
| Version | `1.0.0` from `webdoctor/version.py` |
| Python support | `>=3.9`; CI uses 3.11; local runs often use 3.13 |
| Dashboard | FastAPI + Jinja2 + static CSS/JS/SVG |
| Reports | HTML, Markdown, JSON, PDF; plus `storage_state.json` session artifact |
| `Agent.md` | Now populated with this living playbook |

## 2. Architecture inventory

```text
WebDoctor/
├── webdoctor/
│   ├── cli.py                 # check, models, login, serve, demo
│   ├── config.py              # timeouts, user-agent, AI defaults
│   ├── models.py              # Issue, CheckResult, TestReport, scoring
│   ├── runner.py              # orchestration + URL validation
│   ├── version.py
│   ├── checks/
│   │   ├── http_check.py      # status, total response time, SSL, redirects, redaction
│   │   ├── security.py        # HSTS/CSP/XFO/nosniff/referrer/cookies
│   │   ├── seo_a11y.py        # title, meta, viewport, lang, alt, labels
│   │   ├── links.py           # 40-link cap, HEAD + GET confirmation
│   │   ├── browser.py         # Playwright runtime, console, perf, screenshots
│   │   ├── auth.py            # form login, session capture, cookie audit
│   │   └── monkey.py          # seeded chaos, safety guards, trace
│   ├── diagnostics/
│   │   ├── ai_adapters.py     # sandbox/OpenAI/Anthropic/Gemini catalog
│   │   ├── ai_engine.py       # single-model + side-by-side comparison
│   │   └── rule_engine.py     # offline root-cause summary
│   ├── reporters/
│   │   ├── html_report.py     # standalone dark-crimson report + inline emblem
│   │   ├── markdown_report.py
│   │   ├── json_report.py
│   │   └── pdf_report.py      # WeasyPrint first, Chromium fallback
│   └── server/
│       ├── app.py             # FastAPI dashboard/jobs/exports
│       ├── templates/dashboard.html
│       └── static/
│           ├── dashboard.css
│           ├── dashboard.js
│           └── webdoctor-emblem.svg
├── tests/                     # 15 test files, 82 collected tests
├── examples/                  # GitHub Action, SDK and pytest examples
├── .github/workflows/
│   └── webdoctor-selfcheck.yml
├── webapp_static/             # pipeline + dashboard images, pipeline.mmd
├── README.md
├── SECURITY.md
├── VSCODE_SETUP.md
├── last changes
└── pyproject.toml
```

### Entry points

| Command | Purpose |
|---|---|
| `webdoctor check <url>` | Full diagnostic suite; exit `0` for PASSED/WARN, `1` for FAILED |
| `webdoctor models` | Authoritative AI model catalog and defaults |
| `webdoctor login <url>` | Headful login for 2FA/SSO; saves Playwright `storageState` |
| `webdoctor serve` | Local dashboard; defaults to `127.0.0.1:8080` |
| `webdoctor demo` | Built-in broken site on loopback; exercises real CLI parser |

Python API: `from webdoctor import run_diagnostics, TestReport, Issue`.

## 3. Check suites

- **HTTP/SSL:** status codes, total response time, redirect chain, TLS, compression, HTTP/1.1 INFO. Evidence is redacted for secrets/JWTs/emails.
- **Security headers:** HSTS validity/`max-age`, CSP, XFO or CSP `frame-ancestors`, nosniff, Referrer-Policy, Permissions-Policy, cookie Secure/HttpOnly/SameSite.
- **SEO/a11y:** title, meta description, viewport, `lang`, image `alt`, headings, favicon, form/survey labels.
- **Links:** unique `<a>`/`<img>`/resource refs, 40-link cap, HEAD failure confirmed with GET.
- **Browser:** Playwright JS errors, console errors, failed resources, performance waterfall, screenshot, scoped overlay dismissal.
- **Auth:** form login, success proof, session capture/reuse, auth-cookie audit.
- **Monkey:** deterministic seeded planner; blocks submits, writes, downloads, destructive labels, unsafe schemes and off-origin navigation; dialogs dismissed; full trace recorded.

## 4. Diagnostics and scoring

```text
score = 100 - 30*critical - 15*errors(max 3/category) - 4*warnings(max 5/category)
```

- Critical issues are uncapped and force FAILED.
- INFO never penalized.
- Grades: A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40, else F.
- Root causes dedupe by `(cause category | normalized issue ID | target URL)`.
- All occurrences remain in `check_results`.
- `source="environment"` issues are visible but excluded from `issues`, scoring, and “all checks passed” claims.
- `score_breakdown` exposes raw/deduped counts, caps, penalties, duplicates collapsed, and `environment_issue_count`.

## 5. Reports and dashboard

- HTML report is standalone: dark crimson theme, inline original SVG emblem, embedded screenshots, sanitized AI markdown, escaped embedded JSON, runner-notes section, monkey trace section.
- Markdown report is a compact CI/PR summary.
- JSON report carries full `TestReport.to_dict()`, including `environment_issues`.
- PDF uses WeasyPrint first; native import/render failures fall back to Chromium. Byte determinism is guaranteed only for WeasyPrint.
- Dashboard routes: `/`, `/static/...`, `/api/models`, `/api/diagnose`, `/api/progress/{id}`, `/api/login-test`, `/api/reports/{id}/{fmt}`.
- `/api/progress` is a secret-free whitelist.
- Dashboard APIs require a per-process `X-WebDoctor-Token` injected as a meta tag; non-loopback `Host` headers are rejected (DNS-rebinding guard); CSP/nosniff/referrer headers apply to UI, static assets and APIs, but not downloaded reports.
- Report export buttons fetch report bytes and save through Blob + `download`; PDF is disabled with an explanation when unavailable.
- Use `WEBDOCTOR_REPORTS=<temp>` for smoke tests so no generated report touches the repo.

## 6. Definition of done

- [x] Behavior matches accuracy-first principle.
- [x] Security invariants preserved.
- [x] Relevant tests added/updated.
- [x] README/test counts updated.
- [x] `last changes` updated with files, reason, commands and result.
- [x] All verification gates pass.
- [x] `git status --short` reviewed; no secrets/sessions/reports/PDFs staged.
- [x] No commit/push except when explicitly requested.

## 7. Verification gates

Run from repo root with `.venv`:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tests\test_all.py
.venv\Scripts\python.exe -m compileall -q webdoctor tests examples
git diff --check
node --check webdoctor\server\static\dashboard.js
.venv\Scripts\webdoctor.exe models
.venv\Scripts\webdoctor.exe check https://example.com --no-ai --no-browser --no-links --ci
.venv\Scripts\webdoctor.exe demo --no-ai --report-dir <temp>
```

Also run the Python 3.9/3.10/3.11 grammar sweep:

```powershell
.venv\Scripts\python.exe -c "import ast, pathlib; versions=[(3,9),(3,10),(3,11)]; files=[p for root in ['webdoctor','tests','examples'] for p in pathlib.Path(root).rglob('*.py')]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p), feature_version=v) for v in versions for p in files]; print(f'AST parse OK: {len(files)} files x {len(versions)} versions')"
```

Dashboard smoke recipe:

1. Start with `$env:WEBDOCTOR_REPORTS='<temp>'`.
2. `webdoctor serve --host 127.0.0.1 --port <free-port>`.
3. Confirm `/`, CSS, JS, SVG emblem, `/api/models` all HTTP 200.
4. Submit a diagnosis with sentinel password/API key/session JSON.
5. Poll progress; assert no sentinel appears.
6. Download HTML/MD/JSON/PDF and verify non-empty files.
7. Confirm `reports/` does not exist in the repo and no PDF remains there.

Dashboard security smoke (separate from the functional recipe above): assert `/` embeds the token meta tag, `/api/models` returns 401 without/invalid token, a hostile `Host` returns 400, sentinel password/key/session never appear in progress, and report exports require the token.

Finish with `git status --short` and a full-tree secret scan (`storage_state.json`, generated reports, sentinel values) — not just added lines. `git diff --check` ignores untracked files; use `git add -N Agent.md` (intent-to-add) before relying on it for this file.

## 8. Completed plan — done steps

### 2026-11-24 — foundation and accuracy hardening `[x]`

- [x] Created branch `improvement/accuracy-pass` from `6c98700`.
- [x] Created `.venv` and installed dependencies plus Playwright Chromium.
- [x] Added `_sanitize_request()`; `/api/progress` returns whitelist only.
- [x] Added `validate_target_url()` SSRF/private-range protection and localhost-only serve default.
- [x] Hardened HTML reports: `bleach` AI markdown, escaped `reportData`, no inline JS copy handlers.
- [x] Unified TLS verification; `--insecure` opt-in.
- [x] Added `SEC_CHECK_SKIPPED`, login-success selector, HEAD retry, link truncation metrics.
- [x] Verified provider model catalog and defaults against official docs.
- [x] Added stable de-duplication fingerprints and capped scoring penalties.
- [x] Extracted dashboard into Jinja2 template + static CSS/JS.
- [x] Added job TTL/cap/thread safety and path-traversal-protected downloads.
- [x] Added CI workflow and OS-assigned test ports.

### 2026-11-24 — live testing and monkey suite `[x]`

- [x] Fixed false SEO/a11y findings on HTML fetch failure.
- [x] Ran hard tests on example.com, python.org, mozilla.org, expired.badssl.com, httpbin 500.
- [x] Added deterministic hard monkey suite with seed, trace, safety guards and dedicated report section.
- [x] Fixed Python 3.11 f-string syntax failure in HTML report JSON escaping.
- Commits: `edc9800`, `fad843d`.

### 2026-11-25 — PDF fallback, accuracy pass, theme `[x]`

- [x] Added WeasyPrint → Chromium PDF fallback.
- [x] Added `Issue.source="environment"` and `TestReport.environment_issues`.
- [x] Excluded runner failures from scoring and from “all checks passed” claims.
- [x] Added runner notes to rule summary, HTML, Markdown and dashboard.
- [x] Fixed 401/403 severity, total-response labeling, redirect chain, evidence redaction.
- [x] Fixed HSTS `max-age`, CSP `frame-ancestors`, and cookie SameSite checks.
- [x] Confirmed HEAD failures with GET before calling links broken.
- [x] Added dark crimson theme and original hand-built SVG emblem.
- [x] Corrected PDF warning to “no renderer produced a file”.

### 2026-11-25 — centered brand and export fix `[x]`

- [x] Centered and enlarged logo + WebDoctor wordmark for Google-like header.
- [x] Replaced invalid `<a><button>` export markup with real buttons.
- [x] Implemented Blob-based downloads with success/error status.
- [x] Disabled PDF button with explanation when PDF generation is unavailable.
- [x] Verified HTML/MD/JSON/PDF all download from live dashboard.
- Commit: `cefd0ae`.

### 2026-11-25 — PR merge `[x]`

- [x] PR #1 merged into `origin/main` as `08505b5`.
- [x] Current branch content matches `origin/main`.

### 2026-11-26 — Agent playbook `[x]`

- [x] Inspected full project inventory.
- [x] Confirmed 82 collected tests and docs/test-count consistency.
- [x] Identified stale docs, security limitations, and future backlog.
- [x] Wrote this `Agent.md` living playbook.

### 2026-11-26 — Dashboard security hardening `[x]`

- [x] Added a per-process `DASHBOARD_TOKEN`; every `/api/*` route now requires `X-WebDoctor-Token` via a constant-time dependency.
- [x] Added loopback-only `Host` middleware to block DNS-rebinding; non-loopback hosts get HTTP 400.
- [x] Added strict CSP, `nosniff`, and `no-referrer` headers for the UI/static/APIs; downloaded reports keep their own rendering rules.
- [x] Removed inline `onclick`/`onchange` handlers; JS reads the token from a meta tag through `apiFetch()`.
- [x] Hardened `validate_target_url()` with best-effort DNS resolution so hostnames pointing at private/link-local addresses are rejected; loopback remains allowed for local demos.
- [x] Added an authorization checklist to `SECURITY.md` and a full-tree secret-scan gate.
- [x] Added 9 regression tests (token enforcement, hostile hosts, hardening headers, inward DNS resolution); full suite now 91 passed.
- [x] Verified with compileall, `git diff --check`, `node --check`, AST grammar sweep, legacy suite, CLI smoke, a live loopback dashboard smoke with sentinel secrets, and a headless Chromium CSP/token check.

### 2026-11-26 — Full feature validation and demo fix `[x]`

- [x] Fixed `webdoctor demo` default port 8765 (WinError 10013 on this machine) to an OS-assigned loopback port; added a regression test.
- [x] Exercised CLI `check` against broken/healthy/authenticated local sites, `demo`, `models`, `--version`, custom `--out-*` paths, `--session` reuse, and `--ai-compare` fallback.
- [x] Verified SDK `run_diagnostics`, all four report formats, monkey suite, PDF fallback, and the dashboard live/browser smokes.
- [x] Reconciled the sandbox model catalog with README (`gpt-6-sol` was missing). Test count is now 92 after the demo-port regression test.

### 2026-11-26 — Docs consistency and P2 robustness `[x]`

- [x] Fixed every stale doc in section 10 and marked the entries complete.
- [x] Centralized reproduction-command shell quoting in `webdoctor/shell.py` and applied it across all checks and the runner.
- [x] Renamed “consensus” to “side-by-side” in `ai_engine.py` and its tests.
- [x] Fixed a real safety bug: the monkey submit guard was registered after navigation, and a bare arrow-function string was never invoked by Playwright. It is now an idempotent init script installed before `goto`, with a load-time submit regression test.
- [x] Added authenticated monkey, pasted-session reuse, CLI dispatch/version, shell-quoting, and dashboard export error-path tests; full suite now 100 passed.
- [x] Regenerated `webapp_static/pipeline.png` from the corrected `.mmd` with Mermaid CLI.
- [x] Replaced the hardcoded dashboard version with `TOOL_VERSION`.

## 9. Decision log / plan changes

```text
2026-11-24  Baseline plan: security hardening, accuracy fixes, dashboard extraction, tests.
2026-11-24  Added deterministic monkey suite after user asked whether monkey testing existed.
2026-11-24  Added Python 3.11 compatibility fix after CI failure.
2026-11-25  Added PDF Chromium fallback after WeasyPrint native-library failure on Windows.
2026-11-25  Added environment-issue separation after accuracy audit: tester failures must not penalize sites.
2026-11-25  Added dark crimson theme and original emblem after user requested anime-inspired look.
2026-11-25  Changed header to centered/large layout and fixed report exports after user feedback.
2026-11-25  Pushed changes; PR #1 merged to origin/main.
2026-11-26  Consolidated all completed work and future backlog into this living Agent.md plan.
2026-11-26  Chose per-process token + loopback Host validation over documenting the dashboard risk; no CORS grants and no inline handlers.
2026-11-26  Added best-effort DNS resolution to target validation; loopback hostnames stay allowed for the built-in demo and local tests.
2026-11-26  Changed `webdoctor demo` to an OS-assigned loopback port after the fixed 8765 default hit Windows port permissions.
2026-11-26  Installed the monkey submit guard before initial navigation as an idempotent init script after finding Playwright never invoked the bare arrow-function source.
2026-11-26  Renamed multi-model “consensus” output to “side-by-side” to match user-facing docs.
```

## 10. Known inconsistencies to fix

- [x] `README.md`: clarified that four formats are reports and `storage_state.json` is a session artifact written only for authenticated runs.
- [x] `README.md`: HTTP suite row now says total response time; browser suite reports TTFB separately.
- [x] `README.md` vs `ai_engine.py`: “consensus” renamed to “side-by-side” in code/tests to match the docs.
- [x] `VSCODE_SETUP.md`: “Manus built-in AI provider” replaced with “Sandbox (OpenAI-compatible proxy)”.
- [x] `webapp_static/pipeline.mmd` and PNG: regenerated with auth, monkey, environment issues, compare mode and total response time.
- [x] `last changes` stray `.")` at the end of a paragraph removed.
- [x] `last changes` Phase 4 PDF note now points at the later Chromium fallback entry.
- [x] `examples/programmatic_usage.py`: comment now states the provider default and rule-based fallback without credentials.
- [x] `examples/github_action.yml`: switched to `pip install -e .` and `playwright install --with-deps chromium`.
- [x] `webdoctor.egg-info/SOURCES.txt` marked as generated/stale; never used as a module inventory.
- [x] `webdoctor models` listed `sandbox:gpt-6-sol`; README's sandbox table now includes it.

## 11. Future plan — prioritized backlog

### P0 — security and authorization

- [x] Add DNS-resolution/rebinding tests for `validate_target_url()`; hostnames resolving inward are now rejected.
- [x] Dashboard CSRF/rebinding protection: per-process token on every API + loopback `Host` validation; any local process still needs the token from the rendered page.
- [x] Full-tree secret-scan gate documented in section 7 (not just added lines); sentinel smoke covers password/key/session leakage.
- [x] Authorization checklist added to `SECURITY.md`; written scope must be recorded in `last changes` before any external scan.

### P1 — accuracy and consistency

- [x] Shell-quote every reproduction command via `webdoctor.shell.quote()`; added quoting regression tests.
- [x] Renamed “consensus” language in code/tests to “side-by-side comparison”.
- [x] Fixed all stale docs listed in section 10.
- [x] Regenerated `pipeline.png` after fixing `pipeline.mmd` (Mermaid CLI v12.2.0).
- [ ] Re-verify model catalog against provider docs and record a fresh date/source (`ai_adapters.py` carries a 2026-11-24 note).
- [x] Keep `README.md` test count updated whenever tests are added (now 100).

### P2 — robustness and test coverage

- [x] Verified monkey submit guard installs before navigation (fixed an arrow-function string that Playwright never invoked); added a focused load-time submit test.
- [x] Added authenticated monkey test and pasted `session_json` → file → browser reuse test.
- [x] Added JS static checks for export button states and error paths; `node --check` remains in gates.
- [x] Made PDF determinism wording precise: WeasyPrint path only; Chromium output is platform-dependent.
- [x] Added CLI dispatch/version tests; dashboard test now uses `TOOL_VERSION` instead of a hardcoded `v1.0.0`.
- [ ] Refresh local `main` from `origin/main` before starting new work.

## 12. Update protocol

1. Read this file and the tail of `last changes` before acting.
2. Confirm branch and remote state with `git status --short --branch` and `git log -3 --oneline`.
3. Implement the smallest correct change; prefer `edit` over overwrite.
4. Add or update tests that prove the behavior.
5. Update `README.md`, `SECURITY.md`, or setup docs when behavior changes.
6. Run every applicable verification gate in section 7.
7. Append a new `last changes` entry using the existing format:

```markdown
## YYYY-MM-DD — Short milestone title

- Files: ...
- Change: ...
- Reason: ...
- Verification: <exact command> — PASS/FAIL
- Result: PASS/FAIL
```

8. Review `git status --short` and scan added lines for secrets.
9. Commit/push only if the user explicitly asks.
10. If a plan item is completed, change `[ ]` to `[x]`; if a plan changes, append a decision in section 9 instead of erasing history.
