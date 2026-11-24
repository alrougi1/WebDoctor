# WebDoctor

**Automated website testing and root-cause diagnostics for developers — with multiple AI providers (ChatGPT, Gemini, Claude) and authenticated/logged-in testing.**

![WebDoctor Pipeline](webapp_static/pipeline.png)

---

## What WebDoctor tests

| Suite | Detects |
|---|---|
| **HTTP & SSL** | 4xx/5xx codes, response time, TTFB, redirect chains, SSL validation, gzip/brotli compression |
| **Security headers** | HSTS, CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, insecure cookies |
| **SEO & accessibility** | Title/description, viewport, `lang`, missing `alt` on images, headings, favicon, form input labels |
| **Broken link crawler** | Concurrently checks up to 40 unique `<a>`/`<img>`/resource URLs for 404s/5xxs/timeouts/SSL errors (truncation is reported in metrics) |
| **Real browser runtime** | Playwright Chromium: JS exceptions + stack traces, console errors, failed sub-resources, performance waterfall, screenshot |
| **Surveys & popups** | Auto-dismisses survey/feedback modals and cookie banners; inventories survey trackers; audits form/survey inputs |
| **Authentication** | Automated form login, session capture/reuse, auth-cookie security audit, post-login protected-area testing |
| **Hard monkey test** | Deterministic, seeded browser chaos: clicks, typing, scrolling, keyboard and viewport changes with a full action trace; reports JS errors, dialogs, blank pages, crashes and blocked unsafe actions |
| **Diagnostics** | Every issue includes *"How it happened"*, a reproduction command, and a developer fix |

---

## 1. Multi-AI diagnosis (ChatGPT, Gemini, Claude, and more)

