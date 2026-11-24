"""Markdown reporter: generates a clean, short developer report."""
from __future__ import annotations

from typing import List
from webdoctor.models import TestReport, Issue, Severity, CheckStatus


GRADE_EMOJI = {"A": "A", "B": "B", "C": "C", "D": "D", "F": "F"}


def generate_markdown(report: TestReport) -> str:
    counts = report.issue_counts()
    perf = (report.metrics.get("perf") or {}) if isinstance(report.metrics, dict) else {}

    lines: List[str] = []
    lines.append(f"# WebDoctor Report — {report.url}")
    lines.append(f"**Target:** {report.final_url or report.url}  ")
    lines.append(f"**Tested:** {report.timestamp} UTC | **Duration:** {report.duration_ms/1000:.1f}s | **Tool:** WebDoctor {report.tool_version}")
    lines.append("")

    # Grade banner
    lines.append("## Summary")
    lines.append(f"| Grade | Score | Status | Critical | Errors | Warnings | Info |")
    lines.append(f"|---|---|---|---|---|---|---|")
    lines.append(f"| **{report.overall_grade}** | {report.score}/100 | {report.overall_status.value.upper()} | {counts['critical']} | {counts['error']} | {counts['warning']} | {counts['info']} |")
    lines.append("")

    # Metrics snapshot
    m = report.metrics
    lines.append("**Key metrics:** " + " | ".join(filter(None, [
        f"HTTP {m.get('status_code', 'n/a')}",
        f"Response {m.get('response_time_ms', 'n/a')}ms",
        f"TTFB {perf.get('ttfb_ms')}ms" if perf.get("ttfb_ms") else None,
        f"DCL {perf.get('dom_content_loaded_ms')}ms" if perf.get("dom_content_loaded_ms") else None,
        f"Load {perf.get('load_ms')}ms" if perf.get("load_ms") else None,
        f"JS errors: {m.get('js_error_count', 0)}",
        f"Broken links: {m.get('broken_count', 0)}",
    ])))
    lines.append("")

    # Executive diagnosis
    if report.ai_summary:
        lines.append("## Executive Diagnosis")
        lines.append(report.ai_summary)
        lines.append("")

    # Check suites summary
    lines.append("## Check Suites")
    lines.append("| Suite | Status | Duration | Issues |")
    lines.append("|---|---|---|---|")
    for cr in report.check_results:
        lines.append(f"| {cr.name} | **{cr.status.value.upper()}** | {cr.duration_ms}ms | {len(cr.issues)} |")
    lines.append("")

    # Dedicated deterministic monkey/chaos section
    monkey = next((cr for cr in report.check_results if cr.category == "monkey"), None)
    if monkey is not None:
        mm = monkey.metrics or {}
        lines.append("## Hard Monkey Test")
        if monkey.status == CheckStatus.SKIPPED:
            lines.append(f"- **Status:** SKIPPED ({mm.get('skipped_reason', 'not run')})")
            lines.append("")
        else:
            lines.append(
                f"- **Seed:** `{mm.get('seed', 'n/a')}` | **Status:** {monkey.status.value.upper()} | "
                f"**Actions:** {mm.get('actions_executed', 0)}/{mm.get('planned_actions', 0)} executed, "
                f"{mm.get('actions_skipped', 0)} skipped"
            )
            lines.append(
                f"- **Safety:** {mm.get('blocked_submit_count', 0)} form submits, "
                f"{mm.get('blocked_mutation_count', 0)} write requests and "
                f"{len(mm.get('off_origin_navigation_attempts') or [])} off-origin navigations blocked"
            )
            lines.append(
                f"- **Findings:** {mm.get('js_error_count', 0)} JS errors, "
                f"{mm.get('console_error_count', 0)} console errors, "
                f"{mm.get('dialog_count', 0)} dialogs, {mm.get('page_crash_count', 0)} crashes"
            )
            fingerprint = mm.get("config_fingerprint")
            if fingerprint:
                lines.append(f"- **Run fingerprint:** `{fingerprint}` (same seed + options = same action plan)")
            trace = (mm.get("trace") or [])[:25]
            if trace:
                lines.append("")
                lines.append("| Step | Action | Target | Result | URL |")
                lines.append("|---|---|---|---|---|")
                for step in trace:
                    target = (step.get("selector") or step.get("key") or
                              (f"dy={step.get('dy')}" if step.get("dy") is not None else
                               f"{step.get('width')}x{step.get('height')}" if step.get("width") else ""))
                    row = [
                        str(step.get("step", "")), str(step.get("type", "")), str(target),
                        str(step.get("result", "")), str(step.get("url", "")),
                    ]
                    lines.append("| " + " | ".join(v.replace("|", "\\|")[:90] for v in row) + " |")
                if len(mm.get("trace") or []) > 25:
                    lines.append("| ... | ... | ... | ... | ... |")
            lines.append("")

    # Issue details
    if report.issues:
        lines.append("## Issues & Root-Cause Explanations")
        for k, issue in enumerate(report.issues, 1):
            sev = issue.severity.value.upper()
            lines.append(f"### {k}. [{sev}] {issue.title}")
            lines.append(f"- **Category:** `{issue.category}` | **ID:** `{issue.id}`")
            lines.append(f"- **Summary:** {issue.summary}")
            if issue.detail and issue.detail != issue.summary:
                detail_block = issue.detail.replace("\n", "\n  > ")
                lines.append(f"- **Detail:**\n  > {detail_block}")
            if issue.how_it_happened:
                lines.append(f"- **How it happened:** {issue.how_it_happened}")
            if issue.reproduction:
                lines.append(f"- **Reproduce:** `{issue.reproduction}`")
            if issue.fix:
                lines.append(f"- **Fix:** {issue.fix}")
            lines.append("")
    else:
        lines.append("## Issues")
        lines.append("No issues found. All automated checks passed cleanly.")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by [WebDoctor](https://github.com/) — Automated website testing & root-cause diagnostics for developers.*")
    return "\n".join(lines)
