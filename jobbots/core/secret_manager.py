from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT

try:
    from infisical_sdk import InfisicalSDKClient
except ImportError:
    InfisicalSDKClient = None  # type: ignore[misc, assignment]

# Load local .env manually as fallback - search multiple locations
_local_env = {}

def _find_and_load_env():
    """Search for .env file in multiple locations and load it."""
    global _local_env

    # Locations to search, in priority order
    search_paths = [
        Path.cwd() / ".env",                                      # Current working directory
        _MONOREPO_ROOT / ".env",         # Project root (automation_monorepo)
        Path.home() / ".env",                                     # User home directory
        Path("C:") / "automation" / "automation_monorepo" / ".env",  # Windows VM hardcoded path
    ]

    for env_path in search_paths:
        try:
            if env_path.exists():
                print(f"[SecretManager] Loading .env from: {env_path}")
                loaded = {}
                for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    k = key.strip()
                    v = val.strip().strip('"').strip("'")
                    loaded[k] = v
                    if k not in os.environ:
                        os.environ[k] = v
                _local_env = loaded
                return env_path
        except Exception as e:
            print(f"[SecretManager] Warning: Failed to load {env_path}: {e}")
            continue

    return None

_env_file_path = _find_and_load_env()
if not _env_file_path:
    print("[SecretManager] Warning: No .env file found in any search location")

def _get_local_env(name: str, default: str = "") -> str:
    if name.startswith("NSTBROWSER_PROFILE_ID_") and name in _local_env:
        return _local_env[name]
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    return _local_env.get(name, default)

INFISICAL_CLIENT_ID = _get_local_env("INFISICAL_CLIENT_ID", "")
PROJECT_ID = _get_local_env("INFISICAL_PROJECT_ID", "a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe")
PROJECT_SLUG = _get_local_env("INFISICAL_PROJECT_SLUG", "mybots-r46g")
ENVIRONMENT = _get_local_env("INFISICAL_ENV", "dev")
INFISICAL_CLIENT_SECRET = _get_local_env("INFISICAL_CLIENT_SECRET", "")

_client = None
_infisical_error_shown = False

def _get_client():
    global _client, _infisical_error_shown
    if _client is not None:
        return _client
    
    if not INFISICAL_CLIENT_ID or not INFISICAL_CLIENT_SECRET or not PROJECT_SLUG:
        if not _infisical_error_shown:
            print(
                "[SecretManager] WARNING: INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, or "
                "INFISICAL_PROJECT_SLUG is not set. Falling back to Infisical CLI/local .env."
            )
            _infisical_error_shown = True
        return None
        
    if InfisicalSDKClient is None:
        if not _infisical_error_shown:
            print(
                "[SecretManager] infisical_sdk not installed; using local .env / environment only."
            )
            _infisical_error_shown = True
        return None
    try:
        _client = InfisicalSDKClient(host="https://us.infisical.com")
        _client.auth.universal_auth.login(
            client_id=INFISICAL_CLIENT_ID,
            client_secret=INFISICAL_CLIENT_SECRET
        )
        return _client
    except Exception as e:
        if not _infisical_error_shown:
            print(f"[SecretManager] Failed to authenticate with Infisical: {e}. Falling back to Infisical CLI/local .env.")
            _infisical_error_shown = True
        return None

_cli_secrets_cache: dict[str, str] | None = None

