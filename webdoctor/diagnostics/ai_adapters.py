"""Unified multi-AI adapter for WebDoctor: OpenAI-compatible (Sandbox built-in),
Anthropic Claude, Google Gemini REST, and custom OpenAI-compatible endpoints."""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional
import requests

from webdoctor.config import AI_MODEL_FAST

log = logging.getLogger(__name__)

# Every ID below was verified against the provider's public model catalog on
# 2026-11-24. Retired/deprecated IDs are deliberately excluded.
BUILTIN_SANDBOX_MODELS = [
    {"id": "gpt-6-luna",          "provider": "sandbox",  "label": "GPT-6 Luna (OpenAI) -- fast, economical"},
    {"id": "gpt-6.1-sol",         "provider": "sandbox",  "label": "GPT-6.1 Sol (OpenAI) -- deep reasoning"},
    {"id": "gpt-6-sol",           "provider": "sandbox",  "label": "GPT-6 Sol (OpenAI)"},
    {"id": "gemini-3.8-flash",    "provider": "sandbox",  "label": "Gemini 3.8 Flash (Google via proxy)"},
]

POPULAR_MODELS = [
    {"id": "gpt-6.2-astra",        "provider": "openai",    "label": "GPT-6.2 Astra (OpenAI) -- most capable"},
    {"id": "gpt-6.2-sol",          "provider": "openai",    "label": "GPT-6.2 Sol (OpenAI) -- everyday work"},
    {"id": "gpt-6.1-terra",        "provider": "openai",    "label": "GPT-6.1 Terra (OpenAI) -- fast, efficient"},
    {"id": "gpt-5.5",              "provider": "openai",    "label": "GPT-5.5 (OpenAI) -- previous flagship"},
    {"id": "claude-fable-5-2",     "provider": "anthropic", "label": "Claude Fable 5.2 (Anthropic) -- deep reasoning"},
    {"id": "claude-opus-5-5",      "provider": "anthropic", "label": "Claude Opus 5.5 (Anthropic)"},
    {"id": "claude-sonnet-5-5",    "provider": "anthropic", "label": "Claude Sonnet 5.5 (Anthropic) -- balanced"},
    {"id": "claude-haiku-5-5",     "provider": "anthropic", "label": "Claude Haiku 5.5 (Anthropic) -- fastest"},
    {"id": "gemini-4-carbon",      "provider": "gemini",    "label": "Gemini 4 Carbon (Google) -- strongest reasoning"},
    {"id": "gemini-4-neon",        "provider": "gemini",    "label": "Gemini 4 Neon (Google)"},
    {"id": "gemini-4-hydrogen",    "provider": "gemini",    "label": "Gemini 4 Hydrogen (Google) -- efficient"},
    {"id": "gemini-4-argon",       "provider": "gemini",    "label": "Gemini 4 Argon (Google) -- fast frontier"},
    {"id": "gemini-3.8-flash",     "provider": "gemini",    "label": "Gemini 3.8 Flash (Google) -- previous generation"},
]

# Sandbox proxy models that accept a low reasoning effort.
REASONING_EFFORT_MODELS = {
    "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-terra",
    "gpt-6.2-sol", "gpt-6.2-astra", "gemini-3.8-flash",
}


def list_available_models() -> List[Dict[str, str]]:
    return BUILTIN_SANDBOX_MODELS + POPULAR_MODELS


def call_llm(
    prompt: str,
    system: str = "You are a senior web reliability engineer. Be precise, technical, concise.",
    provider: str = "sandbox",
    model: str = AI_MODEL_FAST,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    max_tokens: int = 2200,
) -> str:
    """Call any configured LLM provider and return markdown text."""
    provider = provider.lower()

    if provider == "sandbox":
        return _call_sandbox_openai(prompt, system, model, max_tokens)
    if provider == "openai":
        return _call_direct_openai(prompt, system, model, api_key, api_base, max_tokens)
    if provider in ("anthropic", "claude"):
        return _call_anthropic(prompt, system, model, api_key, max_tokens)
    if provider == "gemini":
        return _call_gemini(prompt, system, model, api_key, max_tokens)
    raise ValueError(f"Unknown AI provider '{provider}'. Use sandbox|openai|anthropic|gemini.")


def _call_sandbox_openai(prompt: str, system: str, model: str, max_tokens: int) -> str:
    from openai import OpenAI
    client = OpenAI()
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_tokens,
    }
    if model in REASONING_EFFORT_MODELS:
        kwargs["reasoning_effort"] = "low"
    for _ in range(2):
        try:
            resp = client.chat.completions.create(**kwargs)
            content = (resp.choices[0].message.content or "").strip()
            if content:
                return content
        except Exception as e:
            last_err = e
    return f"*(diagnosis unavailable: {last_err if 'last_err' in locals() else 'empty response'})*"


def _call_direct_openai(prompt: str, system: str, model: str, api_key: Optional[str],
                        api_base: Optional[str], max_tokens: int) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key or None,
        base_url=api_base or "https://api.openai.com/v1",
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def _call_anthropic(prompt: str, system: str, model: str, api_key: Optional[str],
                    max_tokens: int) -> str:
    key = api_key or ""
    if not key:
        raise ValueError("Anthropic Claude requires an API key (ANTHROPIC_API_KEY).")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    texts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(texts).strip()


def _call_gemini(prompt: str, system: str, model: str, api_key: Optional[str],
                 max_tokens: int) -> str:
    key = api_key or ""
    if not key:
        raise ValueError("Google Gemini requires an API key (GEMINI_API_KEY).")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        headers={"content-type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    parts = []
    for cand in data.get("candidates", []):
        for p in cand.get("content", {}).get("parts", []):
            parts.append(p.get("text", ""))
    return "\n".join(parts).strip()
