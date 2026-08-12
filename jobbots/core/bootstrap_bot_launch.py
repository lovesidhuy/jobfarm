"""Shared launcher bootstrap for all portal bot run scripts."""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote, urlparse, urlunparse


_COMMON_SECRETS = (
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "BLUESMINDS_API_KEY",
    "BLUESMINDS_MODEL",
    "BLUESMINDS_BASE_URL",
    "AKASHML_API_KEY",
    "AKASHML_MODEL",
    "AKASHML_BASE_URL",
    "FIRECRAWL_API_KEY",
    "FIRECRAWL_API_BASE",
    "FIRECRAWL_SELF_HOST",
    "TAVILY_API_KEY",
    "CAPMONSTER_API_KEY",
    "CAPMONSTER_CLIENT_KEY",
    "capkey",
    "PROXY_URL",
    "CAPMONSTER_PROXY_URL",
)


def _set_if_secret(name: str, get_secret) -> None:
    value = (get_secret(name, "") or "").strip()
    if value:
        os.environ[name] = value


def prefetch_launch_secrets(repo: Path, extra_secret_names: Iterable[str] = ()) -> None:
    from jobbots.core.supervisor_runtime import merge_dotenv_into_env

    merge_dotenv_into_env(os.environ, repo / ".env")

    try:
        from jobbots.core.secret_manager import get_secret

        for name in (*_COMMON_SECRETS, *extra_secret_names):
            _set_if_secret(name, get_secret)
    except Exception as exc:
        print(f"[Bootstrap] Secret prefetch warning: {exc}")

    if os.environ.get("CAPMONSTER_API_KEY") and not os.environ.get("CAPMONSTER_CLIENT_KEY"):
        os.environ["CAPMONSTER_CLIENT_KEY"] = os.environ["CAPMONSTER_API_KEY"]
    if os.environ.get("PROXY_URL") and not os.environ.get("CAPMONSTER_PROXY_URL"):
        os.environ["CAPMONSTER_PROXY_URL"] = os.environ["PROXY_URL"]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _profile_pool_for_bot(bot_name: str) -> list[str]:
    bot_key = f"NSTBROWSER_PROFILE_ID_{bot_name.upper()}_POOL"
    raw = os.environ.get(bot_key) or os.environ.get("NSTBROWSER_PROFILE_ID_POOL") or ""
    profiles = []
    for part in raw.replace("\n", ",").split(","):
        profile_id = part.strip()
        if profile_id and profile_id not in profiles:
            profiles.append(profile_id)
    return profiles


def _select_rotated_nstbrowser_profile(repo: Path, bot_name: str) -> str:
    profiles = _profile_pool_for_bot(bot_name)
    if not profiles:
        return ""

    data_dir = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
    if data_dir:
        state_path = Path(data_dir) / "nst_profile_rotation_state.json"
    else:
        state_path = repo / "data" / "nst_profile_rotation_state.json"
    state: dict[str, int] = {}
    try:
        if state_path.exists():
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = {str(k): int(v) for k, v in loaded.items()}
    except Exception:
        state = {}

    cursor_key = f"{bot_name}:cursor"
    index = state.get(cursor_key, -1) + 1
    profile_id = profiles[index % len(profiles)]
    state[cursor_key] = index % len(profiles)

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Bootstrap] NSTBrowser rotation state warning: {exc}")

    return profile_id


def _dataimpulse_session_proxy_url(proxy_url: str, session_id: str) -> str:
    parsed = urlparse(proxy_url)
    host = parsed.hostname or ""
    if "dataimpulse" not in host.lower() or not parsed.username or not session_id:
        return proxy_url

    username = unquote(parsed.username)
    if "__" in username:
        base = username.split(";", 1)[0]
        username = f"{base};{session_id}"
    else:
        base = username.split(";", 1)[0]
        username = f"{base}__{session_id}"

    password = unquote(parsed.password or "")
    auth = quote(username, safe="._-;") if username else ""
    if password:
        auth += ":" + quote(password, safe="")
    netloc = auth + "@" + host
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse((parsed.scheme or "http", netloc, parsed.path or "", "", "", ""))


def _rotated_proxy_session_id(bot_name: str) -> str:
    explicit = (os.environ.get("NSTBROWSER_PROXY_SESSION_ID") or "").strip()
    if explicit:
        return explicit
    safe_bot = "".join(ch if ch.isalnum() else "-" for ch in bot_name.lower()).strip("-") or "bot"
    return f"{safe_bot}-{int(time.time())}"


