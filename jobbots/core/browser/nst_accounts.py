"""Dual NSTBrowser account support + soft quota rotation.

Two API keys (subscriptions) can hold parallel profile sets:

* Slot **1** — ``NSTBROWSER_API_KEY`` + ``NSTBROWSER_PROFILE_ID_{BOT}``
  (tested logins; often near daily open cap).
* Slot **2** — ``NSTBROWSER_API_KEY_2`` + ``NSTBROWSER_PROFILE_ID_2_{BOT}``
  (spare quota).

Selection
---------
``NSTBROWSER_ACTIVE_SLOT``:

* ``auto`` (default) — prefer slot 1 until soft quota, then slot 2
* ``1`` / ``2`` — force a slot

``NSTBROWSER_QUOTA_SOFT_LIMIT`` (default ``28``) — when
``NSTBROWSER_DAILY_OPENS_1`` ≥ limit, auto mode uses slot 2 if key+profiles exist.

Successful browser launches are tracked in ``artifacts/nst_quota_state.json``.
Set ``NSTBROWSER_DAILY_OPENS_1`` / ``_2`` in env/Infisical after checking the
dashboard when an authoritative count is available; those values take priority.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

_log = logging.getLogger("browser.nst_accounts")

REQUIRED_BOTS = (
    "indeed_it",
    "indeed_general",
    "glassdoor_it",          # IT only (no glassdoor_general in prod)
    "workopolis_it",         # IT only (no workopolis_general in prod)
    "linkedin_general",      # Sole LinkedIn bot (IT + office/CS on one NST session)
    "jobbank_it",            # Authenticated Direct Apply (Webshare static)
)

# Optional aliases / paused bots (profiles may exist; not required for preflight)
OPTIONAL_BOTS = (
    "glassdoor_general",
    "workopolis_general",
    "linkedin_it",           # superseded by linkedin_general
    "linkedin_discovery",
    "linkedin_discovery_it",
)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def env_key_for_bot(bot_name: str, *, slot: int = 1) -> str:
    bot = (bot_name or "").strip().upper()
    if slot <= 1:
        return f"NSTBROWSER_PROFILE_ID_{bot}"
    return f"NSTBROWSER_PROFILE_ID_2_{bot}"


def api_key_env_for_slot(slot: int) -> str:
    return "NSTBROWSER_API_KEY" if slot <= 1 else "NSTBROWSER_API_KEY_2"


def soft_quota_limit() -> int:
    try:
        return int(os.getenv("NSTBROWSER_QUOTA_SOFT_LIMIT", "28") or "28")
    except ValueError:
        return 28


def _state_path() -> Path:
    root = _MONOREPO_ROOT
    return root / "artifacts" / "nst_quota_state.json"


def _quota_day() -> str:
    """Return the day used by the local launch counter (UTC by default)."""
    # This override covers a provider dashboard with a different rollover day
    # without adding a timezone package to the worker image.
    explicit = (os.getenv("NSTBROWSER_QUOTA_DAY") or "").strip()
    if explicit:
        return explicit
    return datetime.now(timezone.utc).date().isoformat()


def load_quota_state() -> dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_quota_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def daily_opens_for_slot(slot: int) -> int | None:
    """Return known daily profile opens for a slot, or None if unknown."""
    env_key = f"NSTBROWSER_DAILY_OPENS_{slot}"
    raw = (os.getenv(env_key) or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    state = load_quota_state()
    if state.get("quota_day") != _quota_day():
        return None
    try:
        v = state.get(f"daily_opens_{slot}")
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def set_daily_opens(slot: int, opens: int, *, limit: int = 30) -> None:
    state = load_quota_state()
    state["quota_day"] = _quota_day()
    state[f"daily_opens_{slot}"] = int(opens)
    state[f"daily_limit_{slot}"] = int(limit)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_quota_state(state)
    # Also surface for this process
    os.environ[f"NSTBROWSER_DAILY_OPENS_{slot}"] = str(int(opens))


def record_profile_open(slot: int, *, limit: int | None = None) -> int:
    """Record one successful NST browser launch and return its observed count.

    This is a local observed-launch counter, not a claim that it is the
    provider dashboard's authoritative quota. An explicit environment count
    remains authoritative when an operator has checked the dashboard.
    """
    state = load_quota_state()
    if state.get("quota_day") != _quota_day():
        state = {"quota_day": _quota_day()}
    key = f"daily_opens_{int(slot)}"
    try:
        current = int(state.get(key, 0))
    except (TypeError, ValueError):
        current = 0
    observed = current + 1
    state[key] = observed
    state[f"daily_limit_{int(slot)}"] = int(limit or soft_quota_limit())
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_quota_state(state)
    return observed


def _default_secret_getter():
    """Env first, then Infisical/``.env`` via secret_manager (never raise)."""

    def getter(k: str, d: str = "") -> str:
        val = (os.getenv(k) or "").strip()
        if val:
            return val
        try:
            from jobbots.core.secret_manager import get_secret as _gs

            return (_gs(k, d) or d or "").strip()
        except Exception:
            return (d or "").strip()

    return getter


def slot_has_credentials(slot: int, get_secret=None) -> bool:
    getter = get_secret or _default_secret_getter()
    key = (getter(api_key_env_for_slot(slot), "") or "").strip()
    return bool(key)


def slot_has_profile(bot_name: str, slot: int, get_secret=None) -> bool:
    getter = get_secret or _default_secret_getter()
    pid = (getter(env_key_for_bot(bot_name, slot=slot), "") or "").strip()
    return bool(pid)


def choose_active_slot(*, bot_name: str = "", get_secret=None) -> int:
    """Pick slot 1 or 2 based on force flag + soft quota + profile availability."""
    getter = get_secret or _default_secret_getter()
    forced = (os.getenv("NSTBROWSER_ACTIVE_SLOT") or "auto").strip().lower()
    if forced in {"1", "primary", "a", "a1"}:
        return 1
    if forced in {"2", "secondary", "b", "a2"}:
        if slot_has_credentials(2, getter):
            return 2
        _log.warning("NSTBROWSER_ACTIVE_SLOT=2 but API key 2 missing; using slot 1")
        return 1

    # auto
    opens1 = daily_opens_for_slot(1)
    limit = soft_quota_limit()
    prefer_2 = opens1 is not None and opens1 >= limit
    if prefer_2 and slot_has_credentials(2, getter):
        if not bot_name or slot_has_profile(bot_name, 2, getter):
            _log.info(
                "NST auto-slot: primary opens=%s >= soft_limit=%s → slot 2",
                opens1, limit,
            )
            return 2
        _log.warning(
            "NST auto-slot wanted 2 (quota) but no profile for %s on slot 2; using 1",
            bot_name,
        )
    return 1


def resolve_api_key(*, slot: int | None = None, bot_name: str = "", get_secret=None) -> tuple[int, str]:
    """Return ``(slot, api_key)`` for the chosen account."""
    getter = get_secret or _default_secret_getter()
    if slot is None:
        slot = choose_active_slot(bot_name=bot_name, get_secret=getter)
    key_name = api_key_env_for_slot(slot)
    key = (getter(key_name, "") or "").strip()
    if not key and slot == 2:
        # Fall back to primary
        key = (getter("NSTBROWSER_API_KEY", "") or "").strip()
        slot = 1
    if not key:
        raise RuntimeError(
            f"Missing {key_name} (and no primary fallback). "
            "Set NSTBROWSER_API_KEY / NSTBROWSER_API_KEY_2 in Infisical."
        )
    return slot, key


def resolve_profile_id(
    bot_name: str,
    *,
    slot: int | None = None,
    get_secret=None,
) -> tuple[int, str, str]:
    """Return ``(slot, profile_id, env_key_used)``."""
    getter = get_secret or _default_secret_getter()
    if slot is None:
        slot = choose_active_slot(bot_name=bot_name, get_secret=getter)
    key = env_key_for_bot(bot_name, slot=slot)
    pid = (getter(key, "") or "").strip()
    if not pid and bot_name == "linkedin_general":
        alt_key = env_key_for_bot("linkedin_it", slot=slot)
        pid = (getter(alt_key, "") or "").strip()
        if pid:
            key = alt_key
    if not pid and slot == 2:
        # Fall back to primary profile map (same machine agent may still open
        # primary profiles only with primary key — prefer empty over wrong).
        key1 = env_key_for_bot(bot_name, slot=1)
        pid1 = (getter(key1, "") or "").strip()
        if pid1 and not slot_has_credentials(2, getter):
            return 1, pid1, key1
    if not pid:
        # Last resort: generic NSTBROWSER_PROFILE_ID
        generic = (getter("NSTBROWSER_PROFILE_ID", "") or "").strip()
        if generic:
            return slot, generic, "NSTBROWSER_PROFILE_ID"
        raise RuntimeError(
            f"Missing NST profile id for bot={bot_name} slot={slot} ({key}). "
            "Run scripts/sync_nst_dual_accounts.py after logging into the dashboard."
        )
    return slot, pid, key


def apply_slot_to_env(env: dict, bot_name: str, *, get_secret=None) -> dict:
    """Mutate env with the active API key + profile id for ``bot_name``."""
    from jobbots.core.secret_manager import get_secret as _gs

    getter = get_secret or (lambda k, d="": env.get(k) or os.getenv(k) or _gs(k, d) or "")
    slot, api_key = resolve_api_key(bot_name=bot_name, get_secret=getter)
    slot, pid, used_key = resolve_profile_id(bot_name, slot=slot, get_secret=getter)
    env["NSTBROWSER_ACTIVE_SLOT"] = str(slot)
    env["NSTBROWSER_API_KEY"] = api_key  # local agent always reads this header key
    env["NSTBROWSER_PROFILE_ID"] = pid
    env[env_key_for_bot(bot_name, slot=1) if slot == 1 else env_key_for_bot(bot_name, slot=2)] = pid
    # Keep per-bot key workers already look up
    env[f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}"] = pid
    env["_NST_RESOLVED_SLOT"] = str(slot)
    env["_NST_RESOLVED_PROFILE_ENV"] = used_key
    return env


def profile_map_from_env(*, slot: int = 1, get_secret=None) -> dict[str, str]:
    getter = get_secret or (lambda k, d="": os.getenv(k, d) or "")
    out: dict[str, str] = {}
    for bot in list(REQUIRED_BOTS) + list(OPTIONAL_BOTS):
        pid = (getter(env_key_for_bot(bot, slot=slot), "") or "").strip()
        if pid:
            out[bot] = pid
    return out
