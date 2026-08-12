"""Discovery-engine telemetry: Datadog gauges + structured logging.

All metrics are best-effort — failures never crash the discovery run.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Generator

_log = logging.getLogger("discovery.metrics")


def _dd_gauge(metric: str, value: float, tags: list[str] | None = None) -> None:
    """Emit a Datadog gauge (best-effort)."""
    try:
        from jobbots.core.datadog_metrics import gauge
        gauge(metric, value, tags=tags or [])
    except Exception:
        pass


def record_jobs_found(provider: str, platform: str, count: int) -> None:
    _log.info("jobs_found provider=%s platform=%s count=%d", provider, platform, count)
    _dd_gauge("discovery.jobs_found", count, [f"provider:{provider}", f"platform:{platform}"])


def record_jobs_normalized(count: int) -> None:
    _log.info("jobs_normalized count=%d", count)
    _dd_gauge("discovery.jobs_normalized", count)


def record_jobs_deduplicated(before: int, after: int) -> None:
    removed = before - after
    _log.info("jobs_deduplicated before=%d after=%d removed=%d", before, after, removed)
    _dd_gauge("discovery.jobs_deduplicated", removed)


def record_screening_result(passed: int, rejected: int) -> None:
    _log.info("screening passed=%d rejected=%d", passed, rejected)
    _dd_gauge("discovery.jobs_screened_pass", passed)
    _dd_gauge("discovery.jobs_screened_reject", rejected)


def record_jobs_enqueued(count: int, created: int) -> None:
    _log.info("jobs_enqueued total=%d new=%d", count, created)
    _dd_gauge("discovery.jobs_enqueued", count)
    _dd_gauge("discovery.jobs_enqueued_new", created)


def record_provider_error(provider: str, error: str) -> None:
    _log.error("provider_error provider=%s error=%s", provider, error)
    _dd_gauge("discovery.provider_errors", 1, [f"provider:{provider}"])


@contextmanager
def timed_provider(provider_name: str) -> Generator[None, None, None]:
    """Context manager that records provider duration."""
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        _log.info("provider_duration provider=%s seconds=%.2f", provider_name, elapsed)
        _dd_gauge("discovery.provider_duration_seconds", elapsed, [f"provider:{provider_name}"])


@contextmanager
def timed_run() -> Generator[None, None, None]:
    """Context manager that records total discovery run duration."""
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        _log.info("discovery_run_duration seconds=%.2f", elapsed)
        _dd_gauge("discovery.run_duration_seconds", elapsed)
