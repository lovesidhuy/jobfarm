"""Refresh ``email_applied_history`` from IMAP before Phase I screening.

Discovery already *reads* confirmation emails via ``IndeedSyncIndex``. This
module keeps that ledger fresh so callers never need a separate manual
``sync_imap_applied_data`` step.

Env
---
``DISCOVERY_REFRESH_EMAIL_HISTORY``
    Default ``1``. Set ``0`` / ``false`` / ``off`` to skip (tests / offline).
``DISCOVERY_EMAIL_SYNC_DAYS``
    How many days of IMAP mail to scan (default ``30``).
``DISCOVERY_EMAIL_REFRESH_HOURS``
    Min hours between full IMAP refreshes (default ``6``). Within the window
    we skip IMAP (ledger still used from Mongo). Set ``0`` to refresh every run.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

_log = logging.getLogger("discovery.email_history")


def email_refresh_enabled() -> bool:
    return os.getenv("DISCOVERY_REFRESH_EMAIL_HISTORY", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _stamp_path() -> Path:
    raw = (os.getenv("DISCOVERY_EMAIL_REFRESH_STAMP") or "").strip()
    if raw:
        return Path(raw)
    return _MONOREPO_ROOT / "artifacts" / "email_refresh.stamp"


def _min_interval_seconds() -> float:
    try:
        hours = float(os.getenv("DISCOVERY_EMAIL_REFRESH_HOURS", "6") or "6")
    except ValueError:
        hours = 6.0
    if hours <= 0:
        return 0.0
    return hours * 3600.0


def refresh_email_applied_history(*, days: int | None = None) -> dict[str, Any]:
    """Pull recent application-confirmation emails into Mongo + CSV.

    Best-effort: IMAP / secret failures are logged and never abort discovery.
    Returns a small stats dict for logs / dry-run diagnostics.
    """
    if not email_refresh_enabled():
        _log.info("Email applied-history refresh skipped (DISCOVERY_REFRESH_EMAIL_HISTORY off)")
        return {"skipped": True, "reason": "disabled"}

    interval = _min_interval_seconds()
    stamp = _stamp_path()
    if interval > 0 and stamp.is_file():
        try:
            age = time.time() - stamp.stat().st_mtime
            if age < interval:
                _log.info(
                    "Email applied-history refresh skipped (last %.1fh ago; min interval %.1fh)",
                    age / 3600.0, interval / 3600.0,
                )
                return {
                    "skipped": True,
                    "reason": "throttle",
                    "age_hours": round(age / 3600.0, 2),
                    "min_hours": interval / 3600.0,
                }
        except Exception as exc:
            _log.debug("email refresh stamp read failed: %s", exc)

    if days is None:
        try:
            days = int(os.getenv("DISCOVERY_EMAIL_SYNC_DAYS", "30") or "30")
        except ValueError:
            days = 30
    days = max(1, min(int(days), 180))

    try:
        from scripts.sync_imap_applied_data import (
            ACCOUNTS,
            fetch_confirmations_for_account,
            save_to_csv,
            save_to_mongodb,
        )
        from jobbots.core.secret_manager import get_secret
    except Exception as exc:
        _log.warning("Email history refresh unavailable (import): %s", exc)
        return {"skipped": True, "reason": f"import:{exc}"}

    imap_server = (get_secret("IMAP_SERVER", "imap.gmail.com") or "imap.gmail.com").strip()
    all_records: list[dict] = []
    per_account: dict[str, int] = {}

    for label, email_key, password_key in ACCOUNTS:
        email_addr = (get_secret(email_key, "") or "").strip()
        app_password = (get_secret(password_key, "") or "").strip()
        if not email_addr or not app_password:
            _log.info("Email refresh skip account=%s (missing credentials)", label)
            per_account[label] = 0
            continue
        try:
            records = fetch_confirmations_for_account(
                label, email_addr, app_password, imap_server, days,
            )
            per_account[label] = len(records)
            all_records.extend(records)
            _log.info(
                "Email refresh account=%s confirmations=%d (days=%d)",
                label, len(records), days,
            )
        except Exception as exc:
            _log.warning("Email refresh failed account=%s: %s", label, exc)
            per_account[label] = -1

    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(time.time()), encoding="utf-8")
    except Exception as exc:
        _log.debug("email refresh stamp write failed: %s", exc)

    if not all_records:
        return {
            "skipped": False,
            "days": days,
            "records": 0,
            "accounts": per_account,
        }

    try:
        saved = save_to_mongodb(all_records)
    except Exception as exc:
        _log.warning("Email history Mongo save failed: %s", exc)
        saved = 0

    try:
        monorepo = _MONOREPO_ROOT
        csv_path = monorepo / "all excels" / "email_applied_history.csv"
        save_to_csv(all_records, csv_path)
    except Exception as exc:
        _log.warning("Email history CSV save failed: %s", exc)

    stats = {
        "skipped": False,
        "days": days,
        "records": len(all_records),
        "mongo_upserts": saved,
        "accounts": per_account,
    }
    _log.info(
        "Email applied-history refreshed: records=%d mongo=%s accounts=%s",
        len(all_records), saved, per_account,
    )
    return stats
