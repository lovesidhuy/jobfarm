"""Shared Cloudflare / CapMonster env defaults."""
from __future__ import annotations

import os
from typing import MutableMapping


def is_nstbrowser_vendor(env: MutableMapping[str, str] | None = None) -> bool:
    target = env if env is not None else os.environ
    return (target.get("BROWSER_VENDOR") or "").strip().lower() in ("nstbrowser", "nst")


def apply_standard_captcha_env(env: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    """
    Indeed IT/Nstbrowser Cloudflare path:
      CapMonster cf_clearance via the configured browser proxy.

    Uses setdefault so .env / parent shell overrides still win.
    """
    target = os.environ if env is None else env

    defaults = {
        "DISABLE_GUI_CAPTCHA": "1",
        "CAPTCHA_ALLOW_GUI_FALLBACK": "0",
        "CAPTCHA_ALLOW_MANUAL_FALLBACK": "0",
        "CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE": "1",
        "CAPTCHA_CF_CAPMONSTER_RETRIES": "2",
        "CAPTCHA_CF_SKIP_RELOAD": "1",
        "CAPTCHA_CF_PATIENT_WAIT": "3",
        "BYPASS_PROXY": "0",
        "USE_CAPMONSTER_CAPTCHA_SOLVER": "1",
        "CAPTCHA_USE_CAPMONSTER": "1",
        "CAPTCHA_CLOUDFLARE_SOLVER": "capmonster",
        "CAPTCHA_CAPMONSTER_PROXYLESS_FALLBACK": "0",
    }
    for key, value in defaults.items():
        target.setdefault(key, value)
    return target


def apply_standard_captcha_env_overwrite(env: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    """Force captcha env (login loops / tests)."""
    target = os.environ if env is None else env
    target.update(
        {
            "DISABLE_GUI_CAPTCHA": "1",
            "CAPTCHA_ALLOW_GUI_FALLBACK": "0",
            "CAPTCHA_ALLOW_MANUAL_FALLBACK": "0",
            "CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE": "1",
            "CAPTCHA_CF_CAPMONSTER_RETRIES": "2",
            "CAPTCHA_CF_SKIP_RELOAD": "1",
            "CAPTCHA_CF_PATIENT_WAIT": "3",
            "BYPASS_PROXY": "0",
            "USE_CAPMONSTER_CAPTCHA_SOLVER": "1",
            "CAPTCHA_USE_CAPMONSTER": "1",
            "CAPTCHA_CLOUDFLARE_SOLVER": "capmonster",
            "CAPTCHA_CAPMONSTER_PROXYLESS_FALLBACK": "0",
        }
    )
    return target


def captcha_bootstrap_message(env: MutableMapping[str, str] | None = None) -> str:
    target = os.environ if env is None else env
    vendor = (target.get("BROWSER_VENDOR") or "chrome").strip().lower()
    nst_id = (target.get("NSTBROWSER_PROFILE_ID") or "").strip()
    message = (
        "[Bootstrap] CF path: CapMonster cf_clearance via browser proxy "
        "(no SeleniumBase GUI, pyautogui, or manual fallback)."
    )
    if vendor in ("nstbrowser", "nst") and nst_id:
        return f"[Bootstrap] Nstbrowser profile: {nst_id}\n{message}"
    return message
