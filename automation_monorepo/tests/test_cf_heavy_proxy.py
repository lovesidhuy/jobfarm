"""Proxy lane routing: CF-heavy boards use Proxy-Cheap; LinkedIn uses Webshare."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_REPO = ROOT.parent
for _p in (str(_REPO), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch):
    """Isolate from Travis Infisical/env secrets that would pollute lane selection.

    Travis injects real PROXY_URL / DATAIMPULSE / NSTBROWSER_PROXY_URL. Without
    clearing both os.environ *and* secret_manager caches, get_browser_proxy_url
    returns gw.dataimpulse.com and these unit tests fail on CI only.
    """
    for k in (
        "BOT_NAME",
        "JOB_QUEUE_PORTAL",
        "JOBBOTS_CF_HEAVY_PROXY",
        "NSTBROWSER_PROXY_URL",
        "PROXY_URL",
        "CAPMONSTER_PROXY_URL",
        "PROXY_CHEAP_URL",
        "WEBSHARE_PROXY_URL",
        "JOBSPY_PROXY_WEBSHARE",
        "JOBSPY_PROXY_DATAIMPULSE",
        "DATAIMPULSE_PROXY_URL",
    ):
        monkeypatch.delenv(k, raising=False)

    # Synthetic lanes only — must not fall through to Infisical CLI / env secrets.
    monkeypatch.setenv("PROXY_CHEAP_URL", "http://u:p@thehub.proxy-cheap.com:8080")
    monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://u:p@72.1.132.207:8099")
    monkeypatch.setenv("JOBSPY_PROXY_WEBSHARE", "http://u:p@72.1.132.207:8099")

    try:
        from core import secret_manager as sm

        sm._secrets_cache.clear()
        # Force empty CLI cache so Infisical export cannot re-inject production proxies.
        sm._cli_secrets_cache = {}
        cheap = "http://u:p@thehub.proxy-cheap.com:8080"
        web = "http://u:p@72.1.132.207:8099"
        # Seed cache so get_secret never falls through to Infisical SDK/CLI on Travis.
        sm._secrets_cache.update(
            {
                "PROXY_CHEAP_URL": cheap,
                "WEBSHARE_PROXY_URL": web,
                "JOBSPY_PROXY_WEBSHARE": web,
                "NSTBROWSER_PROXY_URL": "",
                "PROXY_URL": "",
                "CAPMONSTER_PROXY_URL": "",
                "JOBSPY_PROXY_DATAIMPULSE": "",
                "DATAIMPULSE_PROXY_URL": "",
            }
        )
        if isinstance(getattr(sm, "_local_env", None), dict):
            for k in list(sm._secrets_cache):
                sm._local_env.pop(k, None)
    except Exception:
        pass

    yield

    try:
        from core import secret_manager as sm

        sm._secrets_cache.clear()
        sm._cli_secrets_cache = None
    except Exception:
        pass


def test_indeed_uses_cheap():
    from core.secret_manager import is_cf_heavy_portal, get_browser_proxy_url, get_capmonster_proxy_url

    os.environ["BOT_NAME"] = "indeed_it"
    assert is_cf_heavy_portal() is True
    b = get_browser_proxy_url()
    c = get_capmonster_proxy_url()
    assert "proxy-cheap" in b or "thehub" in b, b
    assert b == c


def test_linkedin_uses_webshare():
    from core.secret_manager import is_cf_heavy_portal, get_browser_proxy_url

    os.environ["BOT_NAME"] = "linkedin_general"
    assert is_cf_heavy_portal() is False
    b = get_browser_proxy_url()
    assert "72.1.132.207" in b or "webshare" in b.lower(), b


def test_stamp_cf_heavy_env():
    from core.secret_manager import stamp_cf_heavy_proxy_env

    env = {
        "PROXY_CHEAP_URL": "http://u:p@thehub.proxy-cheap.com:8080",
        "WEBSHARE_PROXY_URL": "http://u:p@72.1.132.207:8099",
    }
    stamp_cf_heavy_proxy_env(env, portal="workopolis", bot_name="workopolis_it")
    assert env["JOBBOTS_CF_HEAVY_PROXY"] == "cheap"
    assert env["PROXY_URL"] == env["CAPMONSTER_PROXY_URL"] == env["NSTBROWSER_PROXY_URL"]
    assert "proxy-cheap" in env["PROXY_URL"] or "thehub" in env["PROXY_URL"]


@pytest.mark.parametrize(
    "bot",
    ["indeed_it", "indeed_general", "glassdoor_it", "workopolis_it"],
)
def test_all_cf_heavy_bots_use_cheap_and_match_capmonster(bot):
    from core.secret_manager import is_cf_heavy_portal, get_browser_proxy_url, get_capmonster_proxy_url

    os.environ["BOT_NAME"] = bot
    os.environ["JOB_QUEUE_PORTAL"] = bot.split("_", 1)[0]
    assert is_cf_heavy_portal() is True
    b = get_browser_proxy_url()
    c = get_capmonster_proxy_url()
    assert b == c, (b, c)
    assert "proxy-cheap" in b or "thehub" in b, b


def test_align_capmonster_matches_browser_on_cf_heavy():
    from core.secret_manager import align_capmonster_proxy_env

    env = {
        "BOT_NAME": "glassdoor_it",
        "JOB_QUEUE_PORTAL": "glassdoor",
        "PROXY_CHEAP_URL": "http://u:p@thehub.proxy-cheap.com:8080",
        "WEBSHARE_PROXY_URL": "http://u:p@72.1.132.207:8099",
        "CAPMONSTER_PROXY_URL": "http://u:p@72.1.132.207:8099",  # wrong: webshare
    }
    align_capmonster_proxy_env(env)
    assert "proxy-cheap" in env["CAPMONSTER_PROXY_URL"] or "thehub" in env["CAPMONSTER_PROXY_URL"]
    assert env["CAPMONSTER_PROXY_URL"] == env["PROXY_URL"] == env["NSTBROWSER_PROXY_URL"]
    # CapSolver must share the same sticky egress as the browser for cf_clearance.
    assert env.get("CAPSOLVER_PROXY_URL") == env["CAPMONSTER_PROXY_URL"]
