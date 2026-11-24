"""Multi-AI root-cause diagnosis engine for WebDoctor. Supports OpenAI (Sandbox built-in),
Gemini, Claude, and custom providers, plus multi-model consensus comparison."""
from __future__ import annotations

import json
import logging
from typing import List, Dict, Optional

from webdoctor.models import TestReport
from webdoctor.config import AI_MODEL_FAST
from webdoctor.diagnostics.rule_engine import generate_rule_summary
from webdoctor.diagnostics.ai_adapters import call_llm

log = logging.getLogger(__name__)


def build_diagnosis_prompt(report: TestReport) -> str:
    issues_payload = [
        {"id": i.id, "sev": i.severity.value, "title": i.title[:90], "detail": i.detail[:160]}
        for i in report.issues[:10]
    ]
    counts = report.issue_counts()
    return (
        f"Web reliability diagnosis for {report.final_url or report.url}. "
        f"Grade: {report.overall_grade} ({report.score}/100). "
        f"Critical={counts['critical']} Error={counts['error']} Warn={counts['warning']}.\n"
        f"Issues:\n{json.dumps(issues_payload)}\n\n"
        "Provide: (1) Executive Root-Cause Diagnosis (2 paragraphs explaining HOW the failure "
        "happened at the HTTP/browser/server level and user impact), "
        "(2) Root-Cause Breakdown for top 3 issues with title, how it happened, reproduction "
        "command, and developer fix, (3) Sequenced Action Plan (Today / This Week / Next Sprint). "
        "Keep it concise and technical. Markdown only."
    )


def generate_ai_diagnosis(
    report: TestReport,
    model: str = AI_MODEL_FAST,
    provider: str = "sandbox",
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    compare: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Generate AI diagnosis. `compare` runs multiple models and produces a consensus report."""
    if not report.issues:
        return generate_rule_summary(report)

    prompt = build_diagnosis_prompt(report)

    if compare and len(compare) > 1:
        return _multi_model_diagnosis(prompt, compare)

    try:
        content = call_llm(prompt, provider=provider, model=model, api_key=api_key, api_base=api_base, max_tokens=2200)
        if content:
            return content
    except Exception as e:
        log.warning("AI call failed (%s); using rule summary", e)

    return generate_rule_summary(report)


def _multi_model_diagnosis(prompt: str, models: List[Dict[str, str]]) -> str:
    """Query 2-4 models sequentially (avoid sandbox proxy concurrency issues) and stitch consensus."""
    results: Dict[str, str] = {}
    for m in models:
        label = m.get("label") or m.get("model", "AI")
        try:
            results[label] = call_llm(
                prompt,
                provider=m.get("provider", "sandbox"),
                model=m.get("model", AI_MODEL_FAST),
                api_key=m.get("api_key"),
                api_base=m.get("api_base"),
                max_tokens=2200,
            )
        except Exception as e:
            results[label] = f"*Model call failed: {e}*"

    out = ["# Multi-AI Consensus Diagnosis\n",
           f"> WebDoctor queried **{len(results)} AI models** for this diagnosis. "
           "Where all models agree, act with highest priority; where they differ, "
           "verify with the reproduction commands provided.\n"]
    for label, text in results.items():
        out.append(f"\n## {label}\n\n{text}\n")
    return "\n".join(out)
