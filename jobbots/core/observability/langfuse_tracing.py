"""Fail-open Langfuse tracing for production LLM calls.

Tracing is deliberately lazy and optional: a missing SDK, incomplete
credentials, or Langfuse outage must never stop job discovery or applying.
Inputs are redacted and bounded because prompts can contain candidate data.
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any, Iterator


_MAX_TEXT = 2000
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_SECRET_RE = re.compile(r"\b(?:sk|pk)-lf-[A-Za-z0-9-]+\b|\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b")


def tracing_enabled() -> bool:
    """Return whether the optional integration is configured."""
    if os.getenv("LANGFUSE_TRACING_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def redact_text(value: Any, limit: int = _MAX_TEXT) -> str:
    """Redact common direct identifiers and cap prompt/response size."""
    text = str(value if value is not None else "")
    text = _SECRET_RE.sub("[REDACTED_SECRET]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    if len(text) > limit:
        return text[: max(0, limit - 16)] + "...[truncated]"
    return text


def safe_messages(messages: list[dict] | None) -> list[dict]:
    """Keep only useful, bounded message fields for observability."""
    result: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        result.append(
            {
                "role": redact_text(message.get("role", ""), 32),
                "content": redact_text(message.get("content", "")),
            }
        )
    return result


def _usage_details(completion: Any) -> dict[str, int] | None:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return None
    values = {
        "input": getattr(usage, "prompt_tokens", None),
        "output": getattr(usage, "completion_tokens", None),
        "total": getattr(usage, "total_tokens", None),
    }
    return {key: int(value) for key, value in values.items() if value is not None}


def update_generation(observation: Any, *, output: Any = None, completion: Any = None) -> None:
    """Attach bounded output and usage without allowing telemetry to fail work."""
    if observation is None:
        return
    try:
        payload: dict[str, Any] = {"output": redact_text(output)}
        usage = _usage_details(completion)
        if usage:
            payload["usage_details"] = usage
        observation.update(**payload)
    except Exception:
        return


@contextlib.contextmanager
def trace_generation(
    *,
    name: str,
    model: str,
    provider: str,
    messages: list[dict] | None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a Langfuse generation when configured; otherwise yield None."""
    if not tracing_enabled():
        yield None
        return

    try:
        from langfuse import get_client

        client = get_client()
        observation_context = client.start_as_current_observation(
            as_type="generation",
            name=name,
            input=safe_messages(messages),
            model=redact_text(model, 120),
            metadata={
                "provider": redact_text(provider, 120),
                "environment": os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "production"),
                **(metadata or {}),
            },
        )
        observation = observation_context.__enter__()
    except Exception:
        # Telemetry setup is non-critical. The wrapped application call
        # continues when the SDK is absent, disabled, or temporarily down.
        yield None
        return

    try:
        yield observation
    except Exception as exc:
        try:
            observation.update(
                level="ERROR",
                status_message=redact_text(f"{exc.__class__.__name__}: {exc}", 400),
            )
        except Exception:
            pass
        raise
    finally:
        try:
            observation_context.__exit__(None, None, None)
        except Exception:
            pass


def flush() -> None:
    """Flush short-lived workers without making shutdown fragile."""
    if not tracing_enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        return
