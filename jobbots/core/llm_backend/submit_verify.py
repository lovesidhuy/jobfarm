"""
core.submit_verify — wait + verify helpers for terminal DOM actions.

Use cases (corpus-driven):
  - SmartApply submit verification: 6 jobs in the General corpus failed with
    "Apply clicked but no redirect detected" because the original poll only
    waited 5s. The wait predicates here are extensible and the default is 12s.
  - Indeed Save verification: the legacy `_save_job_on_indeed` returned
    `is_saved_or_True` which always claimed success. `verify_state` actually
    re-reads the DOM state after a click.

Pure-Python module — accepts callables instead of DOM types so it stays
framework-neutral and trivially unit-testable.

Public API
----------
    SubmitResult(ok, reason, evidence)
    wait_for(predicate, *, timeout=12.0, interval=0.5, on_tick=None)
    verify_submit(predicates, *, timeout=12.0)
    verify_state(predicate, *, timeout=4.0)
    retry_action(click_fn, verify_fn, *, max_attempts=2, between=0.6)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class SubmitResult:
    ok: bool
    reason: str = ""
    evidence: str = ""

    @classmethod
    def success(cls, reason: str = "ok", evidence: str = "") -> "SubmitResult":
        return cls(ok=True, reason=reason, evidence=evidence)

    @classmethod
    def failure(cls, reason: str, evidence: str = "") -> "SubmitResult":
        return cls(ok=False, reason=reason, evidence=evidence)


def wait_for(predicate: Callable[[], Optional[str]],
             *,
             timeout: float = 12.0,
             interval: float = 0.5,
             on_tick: Optional[Callable[[float], None]] = None) -> Optional[str]:
    """
    Poll `predicate` every `interval` seconds for up to `timeout` seconds.

    `predicate` returns:
      - a non-empty string  → success; the string is the success reason
      - None or ""          → keep waiting

    Returns the first non-empty string seen, or None on timeout. Any exception
    raised by `predicate` is swallowed (treated as "not yet").
    """
    deadline = time.time() + max(0.0, timeout)
    elapsed = 0.0
    while True:
        try:
            out = predicate()
        except Exception:
            out = None
        if out:
            return out
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        if on_tick:
            try:
                on_tick(elapsed)
            except Exception:
                pass
        sleep_for = min(interval, remaining)
        time.sleep(max(0.05, sleep_for))
        elapsed += sleep_for


def verify_submit(predicates: Iterable[tuple[str, Callable[[], bool]]],
                  *,
                  timeout: float = 12.0,
                  interval: float = 0.5) -> SubmitResult:
    """
    Wait until *any* labelled predicate returns True.

    `predicates` is an iterable of (label, fn) — when one returns truthy, the
    label is reported as the success reason.
    """
    preds = list(predicates)
    if not preds:
        return SubmitResult.failure("no_predicates")

    def _any() -> Optional[str]:
        for label, fn in preds:
            try:
                if fn():
                    return label
            except Exception:
                continue
        return None

    label = wait_for(_any, timeout=timeout, interval=interval)
    if label:
        return SubmitResult.success(label)
    return SubmitResult.failure("timeout")


def verify_state(predicate: Callable[[], bool],
                 *,
                 timeout: float = 4.0,
                 interval: float = 0.25) -> SubmitResult:
    """
    Wait for a single boolean state to become True. Used after a click to
    confirm the DOM actually transitioned (Saved button shows "Saved", etc).
    """
    def _wrap() -> Optional[str]:
        try:
            return "ok" if predicate() else None
        except Exception:
            return None
    out = wait_for(_wrap, timeout=timeout, interval=interval)
    if out:
        return SubmitResult.success("verified")
    return SubmitResult.failure("verify_timeout")


def retry_action(click_fn: Callable[[], bool],
                 verify_fn: Callable[[], bool],
                 *,
                 max_attempts: int = 2,
                 between: float = 0.6,
                 verify_timeout: float = 4.0) -> SubmitResult:
    """
    Click → verify, retry up to `max_attempts` total. Returns SubmitResult.

    `click_fn`  performs the click and returns True if a click happened.
    `verify_fn` returns True iff the desired post-click state holds.
    """
    last_reason = "unknown"
    for attempt in range(1, max_attempts + 1):
        try:
            clicked = bool(click_fn())
        except Exception as e:
            last_reason = f"click_error_attempt{attempt}:{type(e).__name__}"
            time.sleep(between)
            continue

        if not clicked:
            last_reason = f"click_returned_false_attempt{attempt}"
            time.sleep(between)
            continue

        v = verify_state(verify_fn, timeout=verify_timeout)
        if v.ok:
            return SubmitResult.success("verified", evidence=f"attempt={attempt}")
        last_reason = f"verify_timeout_attempt{attempt}"
        time.sleep(between)
    return SubmitResult.failure(last_reason)