Choose your AI provider per run — or **compare multiple models side by side** in one report (each model's diagnosis is shown separately for you to reconcile):

| Provider | Models (verified IDs) | Setup |
|---|---|---|
| **Sandbox (OpenAI-compatible proxy)** | `gpt-6-luna`, `gpt-6.1-sol`, `gemini-3.8-flash` | Only in managed environments; otherwise falls back automatically |
| **OpenAI direct** | `gpt-6.2-astra`, `gpt-6.2-sol`, `gpt-6.1-terra`, `gpt-5.5` | Set `OPENAI_API_KEY` or `--ai-key` |
| **Anthropic Claude** | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-2` | Set `ANTHROPIC_API_KEY` or `--ai-key` |
| **Google Gemini direct** | `gemini-4-carbon`, `gemini-4-neon`, `gemini-4-hydrogen`, `gemini-4-argon`, `gemini-3.8-flash` | Set `GEMINI_API_KEY` or `--ai-key` |

Defaults when `--ai-model` is omitted: `sandbox → gpt-6-luna`, `openai → gpt-6.2-sol`,
`anthropic → claude-sonnet-5-5`, `gemini → gemini-4-hydrogen`.
Retired/deprecated IDs (`gpt-4o`, `claude-sonnet-4`, `gemini-2.5-*`, etc.) are not advertised;
`webdoctor models` is the authoritative catalog.

```bash
# List available models
webdoctor models

# Local/GitHub install: use a provider API key (model default is provider-specific)
export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
webdoctor check https://example.com --ai-provider openai

# Or use Claude
export ANTHROPIC_API_KEY="YOUR_ANTHROPIC_API_KEY"
webdoctor check https://example.com --ai-provider anthropic --ai-key "$ANTHROPIC_API_KEY"

export GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
webdoctor check https://example.com --ai-provider gemini --ai-model gemini-4-hydrogen --ai-key "$GEMINI_API_KEY"

# Managed environments only: built-in AI and comparison
webdoctor check https://example.com --ai-model gpt-6-luna
webdoctor check https://example.com --ai-compare sandbox:gpt-6-luna,sandbox:gemini-3.8-flash
```

Without a usable API key (or configured proxy base URL), WebDoctor automatically uses the
deterministic rule-based summary and prints a clear notice in the report; pass `--no-ai` to
skip the AI step entirely.

---

## 2. Authenticated / logged-in testing ("test like a real user")

WebDoctor can **log in with your account** and run all tests *inside the protected area* — so hidden pages (dashboards, survey panels, admin areas) get the same deep analysis:

### Method A — Web dashboard (easiest)

1. Open `http://localhost:8080`
2. Expand **"Log in and test as a real user"**
3. Enter your login URL, username, and password, then click **Verify login** to confirm credentials
4. Click **Run Diagnosis** — the report shows a green **LOGGED IN RUN** badge and scans the post-login pages

### Method B — CLI automated form login

```bash
webdoctor check "https://app.example.com/dashboard" \
  --login --login-url "https://app.example.com/login" \
  --username "you@company.com" --password "secret" --pdf
```

The browser fills and submits the form, audits the resulting session cookie, saves `storage_state.json`, and tests the protected area.

### Method C — Headful login for 2FA / SSO / OAuth (one time)

```bash
# A real browser opens -> you log in manually (SSO/2FA/captcha) -> press Enter -> session saved
webdoctor login "https://app.example.com/login" --save-session ./my-session.json

# Now reuse that session for unlimited authenticated tests
webdoctor check "https://app.example.com/dashboard" --session ./my-session.json --pdf
```

---

## 3. Hard monkey test (deterministic browser chaos)

WebDoctor did not previously include monkey/fuzz/chaos testing, so this suite was added. It
drives the real Chromium page with a seeded random planner and records every action.
Repeating the same seed, action count, delay and URL produces the same action plan.

```bash
# Hard mode: 200 seeded interactions, full trace in all reports
webdoctor check https://example.com --monkey --no-ai

# Reproduce an exact run
webdoctor check https://example.com --monkey --monkey-seed 4242 \
  --monkey-actions 200 --monkey-delay-ms 60

# Even stricter: never follow same-origin links either
webdoctor check https://example.com --monkey --monkey-no-navigation

# The dashboard has a "Hard monkey test" switch with seed/actions/delay controls
webdoctor serve --port 8080
```

**Safety guards (always active):**

* form submits, `form.submit()`/`requestSubmit()`, POST/PUT/PATCH/DELETE requests, downloads
  and off-origin document navigation are blocked;
* logout/delete/pay/submit-style labels and URLs, password/file inputs, hidden/disabled
  controls and `javascript:`/`mailto:`/`tel:` links are never selected;
* native dialogs are dismissed, never accepted; keyboard actions never press Enter;
* `--monkey` requires the browser suite (`--no-browser` is rejected with a clear error).

**What it detects:** `MONKEY_PAGE_CRASH`, `MONKEY_JS_E<signature>`, `MONKEY_CONSOLE_E<signature>`,
`MONKEY_BLANK_PAGE`, `MONKEY_DIALOG`, `MONKEY_OFF_ORIGIN_BLOCKED`, `MONKEY_SUBMIT_BLOCKED`,
`MONKEY_MUTATION_BLOCKED`, and `MONKEY_RUNNER_ERROR`. Errors already reported by the ordinary
browser suite are suppressed to avoid double penalties. The HTML/Markdown reports include a
dedicated **Hard Monkey Test** section with the run fingerprint, seed, action counts and trace;
JSON includes the same data under `check_results[category="monkey"]` and `metrics.monkey`.

---

## Installation

```bash
git clone https://github.com/alrougi1/webdoctor.git
cd webdoctor
pip install -e .
playwright install chromium
```

---

## Quick start

```bash
# Free rule-based test
webdoctor check https://example.com --no-ai

# Full test with AI diagnosis + PDF
webdoctor check https://example.com --pdf

# CI/CD mode (exit code 0=pass / 1=fail)
webdoctor check https://example.com --no-ai --ci

# See all failure modes on the built-in broken demo site
webdoctor demo --pdf

# Launch the interactive web dashboard (binds to 127.0.0.1 by default)
webdoctor serve --port 8080
```

---

## Dashboard screenshots

![WebDoctor dashboard](webapp_static/dashboard_v2.png)


---

## Reports

Every run can emit four reports (saved to `--report-dir`, default `./reports`):

| File | Purpose |
|---|---|
| `report.html` | Self-contained interactive report with embedded screenshots |
| `report.md` | One-page markdown — PR comments, CI summaries, Slack bots |
| `report.json` | Machine-readable results for dashboards and Datadog |
| `report.pdf` | Clean shareable PDF for stakeholders (WeasyPrint; skipped with a warning if its system libraries are unavailable) |
| `storage_state.json` | Saved login session for authenticated runs (reusable; keep private) |

**Important:** Reports and session files can contain private URLs, screenshots, and reusable authentication cookies. Never publish or share them. The repository ignores generated `reports/` by default; use a test account and protect session files like passwords.

### Scoring formula

Root causes are de-duplicated by a fingerprint of cause category, normalized issue ID, and
target URL; status-based issues from different suites (e.g. `HTTP_4XX`, `LINK_BROKEN`,
`NET_FAIL`) describing the same HTTP status on the same URL count once. All occurrences remain
in the detailed per-suite lists, and `score_breakdown` in `report.json` exposes the audit trail.

```text
score = 100 - 30*critical - 15*errors - 4*warnings
```

* Critical issues are always counted and always force a failed status.
* Errors are capped at 3 per category; warnings at 5 per category; INFO is never penalized.
* Grades: A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40, otherwise F.

---

## CI/CD integration

Copy [`examples/github_action.yml`](examples/github_action.yml) into `.github/workflows/webdoctor.yml`.

`webdoctor check` exits `0` when the overall status is PASSED/WARN and `1` when it is FAILED,
so CI can gate on the exit code. `--ci` removes interactive output and keeps the run non-interactive.

---

## Python SDK & pytest

```python
from webdoctor import run_diagnostics

report = run_diagnostics("https://example.com", browser_mode=True, check_links=True, ai=False)
assert report.score >= 80
assert report.metrics.get("js_error_count", 0) == 0
```

---

## Architecture

```
webdoctor/
├── webdoctor/
│   ├── models.py           # Issue, CheckResult, TestReport dataclasses + scoring
│   ├── config.py           # Timeouts, user-agent, AI models
│   ├── runner.py           # Suite orchestrator (anonymous + authenticated)
│   ├── cli.py              # CLI with rich terminal output
│   ├── checks/
│   │   ├── http_check.py   # HTTP status, TTFB, SSL, redirects, compression
│   │   ├── security.py     # Security headers & cookie flags
│   │   ├── browser.py      # Playwright: JS errors, console, perf, screenshots, popup dismisser
│   │   ├── monkey.py       # Seeded hard monkey/chaos engine, safety guards, action trace
│   │   ├── links.py        # Concurrent broken link crawler
│   │   ├── seo_a11y.py     # SEO + a11y + form/survey input labels
│   │   └── auth.py         # Form login, session capture/reuse, auth-cookie audit
│   ├── diagnostics/
│   │   ├── ai_adapters.py  # Multi-AI provider hub (OpenAI, Gemini, Claude, custom)
│   │   ├── ai_engine.py    # Multi-model comparison + single-model diagnosis
│   │   └── rule_engine.py  # Free offline root-cause knowledge base
│   ├── reporters/          # HTML, Markdown, JSON, PDF generators
│   └── server/             # FastAPI dashboard with live progress
├── tests/                  # 22-test legacy suite + focused pytest suites
│   └── test_all.py         # Unit & integration suite (also collected by pytest)
├── examples/               # GitHub Actions, pytest, Python SDK
└── reports/                # Generated reports & screenshots
```

---

## Running tests

```bash
pytest -q                    # all suites (legacy + focused regression tests)
python tests/test_all.py     # legacy suite only
```

The current full suite is 65 tests (22 legacy + 43 focused regression tests, including 12 monkey-suite tests).

---

## License

[MIT](LICENSE)
