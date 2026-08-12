from __future__ import annotations

"""
Single source of truth for supervised bot identity: script, CDP port, Chrome profile
directory, and JOB_PROFILE / portal. Used by supervisor, orchestrator, login smokes,
and bot entrypoints so profile + port never drift between tools.

Edit `_BOT_ROWS` only when adding a bot or changing ports — everything else derives paths.
"""

import os
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any, TypedDict


class SupervisedBotRow(TypedDict, total=False):
    script: str
    bot_name: str
    cdp_port: str
    bot_instance_id: str
    browser_profile_subdir: str
    job_profile: str
    portal: str
    enabled: bool


# (script under bots/, bot_name, CDP port, BOT_INSTANCE_ID slot, folder under
# data/browser_profiles/, JOB_PROFILE, login portal)
_BOT_ROWS: tuple[SupervisedBotRow, ...] = (
    {
        "script": "indeed_it.py",
        "bot_name": "indeed_it",
        "cdp_port": "9222",
        "bot_instance_id": "0",
        "browser_profile_subdir": "indeed_it",
        "job_profile": "IT",
        "portal": "indeed",
    },
    {
        "script": "indeed_general.py",
        "bot_name": "indeed_general",
        "cdp_port": "9223",
        "bot_instance_id": "1",
        "browser_profile_subdir": "indeed_general",
        "job_profile": "General",
        "portal": "indeed",
        "enabled": True,  # Office/CS Easy Apply farm (separate from indeed_it).
    },
    {
        "script": "glassdoor_it.py",
        "bot_name": "glassdoor_it",
        "cdp_port": "9224",
        "bot_instance_id": "2",
        "browser_profile_subdir": "glassdoor_it",
        "job_profile": "IT",
        "portal": "glassdoor",
    },
    {
        "script": "glassdoor_general.py",
        "bot_name": "glassdoor_general",
        "cdp_port": "9225",
        "bot_instance_id": "3",
        "browser_profile_subdir": "glassdoor_general",
        "job_profile": "General",
        "portal": "glassdoor",
        "enabled": False,  # Paused intentionally; code/logging remains wired for manual runs.
    },
    {
        "script": "workopolis_it.py",
        "bot_name": "workopolis_it",
        "cdp_port": "9230",
        "bot_instance_id": "7",
        "browser_profile_subdir": "workopolis_it",
        "job_profile": "IT",
        "portal": "workopolis",
    },
    {
        "script": "workopolis_general.py",
        "bot_name": "workopolis_general",
        "cdp_port": "9231",
        "bot_instance_id": "8",
        "browser_profile_subdir": "workopolis_general",
        "job_profile": "General",
        "portal": "workopolis",
        "enabled": False,  # IT-only production lane.
    },
    {
        # Disabled: production uses ONE LinkedIn NST session (linkedin_general /
        # user@example.com) for both IT + office/CS Easy Apply.
        "script": "linkedin_it.py", "bot_name": "linkedin_it", "cdp_port": "9240",
        "bot_instance_id": "9", "browser_profile_subdir": "linkedin_it",
        "job_profile": "IT", "portal": "linkedin", "enabled": False,
    },
    {
        # Sole LinkedIn bot — IT + office/CS terms (see jobbots-discover-linkedin-general).
        "script": "linkedin_general.py", "bot_name": "linkedin_general", "cdp_port": "9241",
        "bot_instance_id": "10", "browser_profile_subdir": "linkedin_general",
        "job_profile": "General", "portal": "linkedin", "enabled": True,
    },
    # Google CDP discovers Greenhouse/Lever leads; application_worker routes
    # the approved ATS URLs to bots/google_it.py. Discovery needs this profile
    # in preflight so an expired Google login cannot enter a production cycle.
    {
        "script": "google_it.py",
        "bot_name": "google_it",
        "cdp_port": "9250",
        "bot_instance_id": "11",
        "browser_profile_subdir": "google_it",
        "job_profile": "IT",
        "portal": "google",
        "enabled": True,
    },
    {
        # Job Bank Direct Apply is a browser workflow.  It requires the
        # pre-provisioned, logged-in Webshare profile from runtime secrets.
        "script": "jobbank_it.py",
        "bot_name": "jobbank_it",
        "cdp_port": "9251",
        "bot_instance_id": "12",
        "browser_profile_subdir": "jobbank_it",
        "job_profile": "IT",
        "portal": "jobbank",
        "enabled": True,
    },
)

