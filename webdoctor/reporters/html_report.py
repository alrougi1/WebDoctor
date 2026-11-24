"""Interactive standalone HTML report generator with grade dashboard and root-cause drawers."""
from __future__ import annotations

import base64
import html as html_escape
import json
import os
import re
from typing import Optional
from webdoctor.models import TestReport, Severity, CheckStatus


GRADE_COLORS = {
    "A": ("#10b981", "#059669"),
    "B": ("#3b82f6", "#2563eb"),
    "C": ("#f59e0b", "#d97706"),
    "D": ("#f97316", "#ea580c"),
    "F": ("#ef4444", "#dc2626"),
}

SEV_COLORS = {
    Severity.CRITICAL: ("#ef4444", "#fee2e2"),
    Severity.ERROR:    ("#f97316", "#ffedd5"),
    Severity.WARNING:  ("#f59e0b", "#fef3c7"),
    Severity.INFO:     ("#64748b", "#f1f5f9"),
}

# Allowlist used for AI-generated markdown before it is embedded in the report.
ALLOWED_HTML_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "strong", "em", "b", "i",
    "ul", "ol", "li", "code", "pre", "blockquote", "a", "table", "thead", "tbody",
    "tr", "th", "td", "span", "div",
]
ALLOWED_HTML_ATTRS = {
    "a": ["href", "title"],
    "code": ["class"],
    "pre": ["class"],
    "span": ["class"],
    "div": ["class"],
}


def _esc(s: Optional[str]) -> str:
    return html_escape.escape(str(s or ""), quote=True)


def _md_to_html(md: str) -> str:
    """Markdown -> HTML converter. AI output is sanitized before embedding."""
    if not md:
        return ""
    try:
        import markdown as mdlib
        import bleach
        rendered = mdlib.markdown(md, extensions=["fenced_code", "tables"])
        return bleach.clean(
            rendered,
            tags=ALLOWED_HTML_TAGS,
            attributes=ALLOWED_HTML_ATTRS,
            strip=True,
        )
    except Exception:
        pass
    # Fallback basic conversion
    out = _esc(md)
    out = re.sub(r"^### (.*)$", r"<h3>\1</h3>", out, flags=re.M)
    out = re.sub(r"^## (.*)$", r"<h2>\1</h2>", out, flags=re.M)
    out = re.sub(r"^# (.*)$", r"<h1>\1</h1>", out, flags=re.M)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"^\- (.*)$", r"<li>\1</li>", out, flags=re.M)
    out = out.replace("\n\n", "</p><p>")
    return f"<p>{out}</p>"


