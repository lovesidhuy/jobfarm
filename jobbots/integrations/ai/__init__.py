"""AI integration — lazy facade over ``jobbots.core.llm_backend.ai``.

The frozen AI fallback chain (DeepSeek / Gemini / OpenAI / Ollama via the
llm gateway) lives once in the canonical core. Nothing here changes prompts,
providers, or fallback order.
"""
from __future__ import annotations

import importlib
from typing import Any

_MODULES = (
    "llm_gateway",
    "prompts",
    "deepseekConnections",
    "geminiConnections",
    "openaiConnections",
    "ollamaConnections",
)

__all__ = list(_MODULES)


def __getattr__(name: str) -> Any:
    if name in _MODULES:
        return importlib.import_module(f"jobbots.core.llm_backend.ai.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
