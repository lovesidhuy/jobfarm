"""Shared LLM gateway resolution: Akash ML first, OpenRouter fallback.

The answer brain stays provider-agnostic. This module only picks transport
(base URL + API key + model name) from env / Infisical.

Resilience: ``list_llm_gateway_chain`` returns every configured transport in
priority order so callers can fail over (timeout/429/5xx) without zeroing
discovery volume.
"""
from __future__ import annotations

import os
from typing import NamedTuple


class LlmGateway(NamedTuple):
    provider: str
    base_url: str
    api_key: str
    model: str


def _secret(name: str, default: str = "") -> str:
    try:
        from jobbots.core.secret_manager import get_secret

        val = (get_secret(name, default) or default or "").strip()
    except Exception:
        val = (default or "").strip()
    if not val:
        val = (os.getenv(name) or default or "").strip()
    return val


def _akash_gateway(default_model: str = "deepseek-v4-flash") -> LlmGateway | None:
    bluesminds_key = (
        _secret("BLUESMINDS_API_KEY")
        or _secret("AKASHML_API_KEY")
        or ""
    ).strip()
    if not bluesminds_key:
        return None
    base_url = (
        _secret("AKASHML_BASE_URL")
        or _secret("BLUESMINDS_BASE_URL")
        or "https://api.akashml.com/v1"
    ).rstrip("/")
    model = (
        _secret("AKASHML_MODEL")
        or _secret("BLUESMINDS_MODEL")
        or default_model
        or "deepseek-ai/DeepSeek-V4-Flash"
    )
    low = model.lower().strip()
    if low in {"deepseek-v4-flash", "deepseek-v4", "v4-flash", "deepseek-flash"}:
        model = "deepseek-ai/DeepSeek-V4-Flash"
    label = "akashml" if "akashml.com" in base_url else "bluesminds"
    return LlmGateway(label, base_url, bluesminds_key, model)


def _openrouter_gateway(secrets_llm_model: str = "") -> LlmGateway | None:
    openrouter_key = _secret("OPENROUTER_API_KEY")
    if not openrouter_key:
        return None
    model = (secrets_llm_model or _secret("OPENROUTER_MODEL") or "deepseek/deepseek-chat").strip()
    if "/" not in model:
        if "reasoner" in model or "r1" in model:
            model = "deepseek/deepseek-r1"
        else:
            model = "deepseek/deepseek-chat"
    return LlmGateway(
        "openrouter",
        "https://openrouter.ai/api/v1",
        openrouter_key,
        model,
    )


def _deepseek_official_gateway(secrets_llm_model: str = "") -> LlmGateway | None:
    deepseek_key = _secret("DEEPSEEK_API_KEY")
    if not deepseek_key:
        return None
    model = (secrets_llm_model or "deepseek-chat").strip()
    return LlmGateway("deepseek", "https://api.deepseek.com", deepseek_key, model)


def _groq_gateway(secrets_llm_model: str = "") -> LlmGateway | None:
    groq_key = _secret("GROQ_API_KEY")
    if not groq_key:
        return None
    model = (secrets_llm_model or _secret("GROQ_MODEL") or "llama-3.3-70b-versatile").strip()
    return LlmGateway("groq", "https://api.groq.com/openai/v1", groq_key, model)


