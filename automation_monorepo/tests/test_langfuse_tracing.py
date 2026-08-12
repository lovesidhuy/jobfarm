from __future__ import annotations

import os


def test_langfuse_is_fail_open_without_credentials(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "1")
    from core.observability.langfuse_tracing import trace_generation, tracing_enabled

    assert tracing_enabled() is False
    with trace_generation(
        name="test", model="test", provider="test", messages=[{"role": "user", "content": "x"}]
    ) as observation:
        assert observation is None


def test_langfuse_redacts_identifiers_and_bounds_payload():
    from core.observability.langfuse_tracing import redact_text, safe_messages

    value = "Contact jane@example.com or +1 (604) 555-0123. " + "x" * 3000
    redacted = redact_text(value)
    assert "jane@example.com" not in redacted
    assert "555-0123" not in redacted
    assert len(redacted) <= 2000

    messages = safe_messages([{"role": "user", "content": value}])
    assert messages[0]["role"] == "user"
    assert "jane@example.com" not in messages[0]["content"]


def test_langfuse_generation_updates_usage(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    from core.observability import langfuse_tracing as tracing

    class Observation:
        def __init__(self):
            self.updates = []

        def update(self, **kwargs):
            self.updates.append(kwargs)

    observation = Observation()

    class Usage:
        prompt_tokens = 3
        completion_tokens = 5
        total_tokens = 8

    class Completion:
        usage = Usage()

    tracing.update_generation(observation, output="safe output", completion=Completion())
    assert observation.updates == [
        {"output": "safe output", "usage_details": {"input": 3, "output": 5, "total": 8}}
    ]
