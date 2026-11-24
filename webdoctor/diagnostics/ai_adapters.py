"""Unified multi-AI adapter for WebDoctor: OpenAI-compatible (Sandbox built-in),
Anthropic Claude, Google Gemini REST, and custom OpenAI-compatible endpoints."""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional
import requests

from webdoctor.config import AI_MODEL_FAST

log = logging.getLogger(__name__)

BUILTIN_SANDBOX_MODELS = [
    {"id": "gpt-6-luna",          "provider": "sandbox",  "label": "GPT-6 Luna (OpenAI) -- fast, economical"},
    {"id": "gpt-6.1-sol",         "provider": "sandbox",  "label": "GPT-6.1 Sol (OpenAI) -- deep reasoning"},
    {"id": "gpt-6-sol",           "provider": "sandbox",  "label": "GPT-6 Sol (OpenAI)"},
    {"id": "gemini-3.8-flash",    "provider": "sandbox",  "label": "Gemini 3.8 Flash (Google via proxy)"},
    {"id": "gpt-5-mini",          "provider": "sandbox",  "label": "GPT-5 Mini"},
]

POPULAR_MODELS = [
    {"id": "claude-sonnet-4",      "provider": "anthropic", "label": "Claude Sonnet 4 (Anthropic, your API key)"},
    {"id": "claude-haiku-4",       "provider": "anthropic", "label": "Claude Haiku 4 (Anthropic, your API key)"},
    {"id": "gemini-2.5-flash",     "provider": "gemini",    "label": "Gemini 2.5 Flash (Google, your API key)"},
    {"id": "gemini-2.5-pro",       "provider": "gemini",    "label": "Gemini 2.5 Pro (Google, your API key)"},
    {"id": "gpt-5.5",              "provider": "openai",    "label": "GPT-5.5 (OpenAI direct, your API key)"},
]


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
    if model in ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol", "gemini-3.8-flash"):
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