def _screenshot_data_uri(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return None


def _monkey_section(report: TestReport) -> str:
    """Render the deterministic monkey-test trace as its own report section."""
    monkey = next((cr for cr in report.check_results if cr.category == "monkey"), None)
    if monkey is None:
        return ""
    mm = monkey.metrics or {}
    if monkey.status == CheckStatus.SKIPPED:
        return (
            '<section class="sec"><h2>Hard Monkey Test</h2>'
            f'<div class="empty">Skipped ({_esc(mm.get("skipped_reason", "not run"))}).</div></section>'
        )

    rows = []
    for step in (mm.get("trace") or [])[:50]:
        target = (step.get("selector") or step.get("key") or
                  (f"scroll dy={step.get('dy')}" if step.get("dy") is not None else
                   f"viewport {step.get('width')}x{step.get('height')}" if step.get("width") else "window"))
        rows.append(
            "<tr><td>" + _esc(step.get("step", "")) + "</td><td>" + _esc(step.get("type", "")) +
            "</td><td><code>" + _esc(target) + "</code></td><td>" + _esc(step.get("result", "")) +
            "</td><td>" + _esc(step.get("url", "")) + "</td></tr>"
        )
    trace_html = "".join(rows) or "<tr><td colspan='5'>No actions were executed.</td></tr>"
    offsite = mm.get("off_origin_navigation_attempts") or []
    stats = [
        ("Seed", mm.get("seed", "n/a")),
        ("Actions", f"{mm.get('actions_executed', 0)}/{mm.get('planned_actions', 0)} executed"),
        ("Skipped", mm.get("actions_skipped", 0)),
        ("JS errors", mm.get("js_error_count", 0)),
        ("Console errors", mm.get("console_error_count", 0)),
        ("Dialogs", mm.get("dialog_count", 0)),
        ("Crashes", mm.get("page_crash_count", 0)),
        ("Blocked submits", mm.get("blocked_submit_count", 0)),
        ("Blocked writes", mm.get("blocked_mutation_count", 0)),
        ("Off-origin blocked", len(offsite)),
    ]
    stat_cards = "".join(
        f'<div class="stat"><div class="stat-k">{_esc(label)}</div>'
        f'<div class="stat-v">{_esc(value)}</div></div>' for label, value in stats
    )
    fingerprint = mm.get("config_fingerprint")
    fingerprint_html = (
        f'<p class="sub">Run fingerprint: <code>{_esc(fingerprint)}</code> — '
        'the same seed and options produce the same action plan.</p>' if fingerprint else ""
    )
    return f"""
<section class="sec">
  <h2>Hard Monkey Test — Deterministic Browser Chaos</h2>
  <div class="diag-box">
    <div class="stats-grid">{stat_cards}</div>
    {fingerprint_html}
  </div>
  <details style="margin-top:12px">
    <summary><strong>Action trace ({len(mm.get('trace') or [])} steps)</strong></summary>
    <table style="margin-top:10px">
      <thead><tr><th>Step</th><th>Action</th><th>Target</th><th>Result</th><th>URL</th></tr></thead>
      <tbody>{trace_html}</tbody>
    </table>
  </details>
</section>"""


def generate_html(report: TestReport) -> str:
    counts = report.issue_counts()
    grade = report.overall_grade
    c1, c2 = GRADE_COLORS.get(grade, ("#64748b", "#475569"))
    perf = (report.metrics.get("perf") or {}) if isinstance(report.metrics, dict) else {}
    m = report.metrics
    ss_uri = _screenshot_data_uri(report.screenshot_path)
    ai_html = _md_to_html(report.ai_summary or "")
    # Python 3.11 rejects backslashes inside f-string expressions (PEP 701 is
    # 3.12+), so escape the embedded JSON outside the template literal.
    report_data_json = json.dumps(report.to_dict(), default=str).replace("</", "<\\/")

    # Issue cards
    cards = []
    for k, it in enumerate(report.issues):
        sev = it.severity
        color, bg = SEV_COLORS.get(sev, ("#64748b", "#f1f5f9"))
        detail_lines = _esc(it.detail).replace("\n", "<br>")
        cards.append(f"""
        <article class="issue-card" data-severity="{_esc(sev.value)}" data-cat="{_esc(it.category)}">
          <div class="issue-head" onclick="this.parentElement.classList.toggle('open')">
            <span class="sev-badge" style="background:{bg};color:{color};">{_esc(sev.value.upper())}</span>
            <span class="cat-pill">{_esc(it.category)}</span>
            <h3 class="issue-title">{_esc(it.title)}</h3>
            <span class="chevron" aria-hidden="true">&#9662;</span>
          </div>
          <p class="issue-sum">{_esc(it.summary)}</p>
          <div class="issue-drawer">
            {f'<div class="box detail-box"><strong>Technical detail</strong><pre>{detail_lines}</pre></div>' if it.detail else ''}
            {f'<div class="box how-box"><strong>How the error happened</strong><p>{_esc(it.how_it_happened)}</p></div>' if it.how_it_happened else ''}
            {f'<div class="box repro-box"><strong>Reproduction</strong><code>{_esc(it.reproduction)}</code><button class="copy-btn" data-copy="{_esc(it.reproduction)}">Copy</button></div>' if it.reproduction else ''}
            {f'<div class="box fix-box"><strong>Recommended fix</strong><p>{_esc(it.fix)}</p></div>' if it.fix else ''}
          </div>
        </article>""")
    issues_html = "".join(cards) if cards else "<div class='empty'>No issues detected. Everything looks healthy!</div>"

    # Suite rows
    suites_html = "".join(f"""
      <tr>
        <td>{_esc(cr.name)}</td>
        <td><span class="status-pill status-{_esc(cr.status.value)}">{_esc(cr.status.value.upper())}</span></td>
        <td>{cr.duration_ms} ms</td>
        <td>{len(cr.issues)}</td>
      </tr>""" for cr in report.check_results)

    # Timing bars
    timing_items = [
        ("DNS lookup", perf.get("dns_ms")),
        ("TCP connect", perf.get("tcp_ms")),
        ("TTFB (first byte)", perf.get("ttfb_ms")),
        ("HTML download", perf.get("download_ms")),
        ("DOMContentLoaded", perf.get("dom_content_loaded_ms")),
        ("Load event", perf.get("load_ms")),
        ("First Contentful Paint", perf.get("first_contentful_paint_ms")),
    ]
    tvals = [v for _, v in timing_items if v is not None and v > 0]
    tmax = max(tvals) if tvals else 1
    timing_html = "".join(f"""
      <div class="t-row">
        <span class="t-label">{_esc(label)}</span>
        <div class="t-track"><div class="t-fill" style="width:{min(100, round(100*val/tmax))}%"></div></div>
        <span class="t-val">{val} ms</span>
      </div>""" for label, val in timing_items if val is not None)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WebDoctor Report — {_esc(report.url)}</title>
<style>
:root{{--bg:#0b0f19;--card:#121a2b;--card2:#172238;--txt:#e2e8f0;--mut:#94a3b8;--border:#1f2f4f;--accent:#6366f1;--grad1:{c1};--grad2:{c2}}}
*{{box-sizing:border-box}} body{{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--txt)}}
a{{color:#a5b4fc}} .wrap{{max-width:1160px;margin:0 auto;padding:28px 18px 80px}}
header{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;border-bottom:1px solid var(--border);padding-bottom:18px}}
.logo{{font-size:24px;font-weight:800;letter-spacing:-.5px}} .logo span{{color:var(--grad1)}}
.meta{{color:var(--mut);font-size:13px}}
.hero{{display:grid;grid-template-columns:220px 1fr;gap:20px;margin-top:24px;background:linear-gradient(135deg,rgba(99,102,241,.12),transparent 70%);border:1px solid var(--border);border-radius:18px;padding:22px}}
.grade-card{{display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(135deg,var(--grad1),var(--grad2));border-radius:14px;color:#fff;padding:22px;text-align:center;box-shadow:0 12px 30px rgba(0,0,0,.35)}}
.grade{{font-size:86px;font-weight:900;line-height:1;margin:0}} .g-sub{{font-size:14px;opacity:.9;margin-top:4px}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.stat{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px}}
.stat-k{{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}}
.stat-v{{font-size:22px;font-weight:700;margin-top:2px}}
.sec{{margin-top:34px}} h2{{font-size:20px;margin:0 0 14px;letter-spacing:-.3px}}
.diag-box{{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:14px;padding:18px 22px;color:var(--txt)}}
.diag-box p{{margin:8px 0}} .diag-box h2,.diag-box h3{{color:#fff}} .diag-box code{{background:rgba(255,255,255,.08);padding:2px 6px;border-radius:4px;font-size:13px}}
.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 14px}}
.chip{{background:var(--card);border:1px solid var(--border);color:var(--txt);padding:6px 13px;border-radius:20px;font-size:13px;cursor:pointer}}
.chip.active,.chip:hover{{border-color:var(--accent);color:#fff}}
.issue-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;margin-bottom:10px;overflow:hidden}}
.issue-head{{display:flex;align-items:center;gap:10px;padding:12px 16px;cursor:pointer;flex-wrap:wrap}}
.issue-title{{font-size:15px;margin:0;font-weight:600;flex:1 1 300px}}
.sev-badge{{font-size:11px;font-weight:800;letter-spacing:.08em;padding:3px 9px;border-radius:10px;text-transform:uppercase}}
.cat-pill{{font-size:11px;color:var(--mut);border:1px solid var(--border);padding:2px 8px;border-radius:8px}}
.chevron{{color:var(--mut);transition:transform .2s;margin-left:auto}}
.open .chevron{{transform:rotate(180deg)}}
.issue-sum{{margin:0;padding:0 16px 14px;color:var(--mut);font-size:14px}}
.issue-drawer{{display:none;padding:0 16px 16px;border-top:1px solid var(--border)}}
.open .issue-drawer{{display:block}}
.box{{background:var(--card2);border-radius:10px;padding:12px 14px;margin-top:10px;font-size:13.5px}}
.box strong{{display:block;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin-bottom:4px}}
.box pre{{margin:0;white-space:pre-wrap;font-size:12px;color:#cbd5e1}} .how-box{{background:rgba(99,102,241,.12);border-left:3px solid var(--accent)}}
.repro-box code{{display:block;white-space:pre-wrap;word-break:break-all;font-size:12px;color:#a5b4fc}}
.copy-btn{{margin-top:6px;background:none;border:1px solid var(--accent);color:#a5b4fc;padding:3px 9px;border-radius:6px;cursor:pointer;font-size:11px}}
.copy-btn:hover{{background:var(--accent);color:#fff}}
.fix-box p,.how-box p{{margin:4px 0 0}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;border:1px solid var(--border);font-size:14px}}
th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid var(--border)}} th{{background:var(--card2);color:var(--mut);font-weight:600}}
.status-pill{{font-size:11px;font-weight:700;padding:3px 8px;border-radius:8px}}
.status-passed{{background:#064e3b;color:#6ee7b7}} .status-warn{{background:#78350f;color:#fcd34d}} .status-failed{{background:#7f1d1d;color:#fca5a5}} .status-skipped{{background:#334155;color:#cbd5e1}}
.t-row{{display:grid;grid-template-columns:200px 1fr 90px;gap:12px;align-items:center;margin:8px 0;font-size:13px}}
.t-track{{background:var(--card2);border-radius:6px;height:14px;overflow:hidden}}
.t-fill{{background:linear-gradient(90deg,var(--accent),#a855f7);height:100%}}
.t-val{{text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}}
.shot{{max-width:100%;border-radius:12px;border:1px solid var(--border);box-shadow:0 14px 40px rgba(0,0,0,.45)}}
.toolbar{{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}}
.btn{{background:var(--accent);color:#fff;border:none;padding:8px 15px;border-radius:9px;cursor:pointer;font-size:13px;font-weight:600;text-decoration:none}}
.btn.sec{{background:var(--card2);border:1px solid var(--border);color:var(--txt)}}
.empty{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:30px;text-align:center;color:var(--mut)}}
footer{{margin-top:60px;text-align:center;color:var(--mut);font-size:13px;border-top:1px solid var(--border);padding-top:20px}}
@media(max-width:768px){{.hero{{grid-template-columns:1fr}}.t-row{{grid-template-columns:140px 1fr 70px}}}}
@media print{{body{{background:#fff;color:#111}}:root{{--bg:#fff;--card:#f8fafc;--card2:#eef2f7;--txt:#0f172a;--mut:#475569;--border:#cbd5e1}}.btn,.copy-btn,.filters,.chevron{{display:none}}.issue-drawer{{display:block!important}}.issue-head{{cursor:default}}}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <div class="logo"><span>&#9670;</span> WebDoctor</div>
    <div class="meta">Automated website testing &amp; root-cause diagnostics</div>
  </div>
  <div class="meta" style="text-align:right">
    <strong>{_esc(report.url)}</strong><br>
    {_esc(report.timestamp)} UTC &bull; took {report.duration_ms/1000:.1f}s &bull; v{report.tool_version}
  </div>
</header>

<section class="hero">
  <div class="grade-card">
    <p class="g-sub">OVERALL GRADE</p>
    <p class="grade">{_esc(report.overall_grade)}</p>
    <p class="g-sub">Score {report.score}/100 &bull; {_esc(report.overall_status.value.upper())}</p>
  </div>
  <div class="stats-grid">
    <div class="stat"><div class="stat-k">Critical</div><div class="stat-v" style="color:#ef4444">{counts['critical']}</div></div>
    <div class="stat"><div class="stat-k">Errors</div><div class="stat-v" style="color:#f97316">{counts['error']}</div></div>
    <div class="stat"><div class="stat-k">Warnings</div><div class="stat-v" style="color:#f59e0b">{counts['warning']}</div></div>
    <div class="stat"><div class="stat-k">Info</div><div class="stat-v">{counts['info']}</div></div>
    <div class="stat"><div class="stat-k">HTTP Status</div><div class="stat-v">{m.get('status_code','n/a')}</div></div>
    <div class="stat"><div class="stat-k">Response Time</div><div class="stat-v">{m.get('response_time_ms','n/a')}{'ms' if m.get('response_time_ms') is not None else ''}</div></div>
    <div class="stat"><div class="stat-k">JS Errors</div><div class="stat-v">{m.get('js_error_count',0)}</div></div>
    <div class="stat"><div class="stat-k">Broken Links</div><div class="stat-v">{m.get('broken_count',0)}</div></div>
  </div>
</section>

{f'''<section class="sec"><h2>Executive Diagnosis</h2><div class="diag-box">{ai_html}</div></section>''' if ai_html else ''}

<section class="sec">
  <h2>Issues &amp; Root-Cause Explanations ({len(report.issues)})</h2>
  <div class="filters">
    <button class="chip active" data-f="all">All ({len(report.issues)})</button>
    <button class="chip" data-f="critical" style="color:#ef4444">Critical ({counts['critical']})</button>
    <button class="chip" data-f="error" style="color:#f97316">Errors ({counts['error']})</button>
    <button class="chip" data-f="warning" style="color:#f59e0b">Warnings ({counts['warning']})</button>
    <button class="chip" data-f="info">Info ({counts['info']})</button>
    <button class="chip" id="expAll" style="margin-left:auto">Expand all</button>
  </div>
  <div id="issues">{issues_html}</div>
</section>

<section class="sec">
  <h2>Check Suites</h2>
  <table>
    <thead><tr><th>Suite</th><th>Status</th><th>Duration</th><th>Issues</th></tr></thead>
    <tbody>{suites_html}</tbody>
  </table>
</section>

{_monkey_section(report)}

{f'<section class="sec"><h2>Page Timing Waterfall</h2>{timing_html}</section>' if tvals else ''}

{f'<section class="sec"><h2>Visual Evidence</h2><img class="shot" src="{ss_uri}" alt="Full page screenshot"></section>' if ss_uri else ''}

<div class="toolbar">
  <button class="btn" onclick="window.print()">Print / Save PDF</button>
  <button class="btn sec" onclick="downloadJSON()">Download JSON</button>
  <a class="btn sec" id="dl_md_report" href="report.md" download>Download Markdown</a>
</div>

<footer>Generated by WebDoctor &bull; The developer-first website testing &amp; diagnostics toolkit</footer>
</div>

<script>
const reportData = {report_data_json};
function downloadJSON(){{
  const b=new Blob([JSON.stringify(reportData,null,2)],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='webdoctor-report.json';a.click();
}}
document.querySelectorAll('.filters .chip[data-f]').forEach(c=>c.onclick=()=>{{
  document.querySelectorAll('.filters .chip[data-f]').forEach(x=>x.classList.remove('active'));c.classList.add('active');
  const f=c.dataset.f;
  document.querySelectorAll('.issue-card').forEach(it=>it.style.display=(f==='all'||it.dataset.severity===f)?'':'none');
}});
let expanded=false;
document.getElementById('expAll').onclick=function(){{
  expanded=!expanded;
  document.querySelectorAll('.issue-card').forEach(it=>it.classList.toggle('open',expanded));
  this.innerText=expanded?'Collapse all':'Expand all';
}};
document.querySelectorAll('.copy-btn').forEach(b=>b.onclick=()=>{{
  navigator.clipboard.writeText(b.dataset.copy||'');
  const old=b.innerText; b.innerText='Copied!';
  setTimeout(()=>{{b.innerText=old;}},1500);
}});
// When served by the dashboard, report files live at /api/reports/<id>/<fmt>.
if(location.pathname.startsWith('/api/reports/')){{
  document.getElementById('dl_md_report').href='md';
}}
</script>
</body>
</html>"""
