"""Best-effort Sentry initialization for bot and supervisor processes.

Call ``init_sentry("<component>")`` once, as early as possible in the process.
No-ops when SENTRY_DSN is not configured or sentry-sdk is not installed, so
nothing changes on machines without it (dev Macs, CI).

Sentry installs a process-global excepthook at init time, so it keeps working
in the bot entrypoints even after they evict ``core.*`` from ``sys.modules``
to load the master-folder code.

Captures:
- Uncaught exceptions (process crashes) with full stack traces
- ``logging.error(...)`` and above as events (via the logging integration)

Deliberately NOT captured: handled per-job failures (those are metrics/events,
see core/event_log.py and core/datadog_metrics.py).
"""
from __future__ import annotations

import os

_initialized = False


def _resolve_dsn() -> str:
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if dsn:
        return dsn
    try:
        from jobbots.core.secret_manager import get_secret
        return (get_secret("SENTRY_DSN", "") or "").strip()
    except Exception:
        return ""


def init_sentry(component: str) -> bool:
    """Initialize Sentry. Returns True when actually enabled."""
    global _initialized
    if _initialized:
        return True

    dsn = _resolve_dsn()
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=(os.environ.get("SENTRY_ENVIRONMENT") or "prod").strip(),
            # Crash reporting only — no performance tracing (saves quota).
            traces_sample_rate=0.0,
            # Job descriptions/PII stay out of Sentry.
            send_default_pii=False,
        )
        sentry_sdk.set_tag("component", component)
        bot_name = (os.environ.get("BOT_NAME") or "").strip()
        if bot_name:
            sentry_sdk.set_tag("bot", bot_name)
        run_id = (os.environ.get("BOT_RUN_ID") or "").strip()
        if run_id:
            sentry_sdk.set_tag("run_id", run_id)
        _initialized = True
        return True
    except Exception:
        return False