def _openai_gateway(secrets_llm_model: str = "") -> LlmGateway | None:
    openai_key = _secret("OPENAI_API_KEY")
    if not openai_key:
        return None
    model = (secrets_llm_model or _secret("OPENAI_MODEL") or "gpt-4o-mini").strip()
    base_url = (_secret("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    return LlmGateway("openai", base_url, openai_key, model)


def _gemini_gateway(secrets_llm_model: str = "") -> LlmGateway | None:
    gemini_key = _secret("GEMINI_API_KEY")
    if not gemini_key:
        return None
    model = (secrets_llm_model or _secret("GEMINI_MODEL") or "gemini-1.5-flash").strip()
    base_url = (_secret("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai/").rstrip("/")
    return LlmGateway("gemini", base_url, gemini_key, model)


def _ollama_gateway(secrets_llm_model: str = "") -> LlmGateway | None:
    base_url = (_secret("OLLAMA_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
    model = (secrets_llm_model or _secret("OLLAMA_MODEL") or "llama3.2").strip()
    return LlmGateway("ollama", base_url, "not-needed", model)


def list_llm_gateway_chain(
    *,
    default_model: str = "deepseek-v4-flash",
    secrets_llm_model: str = "",
) -> list[LlmGateway]:
    """Ordered failover chain for resilient completions.

    Configurable via LLM_PROVIDER in env or Infisical:
      - 'ollama' / 'local' -> local Ollama first
      - 'openai' -> official OpenAI first
      - 'gemini' -> Google Gemini first
      - 'groq' -> fast Groq inference first
      - 'deepseek' -> official DeepSeek first
      - 'openrouter' / 'openrouter_force' -> OpenRouter first
      - 'akashml' / 'bluesminds' (default) -> Akash ML free tier with OpenRouter fallback
    """
    configured = (_secret("LLM_PROVIDER") or "").strip().lower()
    allow_or = (_secret("LLM_FALLBACK_OPENROUTER") or "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    akash = _akash_gateway(default_model)
    openrouter = _openrouter_gateway(secrets_llm_model) if allow_or else None
    deepseek = _deepseek_official_gateway(secrets_llm_model)
    groq = _groq_gateway(secrets_llm_model)
    openai = _openai_gateway(secrets_llm_model)
    gemini = _gemini_gateway(secrets_llm_model)
    ollama = _ollama_gateway(secrets_llm_model)

    chain: list[LlmGateway] = []
    seen: set[tuple[str, str]] = set()

    def _add(gw: LlmGateway | None) -> None:
        if not gw or not gw.api_key:
            return
        if gw.api_key in {"YOUR_API_KEY", "changeme"}:
            return
        key = (gw.provider, gw.base_url)
        if key in seen:
            return
        seen.add(key)
        chain.append(gw)

    if configured in {"ollama", "local"}:
        _add(ollama)
        _add(akash)
        _add(openrouter)
        _add(deepseek)
    elif configured == "openai":
        _add(openai)
        _add(gemini)
        _add(openrouter)
        _add(deepseek)
    elif configured == "gemini":
        _add(gemini)
        _add(openai)
        _add(openrouter)
        _add(deepseek)
    elif configured == "groq":
        _add(groq)
        _add(openrouter)
        _add(deepseek)
    elif configured in {"openrouter", "openrouter_force"}:
        _add(openrouter)
        _add(deepseek)
        _add(akash)
    elif configured == "deepseek":
        _add(deepseek)
        _add(openrouter)
        _add(akash)
    else:
        # Default prioritized chain
        _add(akash)
        _add(openrouter)
        _add(deepseek)
        _add(groq)
        _add(openai)
        _add(gemini)

    if not chain:
        # Last resort: secrets.py custom / Ollama-style endpoint
        try:
            from config.secrets import llm_api_key, llm_api_url, llm_model  # type: ignore

            fallback_key = (_secret("LLM_API_KEY") or llm_api_key or "not-needed").strip()
            base = (llm_api_url or "http://localhost:11434/v1").rstrip("/")
            model = (secrets_llm_model or llm_model or default_model).strip()
            _add(LlmGateway("custom", base, fallback_key, model))
        except Exception:
            _add(ollama)
        if not chain:
            _add(ollama)
    return chain


def resolve_llm_gateway(
    *,
    default_model: str = "deepseek-v4-flash",
    secrets_llm_model: str = "",
) -> LlmGateway:
    """Resolve the primary OpenAI-compatible gateway (first in failover chain)."""
    chain = list_llm_gateway_chain(
        default_model=default_model,
        secrets_llm_model=secrets_llm_model,
    )
    if chain:
        return chain[0]
    return LlmGateway("custom", "http://localhost:11434/v1", "not-needed", default_model)

