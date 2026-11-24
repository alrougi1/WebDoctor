"""Tests for provider-specific model defaults and verified model catalog."""
import pytest

from webdoctor.cli import build_parser
from webdoctor.config import (
    AI_MODEL_FAST,
    AI_MODEL_OPENAI,
    AI_MODEL_ANTHROPIC,
    AI_MODEL_GEMINI,
    default_model_for_provider,
)
from webdoctor.diagnostics.ai_adapters import list_available_models
from webdoctor.diagnostics.ai_engine import generate_ai_diagnosis
from webdoctor.models import CheckResult, CheckStatus, Issue, Severity, TestReport


def _report_with_issue():
    issue = Issue(
        id="TEST_ISSUE", category="test", severity=Severity.ERROR,
        title="Synthetic failure", summary="Synthetic failure for tests.",
    )
    return TestReport(
        url="https://example.com",
        check_results=[CheckResult(name="t", category="test", status=CheckStatus.FAILED,
                                   issues=[issue])],
    ).compute_summary()


def test_default_model_for_each_provider():
    assert default_model_for_provider("sandbox") == AI_MODEL_FAST
    assert default_model_for_provider("openai") == AI_MODEL_OPENAI
    assert default_model_for_provider("anthropic") == AI_MODEL_ANTHROPIC
    assert default_model_for_provider("claude") == AI_MODEL_ANTHROPIC
    assert default_model_for_provider("gemini") == AI_MODEL_GEMINI
    with pytest.raises(ValueError):
        default_model_for_provider("unknown")


def test_parser_resolves_model_from_selected_provider():
    p = build_parser()
    assert p.parse_args(["check", "example.com"]).ai_model == AI_MODEL_FAST
    assert p.parse_args(["check", "example.com", "--ai-provider", "openai"]).ai_model == AI_MODEL_OPENAI
    assert p.parse_args(["check", "example.com", "--ai-provider", "anthropic"]).ai_model == AI_MODEL_ANTHROPIC
    assert p.parse_args(["check", "example.com", "--ai-provider", "gemini"]).ai_model == AI_MODEL_GEMINI
    # Explicit --ai-model wins regardless of argument order.
    assert p.parse_args(["check", "example.com", "--ai-model", "custom",
                         "--ai-provider", "gemini"]).ai_model == "custom"
    assert p.parse_args(["check", "example.com", "--ai-provider", "gemini",
                         "--ai-model", "custom"]).ai_model == "custom"
    # Demo builds check args through the real parser; it must not break.
    demo = p.parse_args(["demo", "--no-ai"])
    assert demo.ai_provider == "sandbox"
    assert demo.ai_model == AI_MODEL_FAST


def test_catalog_contains_only_verified_ids():
    models = list_available_models()
    ids = {m["id"] for m in models}
    retired = {
        "gpt-5-mini", "gpt-4.1", "gpt-4o", "o3-mini",
        "claude-sonnet-4", "claude-haiku-4", "claude-opus-4",
        "gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash",
    }
    assert not ids.intersection(retired)
    by_provider = {}
    for m in models:
        by_provider.setdefault(m["provider"], set()).add(m["id"])
    assert {"gpt-6-luna", "gpt-6.1-sol", "gemini-3.8-flash"} <= by_provider["sandbox"]
    assert {"gpt-6.2-astra", "gpt-6.2-sol", "gpt-5.5"} <= by_provider["openai"]
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"} <= by_provider["anthropic"]
    assert {"gemini-4-carbon", "gemini-4-hydrogen", "gemini-3.8-flash"} <= by_provider["gemini"]


def test_missing_api_key_falls_back_to_rule_summary(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    def _fail_if_called(*_args, **_kwargs):
        pytest.fail("call_llm must not run without credentials")

    monkeypatch.setattr("webdoctor.diagnostics.ai_engine.call_llm", _fail_if_called)
    result = generate_ai_diagnosis(_report_with_issue(), provider="anthropic")
    assert "AI diagnosis skipped" in result
    assert "ANTHROPIC_API_KEY" in result
    assert "Site health" in result


def test_configured_provider_uses_verified_default_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}

    def _capture(_prompt, **kwargs):
        captured.update(kwargs)
        return "AI diagnosis"

    monkeypatch.setattr("webdoctor.diagnostics.ai_engine.call_llm", _capture)
    result = generate_ai_diagnosis(_report_with_issue(), provider="anthropic")
    assert result == "AI diagnosis"
    assert captured["provider"] == "anthropic"
    assert captured["model"] == AI_MODEL_ANTHROPIC