_DOTENV_CACHE: dict[str, str] | None = None


def _load_local_dotenv() -> dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None:
        return _DOTENV_CACHE
    values: dict[str, str] = {}
    env_path = monorepo_root() / ".env"
    try:
        if env_path.is_file():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        values = {}
    _DOTENV_CACHE = values
    return values


def _runtime_env_value(key: str) -> str:
    return (os.environ.get(key) or _load_local_dotenv().get(key) or "").strip()


def monorepo_root() -> Path:
    return _MONOREPO_ROOT


def get_profile_base_path() -> Path:
    """Get the base path for browser profiles.

    Resolution order:
      1. ``AUTOMATION_PROFILES_DIR`` env (or ``.env`` cache) — explicit override,
         e.g. ``D:\\automation\\profiles`` on a Windows server with a separate data drive.
      2. Windows default ``C:\\automation\\profiles``.
      3. POSIX default ``<monorepo>/data/browser_profiles``.

    NOTE: ``supervisor._kill_bot_chromes`` requires the path to contain the
    literal substring ``\\profiles\\`` (Windows) for its safety guard, so keep a
    ``profiles`` segment when overriding. Otherwise orphan-Chrome cleanup
    silently no-ops.
    """
    explicit = _runtime_env_value("AUTOMATION_PROFILES_DIR")
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()
    if os.name == "nt":  # Windows
        return Path("C:\\automation\\profiles")
    return monorepo_root() / "data" / "browser_profiles"


def supervised_bot_configs(
    base_dir: Path | str | None = None, *, include_disabled: bool = False
) -> list[dict[str, Any]]:
    """
    Full configs with absolute ``profile_dir`` and legacy ``profile`` alias (same as ``job_profile``).

    ``include_disabled=True`` returns paused bots too (audit/tooling only —
    production callers keep the default).
    """
    return _build_supervised_bot_configs(include_disabled=include_disabled)



def _build_supervised_bot_configs(include_disabled: bool) -> list[dict[str, Any]]:
    profiles = get_profile_base_path()
    out: list[dict[str, Any]] = []
    for row in _BOT_ROWS:
        if not include_disabled and not row.get("enabled", True):
            continue
        cfg: dict[str, Any] = dict(row)
        cfg["bot_instance_id"] = row.get("bot_instance_id", str(len(out) % 4))
        cfg["profile_dir"] = str(profiles / row["browser_profile_subdir"])
        cfg["profile"] = row["job_profile"]
        out.append(cfg)
    return out


def supervised_bot_config_by_name(bot_name: str, base_dir: Path | str | None = None) -> dict[str, Any]:
    for cfg in _build_supervised_bot_configs(include_disabled=True):
        if cfg["bot_name"] == bot_name:
            return cfg
    raise KeyError(f"Unknown supervised bot_name: {bot_name!r}")


def _infisical_value(key: str) -> str:
    """Look a single key up in Infisical (best-effort). Returns "" on failure."""
    try:
        from jobbots.core.secret_manager import get_secret  # local import: avoid cycles
        return (get_secret(key, "") or "").strip()
    except Exception:
        return ""


def _runtime_or_infisical(key: str) -> str:
    """Resolution order: ``os.environ`` → ``.env`` (via ``_runtime_env_value``)
    → Infisical. Used for per-bot keys (e.g. ``IXBROWSER_PROFILE_ID_INDEED_IT``)
    that we don't put into the global Infisical pull because there are 5+ of
    each per profile.
    """
    val = _runtime_env_value(key)
    return val or _infisical_value(key)





def _nstbrowser_profile_id_for(bot_name: str) -> str:
    """Resolve `NSTBROWSER_PROFILE_ID_<BOT>` to a profile id."""
    key = f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}"
    return _runtime_or_infisical(key)


def _browser_vendor() -> str:
    raw = (_runtime_env_value("BROWSER_VENDOR") or "nstbrowser").strip().lower()
    if raw in ("nstbrowser", "nst"):
        return "nstbrowser"
    return "chrome"