def _load_cli_secrets_cache() -> dict[str, str]:
    global _cli_secrets_cache
    if _cli_secrets_cache is not None:
        return _cli_secrets_cache
    
    _cli_secrets_cache = {}
    if not PROJECT_ID or shutil.which("infisical") is None:
        return _cli_secrets_cache

    try:
        import json
        res = subprocess.run(
            [
                "infisical",
                "export",
                "--env",
                ENVIRONMENT,
                "--projectId",
                PROJECT_ID,
                "--format=json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout.strip())
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "key" in item and "value" in item:
                        _cli_secrets_cache[item["key"]] = str(item["value"])
            print(f"[SecretManager] Successfully cached {len(_cli_secrets_cache)} secrets from Infisical CLI.")
    except Exception as e:
        print(f"[SecretManager] Warning: failed to load Infisical CLI export: {e}")
        
    return _cli_secrets_cache

def _get_cli_secret(name: str) -> str:
    """Best-effort Infisical CLI fallback for local logged-in development."""
    cache = _load_cli_secrets_cache()
    return cache.get(name, "")

import re
from urllib.parse import urlparse

def normalize_proxy_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    
    # Check if it matches ip:port:user:pass or similar raw format
    raw_match = re.match(r"^([\w\.\-]+):(\d+):([\w\.\-]+):([\w\.\-\@\#\$]+)$", url)
    if raw_match:
        host, port, user, password = raw_match.groups()
        return f"http://{user}:{password}@{host}:{port}"
        
    # Example: user:pass@ip:port
    auth_host_match = re.match(r"^([\w\.\-]+):([\w\.\-\@\#\$]+)@([\w\.\-]+):(\d+)$", url)
    if auth_host_match:
        user, password, host, port = auth_host_match.groups()
        return f"http://{user}:{password}@{host}:{port}"

    # If it doesn't contain a scheme, prefix it with http://
    if "://" not in url:
        url = f"http://{url}"
        
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc.replace(" ", "")
        return f"{scheme}://{netloc}{parsed.path}"
    except Exception:
        return url

_secrets_cache: dict[str, str] = {}

def get_secret(name: str, default: str = "") -> str:
    """Retrieve a secret from Infisical, falling back to local .env or default."""
    global _secrets_cache
    if name in _secrets_cache:
        return _secrets_cache[name]

    # Prioritize local environment and local .env (very fast, avoids network/CLI delays)
    val = _get_local_env(name, "")
    if val:
        if name in ("PROXY_URL", "CAPMONSTER_PROXY_URL"):
            val = normalize_proxy_url(val)
        _secrets_cache[name] = val
        return val

    client = _get_client()
    if client:
        for attempt in range(3):
            try:
                secret = client.secrets.get_secret_by_name(
                    secret_name=name,
                    project_slug=PROJECT_SLUG,
                    environment_slug=ENVIRONMENT,
                    secret_path="/"
                )
                if secret and secret.secretValue:
                    val = secret.secretValue
                break
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "rate limit" in err_msg.lower():
                    print(f"[SecretManager] Rate limited (429) fetching {name}. Retrying in 5s... (attempt {attempt + 1}/3)")
                    time.sleep(5)
                    continue
                break

    if not val:
        val = _get_cli_secret(name)
            
    if not val:
        val = default

    if name in ("PROXY_URL", "CAPMONSTER_PROXY_URL"):
        val = normalize_proxy_url(val)
    
    _secrets_cache[name] = val
    return val


def get_proxy_url(name: str = "PROXY_URL") -> str:
    """Return a normalized proxy URL (Infisical/CLI, then env/.env)."""
    return get_secret(name, "")


# Indeed / Glassdoor / Workopolis hit Cloudflare hard. Use Proxy-Cheap for
# browser + CapMonster on those bots only. LinkedIn / ATS keep Webshare/static.
_CF_HEAVY_BOTS = frozenset({
    "indeed_it",
    "indeed_general",
    "glassdoor_it",
    "glassdoor_general",
    "workopolis_it",
    "workopolis_general",
})
_CF_HEAVY_PORTALS = frozenset({"indeed", "glassdoor", "workopolis"})


def _env_bot_name(env: dict | None = None) -> str:
    target = env if env is not None else os.environ
    return (target.get("BOT_NAME") or "").strip().lower()


def _env_portal(env: dict | None = None) -> str:
    target = env if env is not None else os.environ
    return (
        target.get("JOB_QUEUE_PORTAL")
        or target.get("JOBBOTS_PORTAL")
        or target.get("PORTAL")
        or ""
    ).strip().lower()


def is_cf_heavy_portal(*, bot_name: str = "", portal: str = "", env: dict | None = None) -> bool:
    """True when browser/CapMonster should prefer Proxy-Cheap over Webshare."""
    target = env if env is not None else os.environ
    force = (target.get("JOBBOTS_CF_HEAVY_PROXY") or "").strip().lower()
    if force in {"0", "false", "no", "off", "webshare", "static"}:
        return False
    if force in {"1", "true", "yes", "on", "cheap", "proxy-cheap", "proxy_cheap"}:
        return True
    bot = (bot_name or _env_bot_name(target)).strip().lower()
    if bot in _CF_HEAVY_BOTS:
        return True
    if bot.startswith(("indeed_", "glassdoor_", "workopolis_")):
        return True
    p = (portal or _env_portal(target)).strip().lower()
    return p in _CF_HEAVY_PORTALS


def _cheap_proxy_candidates(env: dict | None = None) -> list[str]:
    """Ordered Proxy-Cheap / rotating residential candidates."""
    target = env if env is not None else None
    names = (
        "NSTBROWSER_PROXY_URL",  # explicit stamp wins
        "PROXY_CHEAP_URL",
        "JOBSPY_PROXY_DATAIMPULSE",
        "DATAIMPULSE_PROXY_URL",
        "PROXY_URL",
        "CAPMONSTER_PROXY_URL",
    )
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if target is not None:
            val = (target.get(name) or "").strip()
            if not val:
                val = (get_proxy_url(name) or "").strip()
        else:
            val = (get_proxy_url(name) or "").strip()
        if not val or val in seen:
            continue
        # For CF-heavy, skip pure Webshare unless nothing else exists later.
        if name != "NSTBROWSER_PROXY_URL" and _looks_webshare_proxy(val) and not _looks_rotating_proxy(val):
            continue
        seen.add(val)
        out.append(val)
    # Last resort: Webshare if cheap is missing entirely.
    if not out:
        for name in ("WEBSHARE_PROXY_URL", "JOBSPY_PROXY_WEBSHARE", "CAPMONSTER_PROXY_URL", "PROXY_URL"):
            val = (get_proxy_url(name) or "").strip()
            if val and val not in seen:
                out.append(val)
                break
    return out


def _webshare_proxy_candidates(env: dict | None = None) -> list[str]:
    """Ordered static Webshare / non-CF-heavy apply candidates."""
    target = env if env is not None else None
    names = (
        "NSTBROWSER_PROXY_URL",
        "WEBSHARE_PROXY_URL",
        "JOBSPY_PROXY_WEBSHARE",
        "CAPMONSTER_PROXY_URL",
        "PROXY_URL",
        "PROXY_CHEAP_URL",
    )
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if target is not None:
            val = (target.get(name) or "").strip()
            if not val:
                val = (get_proxy_url(name) or "").strip()
        else:
            val = (get_proxy_url(name) or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def get_browser_proxy_url() -> str:
    """Proxy for NST browser profiles.

    * Indeed / Glassdoor / Workopolis → Proxy-Cheap first (CF / captcha egress).
    * LinkedIn and other portals → Webshare static first.
    """
    if is_cf_heavy_portal():
        cands = _cheap_proxy_candidates()
    else:
        cands = _webshare_proxy_candidates()
    return cands[0] if cands else ""


def get_capmonster_proxy_url() -> str:
    """Proxy for CapMonster — must match browser egress IP.

    CF-heavy portals (Indeed/Glassdoor/Workopolis) use Proxy-Cheap with the
    browser. Other portals keep Webshare/static so solver IP matches session.
    """
    if is_cf_heavy_portal():
        cands = _cheap_proxy_candidates()
        return cands[0] if cands else ""
    # Non-CF: prefer static; if CAPMONSTER was left on Proxy-Cheap, upgrade to Webshare.
    for name in (
        "CAPMONSTER_PROXY_URL",
        "NSTBROWSER_PROXY_URL",
        "WEBSHARE_PROXY_URL",
        "JOBSPY_PROXY_WEBSHARE",
        "PROXY_URL",
        "PROXY_CHEAP_URL",
    ):
        val = (get_proxy_url(name) or "").strip()
        if not val:
            continue
        if name in {"CAPMONSTER_PROXY_URL", "PROXY_URL"} and _looks_rotating_proxy(val):
            sticky = (
                (get_proxy_url("WEBSHARE_PROXY_URL") or "").strip()
                or (get_proxy_url("JOBSPY_PROXY_WEBSHARE") or "").strip()
                or (get_proxy_url("NSTBROWSER_PROXY_URL") or "").strip()
            )
            if sticky and not _looks_rotating_proxy(sticky):
                return sticky
        return val
    return ""


def _looks_rotating_proxy(url: str) -> bool:
    host = ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = (url or "").lower()
    return any(
        marker in host
        for marker in (
            "proxy-cheap",
            "thehub.proxy-cheap",
            "dataimpulse",
            "rotating",
        )
    )


def _looks_webshare_proxy(url: str) -> bool:
    host = ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = (url or "").lower()
    return "webshare" in host or "p.webshare.io" in host or host.startswith("72.1.")


def resolve_proxy_url(cli_proxy: str | None = None) -> str:
    """Resolve proxy for setup scripts: CLI override, else browser/Webshare."""
    if cli_proxy and cli_proxy.strip():
        return normalize_proxy_url(cli_proxy.strip())
    return get_browser_proxy_url() or get_proxy_url("PROXY_URL")


def align_capmonster_proxy_env(env: dict | None = None) -> None:
    """Align CapMonster/CapSolver with the browser proxy for this bot/portal.

    CapSolver AntiCloudflareTask and reCAPTCHA tokens must use the same egress
    IP as the browser tab, otherwise Cloudflare rejects cf_clearance.
    """
    target = env if env is not None else os.environ
    cf_heavy = is_cf_heavy_portal(env=target)
    if cf_heavy:
        browser = (
            (target.get("NSTBROWSER_PROXY_URL") or "").strip()
            or (target.get("PROXY_CHEAP_URL") or "").strip()
            or (target.get("JOBSPY_PROXY_DATAIMPULSE") or "").strip()
            or (target.get("CAPSOLVER_PROXY_URL") or "").strip()
            or (target.get("CAPMONSTER_PROXY_URL") or "").strip()
            or (target.get("PROXY_URL") or "").strip()
        )
        # Never force Webshare onto CF-heavy bots when Cheap is available.
        webshare = (
            (target.get("WEBSHARE_PROXY_URL") or "").strip()
            or (target.get("JOBSPY_PROXY_WEBSHARE") or "").strip()
        )
        if webshare and browser and _looks_webshare_proxy(browser):
            cheap = (
                (target.get("PROXY_CHEAP_URL") or "").strip()
                or (target.get("JOBSPY_PROXY_DATAIMPULSE") or "").strip()
            )
            if cheap:
                browser = cheap
        if browser:
            target["CAPMONSTER_PROXY_URL"] = browser
            target["CAPSOLVER_PROXY_URL"] = browser
            target["PROXY_URL"] = browser
            target["NSTBROWSER_PROXY_URL"] = browser
            target["JOBBOTS_CF_HEAVY_PROXY"] = "cheap"
        return

    browser = (
        (target.get("NSTBROWSER_PROXY_URL") or "").strip()
        or (target.get("WEBSHARE_PROXY_URL") or "").strip()
        or (target.get("JOBSPY_PROXY_WEBSHARE") or "").strip()
        or (target.get("CAPSOLVER_PROXY_URL") or "").strip()
        or (target.get("CAPMONSTER_PROXY_URL") or "").strip()
        or (target.get("PROXY_URL") or "").strip()
        or (target.get("PROXY_CHEAP_URL") or "").strip()
    )
    sticky = (
        (target.get("WEBSHARE_PROXY_URL") or "").strip()
        or (target.get("JOBSPY_PROXY_WEBSHARE") or "").strip()
        or (target.get("NSTBROWSER_PROXY_URL") or "").strip()
    )
    if sticky and browser and _looks_rotating_proxy(browser) and not _looks_rotating_proxy(sticky):
        browser = sticky
    if browser:
        target["CAPMONSTER_PROXY_URL"] = browser
        target["CAPSOLVER_PROXY_URL"] = browser
        if sticky and _looks_rotating_proxy((target.get("PROXY_URL") or "").strip()):
            target["PROXY_URL"] = sticky


def stamp_cf_heavy_proxy_env(env: dict, *, portal: str = "", bot_name: str = "") -> dict:
    """Force Proxy-Cheap onto env for Indeed/Glassdoor/Workopolis apply workers.

    Honor ``JOBBOTS_CF_HEAVY_PROXY=webshare`` (or FORCE_WEBSHARE_ALL) to keep
    sticky Webshare when Cheap auth is broken or intentionally disabled.
    """
    if not is_cf_heavy_portal(bot_name=bot_name, portal=portal, env=env):
        force = (env.get("JOBBOTS_CF_HEAVY_PROXY") or "").strip().lower()
        force_ws = (env.get("JOBBOTS_FORCE_WEBSHARE_ALL") or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if force in {"webshare", "static"} or force_ws:
            webshare = (
                (env.get("WEBSHARE_PROXY_URL") or "").strip()
                or (env.get("JOBSPY_PROXY_WEBSHARE") or "").strip()
                or (get_proxy_url("WEBSHARE_PROXY_URL") or "").strip()
                or (get_proxy_url("JOBSPY_PROXY_WEBSHARE") or "").strip()
            )
            if webshare:
                webshare = normalize_proxy_url(webshare)
                env["JOBBOTS_CF_HEAVY_PROXY"] = "webshare"
                env["NSTBROWSER_PROXY_URL"] = webshare
                env["PROXY_URL"] = webshare
                env["CAPMONSTER_PROXY_URL"] = webshare
                env["CAPSOLVER_PROXY_URL"] = webshare
        return env
    cheap = (
        (env.get("PROXY_CHEAP_URL") or "").strip()
        or (env.get("JOBSPY_PROXY_DATAIMPULSE") or "").strip()
        or (get_proxy_url("PROXY_CHEAP_URL") or "").strip()
        or (get_proxy_url("JOBSPY_PROXY_DATAIMPULSE") or "").strip()
    )
    if not cheap:
        return env
    cheap = normalize_proxy_url(cheap)
    env["JOBBOTS_CF_HEAVY_PROXY"] = "cheap"
    env["JOB_QUEUE_PORTAL"] = (portal or env.get("JOB_QUEUE_PORTAL") or "").strip().lower()
    if bot_name:
        env.setdefault("BOT_NAME", bot_name)
    env["NSTBROWSER_PROXY_URL"] = cheap
    env["PROXY_URL"] = cheap
    env["CAPMONSTER_PROXY_URL"] = cheap
    env["CAPSOLVER_PROXY_URL"] = cheap
    # CapSolver/CapMonster must not silently fall back to Webshare for these portals.
    env["CAPTCHA_CAPMONSTER_PROXYLESS_FALLBACK"] = env.get("CAPTCHA_CAPMONSTER_PROXYLESS_FALLBACK") or "0"
    return env


# Startup sweep to normalize keys in environment
for key in (
    "PROXY_URL",
    "CAPMONSTER_PROXY_URL",
    "CAPSOLVER_PROXY_URL",
    "PROXY_CHEAP_URL",
    "WEBSHARE_PROXY_URL",
):
    val = os.environ.get(key)
    if val:
        os.environ[key] = normalize_proxy_url(val)
    if key in _local_env:
        _local_env[key] = normalize_proxy_url(_local_env[key])
