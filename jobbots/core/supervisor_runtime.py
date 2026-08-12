from __future__ import annotations

"""Shared helpers for supervisor / orchestrator subprocess launches."""

import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Infisical keys injected into every bot subprocess (master modules use os.getenv only).
BOT_SUBPROCESS_INFISICAL_SECRETS: tuple[str, ...] = (
    "PROXY_URL",
    "CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE",
    "CAPMONSTER_CLIENT_KEY",
    "CAPMONSTER_API_KEY",
    "capkey",
    "CAPMONSTER_PROXY_URL",
    "IXBROWSER_API_URL", "IXBROWSER_API_HOST", "IXBROWSER_API_PORT",
    "ADSPOWER_API_KEY", "ADSPOWER_API_URL", "ADSPOWER_ENABLED",
    "NSTBROWSER_API_URL", "NSTBROWSER_API_HOST", "NSTBROWSER_API_PORT", "NSTBROWSER_API_KEY",
    "BROWSER_VENDOR", "ADSPOWER_HEADLESS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)

PASSTHROUGH_ENV_KEYS: tuple[str, ...] = (
    "INDEED_BASE_URL",
    "GLASSDOOR_BASE_URL",
    "IMAP_OTP_MAX_WAIT_SECONDS",
    "PORTAL_MANUAL_LOGIN_TIMEOUT_MINUTES",
)


def merge_dotenv_into_env(env: dict, env_file: Path, override: bool = True) -> None:
    """Parse a ``.env`` file and assign into ``env`` using an explicit file path."""
    if not env_file.is_file():
        return
    with open(env_file, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                key = k.strip()
                if override or key not in env:
                    env[key] = v.strip().strip('"').strip("'")


def apply_supervised_bot_identity_env(env: dict, cfg: dict) -> None:
    """Set bot identity/profile values on a subprocess env dict."""
    env["BOT_NAME"] = cfg["bot_name"]
    env["CDP_PORT"] = str(cfg["cdp_port"])
    env["BOT_INSTANCE_ID"] = str(cfg.get("bot_instance_id", 0))
    env["CHROME_PROFILE_DIR"] = cfg["profile_dir"]
    env["JOB_PROFILE"] = cfg["profile"]


def resolve_bot_python(base_dir: Path) -> Path:
    """
    Python interpreter used to spawn bot scripts.

    Prefers, in order:
    - AUTOMATION_PYTHON (explicit path)
    - VIRTUAL_ENV/bin/python or python3
    - <base_dir>/.venv/bin/python or python3
    - sys.executable (whatever launched the supervisor — may miss deps)
    """
    explicit = os.environ.get("AUTOMATION_PYTHON", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv:
        subdir = "Scripts" if os.name == "nt" else "bin"
        names = ("python.exe", "python") if os.name == "nt" else ("python", "python3")
        for name in names:
            candidate = Path(venv) / subdir / name
            if candidate.is_file():
                return candidate

    subdir = "Scripts" if os.name == "nt" else "bin"
    names = ("python.exe", "python") if os.name == "nt" else ("python", "python3")
    for name in names:
        candidate = base_dir / ".venv" / subdir / name
        if candidate.is_file():
            return candidate

    return Path(sys.executable)


def apply_imap_env_for_profile(env: dict, profile: str) -> None:
    """
    Set IMAP_EMAIL / IMAP_APP_PASSWORD for the bot.

    Prefers profile-specific keys (IMAP_EMAIL_IT / IMAP_EMAIL_GENERAL and matching
    passwords). If those are empty, falls back to IMAP_EMAIL / IMAP_APP_PASSWORD
    so a single pair in .env still works for all bots.
    """
    prof = (profile or "").upper()
    if "IT" in prof:
        email = str(env.get("IMAP_EMAIL_IT", "")).strip()
        password = str(env.get("IMAP_APP_PASSWORD_IT", "")).strip()
    else:
        email = str(env.get("IMAP_EMAIL_GENERAL", "")).strip()
        password = str(env.get("IMAP_APP_PASSWORD_GENERAL", "")).strip()
    if not email:
        email = str(env.get("IMAP_EMAIL", "")).strip()
    if not password:
        password = str(env.get("IMAP_APP_PASSWORD", "")).strip()
    env["IMAP_EMAIL"] = email
    env["IMAP_APP_PASSWORD"] = password


def browser_vendor(env: dict | None = None) -> str:
    """Active browser vendor: ``chrome`` or ``nstbrowser``."""
    target = env if env is not None else os.environ
    raw = (target.get("BROWSER_VENDOR") or "nstbrowser").strip().lower()
    if raw in ("nstbrowser", "nst"):
        return "nstbrowser"
    return "chrome"


def resolve_nstbrowser_profile_id(
    bot_name: str,
    get_secret: Callable[[str, str], str],
) -> str:
    """Per-bot Nstbrowser profile ID (honors dual-account slot selection)."""
    try:
        from jobbots.core.browser.nst_accounts import resolve_profile_id

        _slot, pid, _key = resolve_profile_id(bot_name, get_secret=get_secret)
        return (pid or "").strip()
    except Exception:
        pass
    nst_profile_key = f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}"
    try:
        return (get_secret(nst_profile_key, "") or "").strip()
    except Exception:
        return ""


def apply_browser_vendor_profile_env(
    env: dict,
    cfg: dict,
    get_secret: Callable[[str, str], str],
) -> None:
    """Set NSTBROWSER_PROFILE_ID + API key for this bot (dual-slot aware)."""
    bot_name = cfg["bot_name"]
    vendor = browser_vendor(env)

    def _clear(k: str) -> None:
        env.pop(k, None)

    if vendor == "chrome":
        env["BROWSER_VENDOR"] = "chrome"
        _clear("NSTBROWSER_PROFILE_ID")
        return

    # Always write the resolved vendor — setdefault would leave an empty string
    # from the parent env dict in place, causing captcha env to see no vendor.
    env["BROWSER_VENDOR"] = vendor

    if vendor == "nstbrowser":
        try:
            from jobbots.core.browser.nst_accounts import apply_slot_to_env

            apply_slot_to_env(env, bot_name, get_secret=get_secret)
        except Exception as exc:
            print(f"[Supervisor] dual-NST slot resolve failed for {bot_name}: {exc}")
            nst_profile_id = resolve_nstbrowser_profile_id(bot_name, get_secret)
            if nst_profile_id:
                env["NSTBROWSER_PROFILE_ID"] = nst_profile_id
            else:
                _clear("NSTBROWSER_PROFILE_ID")


def apply_unattended_automation_env(env: dict) -> None:
    env["SKIP_USER_START"] = "1"
    env["AUTONOMOUS_SUPERVISOR"] = "1"
    env.setdefault("BROWSER_VENDOR", "nstbrowser")
    env.setdefault("RUN_IN_BACKGROUND", "0")
    from jobbots.core.captcha_runtime import apply_standard_captcha_env

    apply_standard_captcha_env(env)
    # Supervisor production path always prefers CapMonster for CF when unset.
    env.setdefault("CAPTCHA_CLOUDFLARE_SOLVER", "capmonster")


def inject_infisical_secrets_into_env(
    env: dict,
    get_secret: Callable[[str, str], str],
    secret_names: tuple[str, ...] = BOT_SUBPROCESS_INFISICAL_SECRETS,
    log_prefix: str = "[Supervisor]",
) -> None:
    for secret_name in secret_names:
        try:
            val = (get_secret(secret_name, "") or "").strip()
        except Exception as exc:
            print(f"{log_prefix} secret lookup failed for {secret_name}: {exc}")
            val = ""
        if val:
            env[secret_name] = val
    try:
        from jobbots.core.secret_manager import align_capmonster_proxy_env
        align_capmonster_proxy_env(env)
    except Exception:
        pass


def apply_utf8_stdio_env(env: dict) -> None:
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"


def apply_portal_url_env_defaults(env: dict, parent_environ: Optional[dict] = None) -> None:
    parent = parent_environ if parent_environ is not None else os.environ
    for key in PASSTHROUGH_ENV_KEYS:
        v = parent.get(key)
        if v:
            env[key] = v
    if not str(env.get("INDEED_BASE_URL", "")).strip():
        env["INDEED_BASE_URL"] = parent.get("INDEED_BASE_URL", "https://ca.indeed.com")
    if not str(env.get("GLASSDOOR_BASE_URL", "")).strip():
        env["GLASSDOOR_BASE_URL"] = parent.get(
            "GLASSDOOR_BASE_URL", "https://www.glassdoor.ca"
        )


def build_subprocess_env(
    cfg: dict,
    run_id: str,
    base_dir: Path,
    parent_environ: Optional[dict] = None,
    get_secret: Optional[Callable[[str, str], str]] = None,
) -> dict:
    """Environment passed to each bot subprocess (supervisor, orchestrator, smokes)."""
    if get_secret is None:
        from jobbots.core.secret_manager import get_secret as _get_secret

        get_secret = _get_secret

    parent = parent_environ if parent_environ is not None else os.environ
    env = parent.copy()

    merge_dotenv_into_env(env, base_dir / ".env")
    # Explicit parent/shell overrides win for local troubleshooting and CI smoke
    # jobs before any vendor-derived defaults are calculated.
    for key in (
        "BROWSER_VENDOR",
        "CAPTCHA_ALLOW_MANUAL_FALLBACK",
        "CAPTCHA_ALLOW_GUI_FALLBACK",
        "RUN_IN_BACKGROUND",
    ):
        if parent.get(key) is not None:
            env[key] = parent[key]

    apply_supervised_bot_identity_env(env, cfg)
    env["CURRENT_RUN_ID"] = run_id

    apply_browser_vendor_profile_env(env, cfg, get_secret)
    apply_imap_env_for_profile(env, cfg["profile"])
    apply_unattended_automation_env(env)
    inject_infisical_secrets_into_env(env, get_secret)
    apply_utf8_stdio_env(env)
    apply_portal_url_env_defaults(env, parent)

    # Headless Linux: child bots import pyautogui — need a display
    if not str(env.get("DISPLAY", "")).strip():
        env["DISPLAY"] = parent.get("DISPLAY") or ":99"
    env.setdefault("PYTHONUNBUFFERED", "1")

    # Explicit parent/shell overrides win for local troubleshooting (e.g.
    # ADSPOWER_HEADLESS=0) even when Infisical defaults to headless.
    # Only copy non-empty values so that ``BROWSER_VENDOR=""`` in the shell
    # does not clobber the nstbrowser default written above.
    for key in (
        "BROWSER_VENDOR",
        "CAPTCHA_ALLOW_MANUAL_FALLBACK",
        "CAPTCHA_ALLOW_GUI_FALLBACK",
        "RUN_IN_BACKGROUND",
    ):
        val = parent.get(key)
        if val is not None and str(val).strip():
            env[key] = val

    return env