def _stamp_browser_profile_ids(bot_name: str, *, overwrite: bool = False) -> None:
    """Apply NSTBROWSER_PROFILE_ID to os.environ (vendor preference)."""
    vendor = _browser_vendor()

    if vendor == "chrome":
        os.environ["BROWSER_VENDOR"] = "chrome"
        os.environ.pop("NSTBROWSER_PROFILE_ID", None)
        return

    from jobbots.core.browser.nst_accounts import resolve_profile_id, resolve_api_key
    try:
        resolved_slot, resolved_pid, _ = resolve_profile_id(bot_name)
        _, resolved_key = resolve_api_key(slot=resolved_slot)
    except Exception:
        resolved_pid = ""
        resolved_key = ""
    nst_pid = resolved_pid or (os.environ.get("NSTBROWSER_PROFILE_ID") or "").strip()

    os.environ.setdefault("BROWSER_VENDOR", vendor)

    def _set(key: str, value: str) -> None:
        if overwrite:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    def _clear(key: str) -> None:
        os.environ.pop(key, None)

    if vendor == "nstbrowser":
        if nst_pid:
            _set("NSTBROWSER_PROFILE_ID", nst_pid)
            os.environ["NST_PROFILE_ID"] = nst_pid
            if resolved_key:
                os.environ["NSTBROWSER_API_KEY"] = resolved_key
                os.environ["NST_API_KEY"] = resolved_key
        else:
            from jobbots.core.browser.nst_profile_safety import nstbrowser_forbid_create

            if nstbrowser_forbid_create():
                from jobbots.core.browser.nst_profile_safety import require_existing_nst_profile_id

                require_existing_nst_profile_id(
                    "",
                    bot_name=bot_name,
                    env_key=f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}",
                )
            _clear("NSTBROWSER_PROFILE_ID")



_INFISICAL_RUNTIME_SECRETS = (
    # ── Egress proxy ────────────────────────────────────────────────────────
    # The Nstbrowser profile MUST be configured with the same proxy:
    # CapMonster's Cloudflare Turnstile token is bound to the IP that solved
    # the challenge, so the bot's egress IP and CapMonster's solver IP have
    # to match. Cloudflare otherwise rejects the token at submission.
    "PROXY_URL",
    "CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE",
    # ── CapMonster (Cloudflare Turnstile + Indeed reCAPTCHA solver) ────────
    "CAPMONSTER_CLIENT_KEY", "CAPMONSTER_API_KEY", "capkey",
    "CAPMONSTER_PROXY_URL",
    "BROWSER_VENDOR",
    "MONGODB_URI", "MONGO_URI", "MONGODB_PASSWORD",
    "MONGODB_DB_NAME", "USE_MONGODB", "MONGODB_ENABLED",
    "MONGODB_HISTORY_DB", "MONGODB_HISTORY_COLLECTION",
    "MONGODB_EVENTS_DB",
    # ── LLM providers (Groq is primary; others kept for rotation) ──────────
    "GROQ_API_KEY", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    # ── Region / portal base URLs (region-specific; treat as semi-secret) ──
    "INDEED_BASE_URL", "GLASSDOOR_BASE_URL",
    # ── IMAP (per-bot OTP for Indeed/Glassdoor 2FA) ────────────────────────
    # ``apply_imap_env_for_profile`` translates the per-bot keys into the
    # generic ``IMAP_EMAIL`` / ``IMAP_APP_PASSWORD`` the bots actually read.
    "IMAP_EMAIL", "IMAP_APP_PASSWORD",
    "IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT",
    "IMAP_EMAIL_GENERAL", "IMAP_APP_PASSWORD_GENERAL",
    # ── LinkedIn first-step auto-login (manual login still works without) ──
    # The per-bot keys are translated to the generic ``LINKEDIN_USERNAME`` /
    # ``LINKEDIN_EMAIL`` / ``LINKEDIN_PASSWORD`` by
    # ``apply_linkedin_creds_for_profile`` so monorepo's
    # ``core.portals.linkedin_live`` picks them up.
    "LINKEDIN_EMAIL", "LINKEDIN_USERNAME", "LINKEDIN_PASSWORD",
    "LINKEDIN_USERNAME_IT", "LINKEDIN_PASSWORD_IT",
    "LINKEDIN_USERNAME_GENERAL", "LINKEDIN_PASSWORD_GENERAL",
    # ── Telegram Alerts ────────────────────────────────────────────────────
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    # ── Datadog (agent install + metrics toggles) ──────────────────────────
    # DD_API_KEY is consumed by the agent installer (Ansible), not the bots.
    # Bots emit metrics via DogStatsD on localhost:8125, which needs no key.
    "DD_API_KEY", "DD_SITE", "DD_METRICS_ENABLED",
    # ── Sentry (crash reporting; see core/sentry_init.py) ──────────────────
    "SENTRY_DSN", "SENTRY_ENVIRONMENT",
)