def _update_nstbrowser_profile_proxy(profile_id: str, proxy_url: str) -> None:
    if not profile_id or not proxy_url:
        return
    try:
        import requests
        from jobbots.core.secret_manager import get_secret

        api_host = (get_secret("NSTBROWSER_API_HOST", "127.0.0.1") or "127.0.0.1").strip()
        api_port = (get_secret("NSTBROWSER_API_PORT", "8848") or "8848").strip()
        api_key = (get_secret("NSTBROWSER_API_KEY", "") or "").strip()
        if not api_key:
            print("[Bootstrap] NSTBrowser proxy rotation skipped: missing NSTBROWSER_API_KEY.")
            return
        api_url = f"http://{api_host}:{api_port}"
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        response = requests.put(
            f"{api_url}/api/v2/profiles/{profile_id}/proxy",
            json={"url": proxy_url},
            headers=headers,
            timeout=15,
        )
        if response.status_code not in (200, 201):
            print(f"[Bootstrap] NSTBrowser proxy update failed: HTTP {response.status_code} {response.text[:180]}")
            return
        data = response.json()
        if data.get("code") not in (0, 200, None):
            print(f"[Bootstrap] NSTBrowser proxy update failed: {data.get('msg') or data.get('message') or data}")
            return
        print(f"[Bootstrap] NSTBrowser profile proxy rotated for {profile_id}.")
    except Exception as exc:
        print(f"[Bootstrap] NSTBrowser proxy rotation warning: {exc}")


def _stop_nstbrowser_profile(profile_id: str) -> None:
    if not profile_id:
        return
    try:
        import requests
        from jobbots.core.secret_manager import get_secret

        api_host = (get_secret("NSTBROWSER_API_HOST", "127.0.0.1") or "127.0.0.1").strip()
        api_port = (get_secret("NSTBROWSER_API_PORT", "8848") or "8848").strip()
        api_key = (get_secret("NSTBROWSER_API_KEY", "") or "").strip()
        if not api_key:
            return
        api_url = f"http://{api_host}:{api_port}"
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        response = requests.delete(
            f"{api_url}/api/v2/browsers/{profile_id}",
            headers=headers,
            timeout=10,
        )
        if response.status_code in (200, 201, 204, 404):
            print(f"[Bootstrap] NSTBrowser profile stopped before proxy update: {profile_id}")
        else:
            print(f"[Bootstrap] NSTBrowser stop warning: HTTP {response.status_code} {response.text[:180]}")
        time.sleep(1.5)
    except Exception as exc:
        print(f"[Bootstrap] NSTBrowser stop warning: {exc}")


def _apply_nstbrowser_proxy_rotation(bot_name: str) -> None:
    if (os.environ.get("BROWSER_VENDOR") or "").strip().lower() not in {"nstbrowser", "nst"}:
        return

    # Cloud NST profiles carry the proxy that was used while the portal session
    # was established. Replacing it at launch invalidates that continuity and
    # can cause the portal to challenge or invalidate the session. Proxy writes
    # are therefore an explicit maintenance action, never the normal run path.
    if not _truthy(os.environ.get("NSTBROWSER_SYNC_PROFILE_PROXY")):
        print(
            "[Bootstrap] Preserving the NSTBrowser profile proxy. "
            "Set NSTBROWSER_SYNC_PROFILE_PROXY=1 only for an intentional proxy migration."
        )
        return

    # CF-heavy (Indeed/Glassdoor/Workopolis): Proxy-Cheap for browser+CapMonster.
    # LinkedIn / others: static Webshare first.
    try:
        from jobbots.core.secret_manager import is_cf_heavy_portal, get_browser_proxy_url
        if is_cf_heavy_portal(bot_name=bot_name):
            proxy_url = (
                (os.environ.get("NSTBROWSER_PROXY_URL") or "").strip()
                or (os.environ.get("PROXY_CHEAP_URL") or "").strip()
                or (os.environ.get("JOBSPY_PROXY_DATAIMPULSE") or "").strip()
                or (os.environ.get("PROXY_URL") or "").strip()
                or get_browser_proxy_url()
            )
        else:
            proxy_url = (
                (os.environ.get("NSTBROWSER_PROXY_URL") or "").strip()
                or (os.environ.get("WEBSHARE_PROXY_URL") or "").strip()
                or (os.environ.get("JOBSPY_PROXY_WEBSHARE") or "").strip()
                or (os.environ.get("CAPMONSTER_PROXY_URL") or "").strip()
                or (os.environ.get("PROXY_URL") or "").strip()
                or get_browser_proxy_url()
            )
    except Exception:
        proxy_url = (
            (os.environ.get("NSTBROWSER_PROXY_URL") or "").strip()
            or (os.environ.get("PROXY_URL") or "").strip()
            or (os.environ.get("PROXY_CHEAP_URL") or "").strip()
        )
    profile_id = (os.environ.get("NSTBROWSER_PROFILE_ID") or "").strip()
    if not proxy_url or not profile_id:
        return

    # Decide if we want session rotation or direct proxy config
    if _truthy(os.environ.get("NSTBROWSER_ROTATE_PROXY")):
        session_id = _rotated_proxy_session_id(bot_name)
        rotated_proxy_url = _dataimpulse_session_proxy_url(proxy_url, session_id)
        if rotated_proxy_url != proxy_url:
            proxy_url = rotated_proxy_url
            os.environ["PROXY_URL"] = rotated_proxy_url
            os.environ["CAPMONSTER_PROXY_URL"] = rotated_proxy_url
            os.environ["CAPMONSTER_DATAIMPULSE_SESSION_ID"] = session_id
            os.environ["DATAIMPULSE_SESSION_ID"] = session_id
            print(f"[Bootstrap] DataImpulse session rotation selected: {session_id}")
        else:
            print("[Bootstrap] NSTBrowser proxy rotation requested but proxy is not DataImpulse/session-capable.")

    # Keep-alive / multi-job same-window runs must not stop the profile between
    # bot process exits (NST launch quota). Skip stop+proxy rewrite when set.
    if _truthy(os.environ.get("KEEP_BROWSER")) or _truthy(os.environ.get("NSTBROWSER_KEEP_ALIVE")):
        print(
            f"[Bootstrap] KEEP_BROWSER/NSTBROWSER_KEEP_ALIVE set — leaving NST profile "
            f"{profile_id} running (skip stop/proxy rewrite)."
        )
        return

    # Always ensure the proxy is updated in the NSTBrowser profile for uniformity
    print(f"[Bootstrap] Aligning NSTBrowser profile {profile_id} proxy to: {proxy_url}")
    _stop_nstbrowser_profile(profile_id)
    _update_nstbrowser_profile_proxy(profile_id, proxy_url)


