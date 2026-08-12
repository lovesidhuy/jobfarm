"""
Persistent session status for supervised bots (Indeed / Glassdoor / LinkedIn).

Written when each bot confirms login (or fails). The main supervisor can read
``data/supervisor/session_registry.json`` to see which profiles last reported
an OK browser session — automation then reuses Chrome user-data dirs without
re-entering credentials when cookies are still valid.

Env:
  PORTAL_MANUAL_LOGIN_TIMEOUT_MINUTES — default 15; Indeed + Glassdoor manual wait.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT


def monorepo_root() -> Path:
    return _MONOREPO_ROOT


def _registry_path() -> Path:
    # Use the persistent VM profile directory when configured so session state
    # survives code syncs and is writable by the production service user.
    profiles_dir = (os.environ.get("AUTOMATION_PROFILES_DIR") or "").strip()
    if profiles_dir:
        d = Path(profiles_dir)
    else:
        srv_dir = Path("/srv/jobbots/browser_profiles")
        d = srv_dir if srv_dir.is_dir() else monorepo_root() / "data" / "supervisor"
    d.mkdir(parents=True, exist_ok=True)
    return d / "session_registry.json"


def load_session_registry() -> dict:
    p = _registry_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_session_registry(data: dict) -> None:
    p = _registry_path()
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_bot_session_ready(bot_name: str, *, portal: str = "") -> None:
    reg = load_session_registry()
    reg[bot_name] = {
        "session_ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "portal": portal,
    }
    _save_session_registry(reg)


def record_bot_session_not_ready(bot_name: str, *, reason: str = "") -> None:
    reg = load_session_registry()
    reg[bot_name] = {
        "session_ok": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }
    _save_session_registry(reg)


def format_registry_summary() -> str:
    lines = ["[Session registry] data/supervisor/session_registry.json"]
    reg = load_session_registry()
    if not reg:
        lines.append("  (no entries yet — each bot updates this after login check)")
        return "\n".join(lines)
    for name in sorted(reg.keys()):
        e = reg[name]
        ok = e.get("session_ok", False)
        mark = "ok" if ok else "not_ok"
        ts = e.get("updated_at", "?")
        portal = e.get("portal") or e.get("reason") or ""
        extra = f" ({portal})" if portal else ""
        lines.append(f"  {name}: {mark}{extra} @ {ts}")
    return "\n".join(lines)


def portal_manual_login_timeout_minutes() -> int:
    raw = (os.environ.get("PORTAL_MANUAL_LOGIN_TIMEOUT_MINUTES") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 15
