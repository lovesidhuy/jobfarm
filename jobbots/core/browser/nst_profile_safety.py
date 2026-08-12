"""NSTBrowser profile-quota safety — never create profiles near hard caps.

With only a few NST profile slots left (e.g. 3 until a 30-profile quota),
apply/canary paths must reuse an existing ``NSTBROWSER_PROFILE_ID_*`` only.

Creation is refused unless ``NSTBROWSER_FORBID_CREATE=0`` (explicit opt-out).
Unset / ``1`` / truthy → forbid create (safe default).
"""
from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _falsy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off"}


def nstbrowser_forbid_create() -> bool:
    """True unless explicitly opted out with ``NSTBROWSER_FORBID_CREATE=0``."""
    raw = os.environ.get("NSTBROWSER_FORBID_CREATE")
    if raw is None or str(raw).strip() == "":
        return True  # safe default near quota
    if _falsy(raw):
        return False
    return _truthy(raw) or True


def refuse_profile_creation(*, context: str = "") -> None:
    """Raise if profile creation is forbidden (default)."""
    if not nstbrowser_forbid_create():
        return
    where = f" ({context})" if context else ""
    raise RuntimeError(
        "NSTBROWSER_FORBID_CREATE blocks creating new NST profiles"
        f"{where}. Quota is critical — reuse an existing profile ID only "
        "(e.g. NSTBROWSER_PROFILE_ID_INDEED_IT). To allow creation, set "
        "NSTBROWSER_FORBID_CREATE=0 explicitly."
    )


def require_existing_nst_profile_id(
    profile_id: str | None,
    *,
    bot_name: str = "",
    env_key: str = "",
) -> str:
    """Require a non-empty existing profile id; never invent or create one."""
    pid = (profile_id or "").strip()
    if pid:
        return pid
    key_hint = env_key or (
        f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}" if bot_name else "NSTBROWSER_PROFILE_ID"
    )
    raise RuntimeError(
        f"Missing existing NST profile id ({key_hint}). "
        "Refusing to open/create a browser without a configured profile. "
        "Set the env var to a known profile UUID — do not create a new profile "
        "(NSTBROWSER_FORBID_CREATE is on by default)."
    )


def portal_profile_bot_name(portal: str, profile: str) -> str:
    """Map queue portal+profile to NST/supervised bot name.

    LinkedIn is a single production bot (``linkedin_general`` / one Gmail NST
    session) for both IT and office/CS jobs — never dual ``linkedin_it``.
    """
    p = (portal or "").strip().lower()
    if p == "linkedin":
        return "linkedin_general"
    return f"{p}_{(profile or '').strip().lower()}"


def env_key_for_bot(bot_name: str) -> str:
    return f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}"


def resolve_configured_profile_id(bot_name: str) -> str:
    """Resolve stamped or per-bot env profile id (no creation)."""
    direct = (os.environ.get("NSTBROWSER_PROFILE_ID") or "").strip()
    if direct:
        return direct
    key = env_key_for_bot(bot_name)
    return (os.environ.get(key) or "").strip()
