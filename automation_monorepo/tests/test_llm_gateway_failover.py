"""Unit tests for Akash → OpenRouter LLM gateway chain (no network)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ci_env(monkeypatch):
    monkeypatch.setenv("BOT_NAME", "ci-smoke")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "")
    monkeypatch.setenv("DD_METRICS_ENABLED", "0")
    monkeypatch.delenv("SENTRY_DSN", raising=False)


def _patch_secrets(monkeypatch, mapping: dict[str, str]):
    """Force gateway secrets without Infisical / host .env leaking in."""
    import core.llm_backend.ai.llm_gateway as gw

    def _secret(name: str, default: str = "") -> str:
        return (mapping.get(name) or default or "").strip()

    monkeypatch.setattr(gw, "_secret", _secret)


def test_chain_akash_then_openrouter(monkeypatch):
    _patch_secrets(
        monkeypatch,
        {
            "AKASHML_API_KEY": "akash-test-key",
            "OPENROUTER_API_KEY": "or-test-key",
            "LLM_PROVIDER": "akashml",
            "LLM_FALLBACK_OPENROUTER": "1",
        },
    )
    from core.llm_backend.ai.llm_gateway import list_llm_gateway_chain, resolve_llm_gateway

    chain = list_llm_gateway_chain()
    assert len(chain) >= 2
    assert chain[0].provider in {"akashml", "bluesminds"}
    assert chain[1].provider == "openrouter"
    assert chain[1].base_url.startswith("https://openrouter.ai")
    assert resolve_llm_gateway().provider == chain[0].provider


def test_chain_openrouter_only(monkeypatch):
    _patch_secrets(
        monkeypatch,
        {
            "OPENROUTER_API_KEY": "or-only",
            "LLM_FALLBACK_OPENROUTER": "1",
        },
    )
    from core.llm_backend.ai.llm_gateway import list_llm_gateway_chain

    chain = list_llm_gateway_chain()
    assert chain
    assert chain[0].provider == "openrouter"


def test_disable_openrouter_fallback(monkeypatch):
    _patch_secrets(
        monkeypatch,
        {
            "AKASHML_API_KEY": "akash-test-key",
            "OPENROUTER_API_KEY": "or-test-key",
            "LLM_FALLBACK_OPENROUTER": "0",
        },
    )
    from core.llm_backend.ai.llm_gateway import list_llm_gateway_chain

    chain = list_llm_gateway_chain()
    assert chain
    assert chain[0].provider in {"akashml", "bluesminds"}
    assert all(g.provider != "openrouter" for g in chain)


def test_transient_error_detection():
    from core.llm_backend.ai.deepseekConnections import _is_transient_llm_error

    assert _is_transient_llm_error(TimeoutError("Request timed out"))
    assert _is_transient_llm_error(Exception("APITimeoutError: Request timed out."))
    assert _is_transient_llm_error(Exception("Error code: 429 rate limit"))
    assert _is_transient_llm_error(Exception("502 Bad Gateway"))
    assert not _is_transient_llm_error(Exception("invalid json schema"))


def test_batch_fail_open_explicit_it_ea(monkeypatch):
    """Test batch fail open decision on explicit IT Easy Apply."""
    from jobbots.core.shared_modules.indeed.gates import _batch_fail_open_decision

    ok = _batch_fail_open_decision(
        {
            "title": "IT Support Analyst",
            "company": "Acme",
            "location": "Vancouver, BC",
            "has_easy_apply": True,
            "card_text": "",
        }
    )
    assert ok is not None
    assert ok["decision"] == "PROCEED"

    no_ea = _batch_fail_open_decision(
        {
            "title": "IT Support Analyst",
            "company": "Acme",
            "has_easy_apply": False,
        }
    )
    assert no_ea is None

    non_it = _batch_fail_open_decision(
        {
            "title": "Customer Service Representative",
            "company": "Acme",
            "has_easy_apply": True,
        }
    )
    assert non_it is None