def bootstrap_bot_launch(
    *,
    repo: Path,
    bot_name: str,
    bot_import: str,
    cdp_port: str,
    job_profile: str,
    profile_subdir: str,
    extra_secret_names: Iterable[str] = (),
) -> None:
    workspace = repo.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    print(f"[Bootstrap] repo={repo} sys.path={sys.path}")

    prefetch_launch_secrets(repo, extra_secret_names)

    if _truthy(os.environ.get("NSTBROWSER_ROTATE_PROFILE")):
        from jobbots.core.browser.nst_profile_safety import nstbrowser_forbid_create

        # Rotation only cycles pre-existing pool IDs — never creates — but near
        # quota we still refuse silent pool switching unless explicitly allowed.
        if nstbrowser_forbid_create() and not _truthy(
            os.environ.get("NSTBROWSER_ALLOW_ROTATE_UNDER_FORBID")
        ):
            print(
                "[Bootstrap] NSTBROWSER_ROTATE_PROFILE ignored while "
                "NSTBROWSER_FORBID_CREATE is on (reuse stamped profile only)."
            )
        else:
            rotated_profile = _select_rotated_nstbrowser_profile(repo, bot_name)
            if rotated_profile:
                os.environ["NSTBROWSER_PROFILE_ID"] = rotated_profile
                print(f"[Bootstrap] NSTBrowser rotation selected profile: {rotated_profile}")
            else:
                print("[Bootstrap] NSTBrowser rotation requested but no profile pool was configured.")

    data_dir = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
    if data_dir:
        chrome_profile_dir = str(Path(data_dir) / "browser_profiles" / profile_subdir)
    else:
        chrome_profile_dir = str(repo / "data/browser_profiles" / profile_subdir)

    os.environ.update(
        {
            "BOT_NAME": bot_name,
            "BROWSER_VENDOR": os.environ.get("BROWSER_VENDOR", "chrome"),
            "CDP_PORT": cdp_port,
            "CHROME_PROFILE_DIR": chrome_profile_dir,
            "JOB_PROFILE": job_profile,
            "SKIP_USER_START": "1",
            "RUN_IN_BACKGROUND": os.environ.get("RUN_IN_BACKGROUND", "0"),
            "DISABLE_PYAUTOGUI_ALERTS": os.environ.get("DISABLE_PYAUTOGUI_ALERTS", "0"),
            "NSTBROWSER_PROFILE_ID": os.environ.get("NSTBROWSER_PROFILE_ID", ""),
        }
    )

    _apply_nstbrowser_proxy_rotation(bot_name)

    from jobbots.core.captcha_runtime import apply_standard_captcha_env, captcha_bootstrap_message

    apply_standard_captcha_env(os.environ)

    print(f"[Bootstrap] workspace={workspace}")
    print(f"[Bootstrap] profile={os.environ['CHROME_PROFILE_DIR']}")
    print(captcha_bootstrap_message(os.environ))
    print("[Bootstrap] CapMonster enabled for reCAPTCHA.")

    module_name, _, attr = bot_import.partition(":")
    attr = attr or "main"
    bot_module = importlib.import_module(module_name)
    getattr(bot_module, attr)()