_LOCAL_ENV_OVERRIDE_KEYS = frozenset({
    "BROWSER_VENDOR",
    "CAPTCHA_ALLOW_MANUAL_FALLBACK",
    "CAPTCHA_ALLOW_GUI_FALLBACK",
    "RUN_IN_BACKGROUND",
    "BYPASS_PROXY",
})


def _ensure_infisical_secrets_in_env(overwrite: bool = False) -> None:
    """Pull runtime secrets from Infisical and stamp them onto ``os.environ``
    so master-folder modules (which only read ``os.getenv`` / ``.env``) see
    them.

    Precedence: Infisical WINS for these keys (single source of truth across
    machines). When Infisical returns an empty value or is unreachable,
    whatever is already in ``os.environ`` (loaded from ``.env`` or parent
    shell) is preserved.

    The ``overwrite`` parameter is retained for backwards compatibility but
    no longer changes behavior: a non-empty Infisical value always wins, and
    an empty Infisical value never clobbers a real local value.

    Failure-tolerant: if ``core.secret_manager`` is unavailable, this is a
    no-op.
    """
    del overwrite  # kept in signature for backwards compatibility
    try:
        from jobbots.core.secret_manager import align_capmonster_proxy_env, get_secret  # local import: avoid cycles
    except Exception:
        return
    for name in _INFISICAL_RUNTIME_SECRETS:
        try:
            value = (get_secret(name, "") or "").strip()
        except Exception:
            value = ""
        # Only overwrite when Infisical actually returned a value; never
        # clobber a real local value with whitespace.
        if value:
            if name in _LOCAL_ENV_OVERRIDE_KEYS and os.environ.get(name):
                continue
            os.environ[name] = value
    align_capmonster_proxy_env()


def ensure_bot_runtime_defaults(bot_name: str, repo_root: Path | str | None = None) -> None:
    """
    ``os.environ.setdefault`` for BOT_NAME, CDP_PORT, CHROME_PROFILE_DIR, JOB_PROFILE.
    Call before importing ``config.settings`` / ``open_chrome`` so the supervisor and
    standalone ``python bots/foo.py`` agree on the same profile + port.

    Also exports ``IXBROWSER_PROFILE_ID`` when ``IXBROWSER_PROFILE_ID_<BOT>`` is
    set, which makes ``open_chrome`` attach to an ixBrowser profile instead of
    launching SeleniumBase UC, and pulls CapMonster / ixBrowser-API secrets
    from Infisical so ``modules/captcha_handler`` can solve Cloudflare Turnstile
    and reCAPTCHA without any GUI interaction.
    """
    cfg = supervised_bot_config_by_name(bot_name, repo_root)
    os.environ.setdefault("BOT_NAME", cfg["bot_name"])
    os.environ.setdefault("CDP_PORT", str(cfg["cdp_port"]))
    os.environ.setdefault("BOT_INSTANCE_ID", str(cfg["bot_instance_id"]))
    os.environ.setdefault("CHROME_PROFILE_DIR", cfg["profile_dir"])
    os.environ.setdefault("JOB_PROFILE", cfg["job_profile"])
    from jobbots.core.captcha_runtime import apply_standard_captcha_env

    _stamp_browser_profile_ids(cfg["bot_name"], overwrite=False)
    apply_standard_captcha_env(os.environ)
    _ensure_infisical_secrets_in_env(overwrite=False)


def apply_bot_runtime_env_overwrite(cfg: dict[str, Any]) -> None:
    """
    Force current-process env for login loops / tests (overwrites existing keys).
    Prefer this when iterating multiple bots in one Python process *before* each
    bot's portal login.
    """
    os.environ["BOT_NAME"] = cfg["bot_name"]
    os.environ["CDP_PORT"] = str(cfg["cdp_port"])
    os.environ["BOT_INSTANCE_ID"] = str(cfg["bot_instance_id"])
    os.environ["CHROME_PROFILE_DIR"] = cfg["profile_dir"]
    os.environ["JOB_PROFILE"] = cfg["job_profile"]
    from jobbots.core.captcha_runtime import apply_standard_captcha_env_overwrite

    _stamp_browser_profile_ids(cfg["bot_name"], overwrite=True)
    apply_standard_captcha_env_overwrite(os.environ)
