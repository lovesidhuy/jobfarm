from __future__ import annotations

"""Best-effort Datadog metrics via DogStatsD (local Datadog Agent, UDP 8125).

Metrics flow:  bot code → statsd (UDP, fire-and-forget) → Datadog Agent → Datadog.

Design constraints:
- NEVER raises and NEVER blocks: UDP send to localhost, no network round-trip.
- No-op when the ``datadog`` package is missing or ``DD_METRICS_ENABLED=0``,
  so bots run unchanged on machines without the agent (dev Macs, CI).
- The agent owns the API key; this module needs no secrets.

Metric naming convention:
    bot.applications        counter, tags: bot, portal, event (applied/skipped/failed/...)
    bot.heartbeat           gauge=1,  tags: bot, status
    supervisor.bot_exit     counter, tags: bot, outcome (clean/crash)
    supervisor.unhealthy    counter, tags: bot
"""
import os

_statsd = None
_resolved = False


def _enabled() -> bool:
    return os.environ.get("DD_METRICS_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _get_statsd():
    """Lazily resolve the DogStatsD client. Returns None when unavailable."""
    global _statsd, _resolved
    if _resolved:
        return _statsd
    _resolved = True
    if not _enabled():
        return None
    try:
        from datadog import DogStatsd
        _statsd = DogStatsd(
            host=os.environ.get("DD_AGENT_HOST", "127.0.0.1"),
            port=int(os.environ.get("DD_DOGSTATSD_PORT", "8125")),
            namespace="jobbots",
        )
    except Exception:
        _statsd = None
    return _statsd


def increment(metric: str, tags: list[str] | None = None, value: int = 1) -> None:
    client = _get_statsd()
    if client is None:
        return
    try:
        client.increment(metric, value=value, tags=tags or [])
    except Exception:
        pass


def gauge(metric: str, value: float, tags: list[str] | None = None) -> None:
    client = _get_statsd()
    if client is None:
        return
    try:
        client.gauge(metric, value=value, tags=tags or [])
    except Exception:
        pass
