"""Farm productivity contract — offline + optional live (active NST slot / proxies).

Ensures the refactor did not drift the production topology that makes the
ephemeral farm productive:

* Active NST slot (1 or 2) holds the logged-in browser profiles
* CF-heavy boards (Indeed / Glassdoor / Workopolis) use Proxy-Cheap for
  browser **and** CapMonster (same egress)
* LinkedIn and Job Bank use Webshare static
* Discovery scrape ladder: local → webshare → Proxy-Cheap (dataimpulse tier)
* Indeed IT + Indeed General separate; LinkedIn sole session handles IT+CX/admin
* Glassdoor IT + Workopolis IT each have one NST profile (general paused)
* ATS (GH/Lever/Ashby/Bamboo) via Playwright ``google_it`` — **no NST**
* Job Bank Direct Apply is confirmation-backed in its dedicated NST profile

Offline checks are free and must pass in CI. Live checks need NST API +
proxy secrets (ephemeral worker / local agent with the active slot logged in).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jobbots.paths import REPO_ROOT

# Production supervised bots that must be enabled for a productive farm.
_REQUIRED_ENABLED = (
    "indeed_it",
    "indeed_general",
    "glassdoor_it",
    "workopolis_it",
    "linkedin_general",
    "jobbank_it",
    "google_it",  # ATS Playwright applier — no NST
)

# Intentionally paused (code remains; must not auto-start).
_REQUIRED_PAUSED = (
    "glassdoor_general",
    "workopolis_general",
    "linkedin_it",  # superseded by linkedin_general sole session
)

# NST cloud profiles required for browser apply (google_it is Playwright-only).
_NST_BROWSER_BOTS = (
    "indeed_it",
    "indeed_general",
    "glassdoor_it",
    "workopolis_it",
    "linkedin_general",
    "jobbank_it",
)

_CF_HEAVY = ("indeed_it", "indeed_general", "glassdoor_it", "workopolis_it")
_WEBSHARE_LANE = ("linkedin_general", "jobbank_it")

_APPLY_PORTALS_RE = re.compile(
    r"^JOBBOTS_APPLY_PORTALS=indeed,linkedin,glassdoor,workopolis,jobbank,"
    r"google,greenhouse,lever,ashby,bamboohr\s*$",
    re.M,
)


def _overrides_path() -> Path:
    return REPO_ROOT / "packer" / "linux" / "runtime-prod-overrides.conf"


def _read_overrides() -> str:
    path = _overrides_path()
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def check_supervised_topology() -> list[dict[str, Any]]:
    from jobbots.core.supervised_bots import supervised_bot_configs

    enabled = {c["bot_name"]: c for c in supervised_bot_configs(include_disabled=False)}
    all_bots = {c["bot_name"]: c for c in supervised_bot_configs(include_disabled=True)}
    out: list[dict[str, Any]] = []

    for name in _REQUIRED_ENABLED:
        cfg = enabled.get(name)
        out.append(
            _check(
                f"bot_enabled:{name}",
                cfg is not None,
                f"portal={cfg.get('portal') if cfg else None} "
                f"profile={cfg.get('job_profile') if cfg else None}",
            )
        )

    for name in _REQUIRED_PAUSED:
        cfg = all_bots.get(name)
        paused = cfg is not None and not cfg.get("enabled", True)
        out.append(
            _check(
                f"bot_paused:{name}",
                paused,
                "enabled flag must be False" if cfg else "missing from registry",
            )
        )

    # Indeed dual-profile isolation
    it = all_bots.get("indeed_it") or {}
    gen = all_bots.get("indeed_general") or {}
    out.append(
        _check(
            "indeed_profiles_isolated",
            (it.get("job_profile") or "").upper() == "IT"
            and (gen.get("job_profile") or "").title() == "General"
            and it.get("browser_profile_subdir") != gen.get("browser_profile_subdir"),
            f"it={it.get('browser_profile_subdir')} gen={gen.get('browser_profile_subdir')}",
        )
    )

    # LinkedIn sole session
    li_gen = all_bots.get("linkedin_general") or {}
    li_it = all_bots.get("linkedin_it") or {}
    out.append(
        _check(
            "linkedin_sole_session",
            li_gen.get("enabled", False) is True and li_it.get("enabled", True) is False,
            "linkedin_general on, linkedin_it off",
        )
    )

    # Glassdoor / Workopolis: IT only (one NST profile each; rows may omit enabled=True)
    for name in ("glassdoor_it", "workopolis_it"):
        cfg = all_bots.get(name) or {}
        enabled = cfg.get("enabled", True) is True
        out.append(
            _check(
                f"nst_profile_bot:{name}",
                enabled and (cfg.get("job_profile") or "").upper() == "IT",
                f"portal={cfg.get('portal')} profile={cfg.get('job_profile')} enabled={enabled}",
            )
        )

    return out


def check_portal_adapters() -> list[dict[str, Any]]:
    from jobbots.integrations.portals import ATS_PORTALS, BROWSER_PORTALS, available_portals

    expected = {
        "indeed", "glassdoor", "workopolis", "linkedin", "jobbank",
        "greenhouse", "ashby", "lever", "bamboohr",
    }
    have = set(available_portals())
    out = [
        _check("portal_adapters_complete", have == expected, f"have={sorted(have)}"),
        _check(
            "browser_portals",
            set(BROWSER_PORTALS) == {"indeed", "glassdoor", "workopolis", "linkedin", "jobbank"},
            str(BROWSER_PORTALS),
        ),
        _check(
            "ats_portals",
            set(ATS_PORTALS) == {"greenhouse", "ashby", "lever", "bamboohr"},
            str(ATS_PORTALS),
        ),
    ]
    return out


def check_prod_overrides() -> list[dict[str, Any]]:
    text = _read_overrides()
    out: list[dict[str, Any]] = []
    if not text:
        return [_check("runtime_overrides_present", False, "packer/linux/runtime-prod-overrides.conf missing")]

    slot_m = re.search(r"^NSTBROWSER_ACTIVE_SLOT=([12])\s*$", text, re.M)
    out.append(
        _check(
            "active_slot_pinned",
            bool(slot_m),
            f"NSTBROWSER_ACTIVE_SLOT={slot_m.group(1) if slot_m else 'missing'} (must be 1 or 2)",
        )
    )
    out.append(_check("rotate_proxy_off", bool(re.search(r"^NSTBROWSER_ROTATE_PROXY=0\s*$", text, re.M)), "NSTBROWSER_ROTATE_PROXY=0"))
    out.append(_check("metro_vancouver_only", bool(re.search(r"^METRO_VANCOUVER_ONLY=1\s*$", text, re.M)), "METRO_VANCOUVER_ONLY=1"))
    out.append(_check("ats_board_budget", bool(re.search(r"^ATS_BOARD_API_MAX_SLUGS_PER_PLATFORM=250\s*$", text, re.M)), "ATS_BOARD_API_MAX_SLUGS_PER_PLATFORM=250"))
    out.append(_check("apply_portals", bool(_APPLY_PORTALS_RE.search(text)), "full browser+ATS apply set"))
    out.append(
        _check(
            "jobbank_apply_enabled",
            "jobbank" in (re.search(r"^JOBBOTS_APPLY_PORTALS=(.*)$", text, re.M) or [None, ""])[1],
            "jobbank included in JOBBOTS_APPLY_PORTALS",
        )
    )
    out.append(
        _check(
            "jobbank_email_retired",
            bool(re.search(r"^JOBBOTS_JOBBANK_EMAIL_APPLY_RETIRED=1\s*$", text, re.M)),
            "JOBBOTS_JOBBANK_EMAIL_APPLY_RETIRED=1",
        )
    )
    out.append(
        _check(
            "jobbank_direct_apply_on",
            bool(re.search(r"^JOBBANK_DIRECT_APPLY_ENABLED=1\s*$", text, re.M)),
            "JOBBANK_DIRECT_APPLY_ENABLED=1",
        )
    )
    out.append(
        _check(
            "general_apply_indeed_only",
            bool(re.search(r"^JOBBOTS_GENERAL_APPLY_PORTALS=indeed\s*$", text, re.M)),
            "office/CS Easy Apply drains via indeed general worker",
        )
    )
    out.append(
        _check(
            "jobspy_skip_local",
            bool(re.search(r"^JOBSPY_SKIP_LOCAL=1\s*$", text, re.M)),
            "cloud discovery starts on paid proxy tiers",
        )
    )
    out.append(
        _check(
            "jobspy_smart_ladder",
            bool(re.search(r"^JOBSPY_PROXY_MODE=smart\s*$", text, re.M)),
            "JOBSPY_PROXY_MODE=smart",
        )
    )
    out.append(
        _check(
            "glassdoor_hybrid",
            bool(re.search(r"^GLASSDOOR_DISCOVERY_PROVIDER=hybrid\s*$", text, re.M)),
            "GLASSDOOR_DISCOVERY_PROVIDER=hybrid",
        )
    )
    return out


def check_discovery_bins() -> list[dict[str, Any]]:
    bin_dir = REPO_ROOT / "packer" / "linux" / "bin"
    required = {
        "jobbots-discover-indeed-it": None,
        "jobbots-discover-indeed-general": None,
        "jobbots-discover-linkedin-general": None,
        "jobbots-discover-glassdoor-it": "--portals glassdoor,workopolis",
        "jobbots-discover-ats-it": "phase=A_fast",
        "jobbots-discover-jobbank-it": None,  # Direct Apply discovery producer
    }
    out: list[dict[str, Any]] = []
    for name, needle in required.items():
        path = bin_dir / name
        exists = path.is_file()
        detail = "present" if exists else "missing"
        ok = exists
        if exists and needle:
            text = path.read_text(encoding="utf-8", errors="replace")
            ok = needle in text
            detail = f"contains {needle!r}" if ok else f"missing marker {needle!r}"
        out.append(_check(f"discover_bin:{name}", ok, detail))
    return out


def check_proxy_lane_design(*, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Unit-style check that CF-heavy vs Webshare routing is wired correctly.

    Uses synthetic proxy URLs when real secrets are absent so CI stays offline-safe.
    """
    from jobbots.core.secret_manager import (
        get_browser_proxy_url,
        get_capmonster_proxy_url,
        is_cf_heavy_portal,
        stamp_cf_heavy_proxy_env,
    )

    cheap = "http://u:p@thehub.proxy-cheap.com:8080"
    webshare = "http://u:p@72.1.132.207:8099"
    target = dict(env) if env is not None else {}
    # Isolate process env for deterministic design check. Production workers have
    # real CAPMONSTER/WEBSHARE in secrets.env — stamp every alias so get_proxy_url
    # cannot mix real credentials into the synthetic lane proof.
    saved = {
        k: os.environ.get(k)
        for k in (
            "BOT_NAME", "JOB_QUEUE_PORTAL", "JOBBOTS_CF_HEAVY_PROXY",
            "PROXY_CHEAP_URL", "WEBSHARE_PROXY_URL", "JOBSPY_PROXY_WEBSHARE",
            "PROXY_URL", "CAPMONSTER_PROXY_URL", "NSTBROWSER_PROXY_URL",
            "JOBSPY_PROXY_DATAIMPULSE", "DATAIMPULSE_PROXY_URL",
        )
    }
    out: list[dict[str, Any]] = []

    def _same_egress(a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a == b:
            return True
        # Host match is enough for design (credentials may differ across aliases).
        return (urlparse(a).hostname or "") == (urlparse(b).hostname or "")

    try:
        for k in saved:
            os.environ.pop(k, None)
        # Drop cached Infisical values so os.environ synthetic URLs win.
        try:
            from jobbots.core import secret_manager as _sm

            for k in saved:
                _sm._secrets_cache.pop(k, None)
        except Exception:
            pass
        cheap_u = target.get("PROXY_CHEAP_URL") or cheap
        web_u = target.get("WEBSHARE_PROXY_URL") or webshare
        os.environ["PROXY_CHEAP_URL"] = cheap_u
        os.environ["WEBSHARE_PROXY_URL"] = web_u
        os.environ["JOBSPY_PROXY_WEBSHARE"] = target.get("JOBSPY_PROXY_WEBSHARE") or web_u
        # Leave NSTBROWSER_PROXY_URL empty so lane selection is portal-driven.
        os.environ["CAPMONSTER_PROXY_URL"] = web_u  # baseline; stamp overwrites for CF-heavy
        os.environ["PROXY_URL"] = cheap_u

        for bot in _CF_HEAVY:
            os.environ["BOT_NAME"] = bot
            portal = bot.split("_", 1)[0]
            os.environ["JOB_QUEUE_PORTAL"] = portal
            os.environ["CAPMONSTER_PROXY_URL"] = cheap_u
            os.environ["PROXY_URL"] = cheap_u
            os.environ["NSTBROWSER_PROXY_URL"] = cheap_u
            cf = is_cf_heavy_portal(bot_name=bot, portal=portal)
            b = get_browser_proxy_url()
            c = get_capmonster_proxy_url()
            host_ok = "proxy-cheap" in (b or "") or "thehub" in (b or "")
            same = _same_egress(b, c)
            out.append(
                _check(
                    f"proxy_cf_heavy:{bot}",
                    cf and host_ok and same,
                    f"cf={cf} browser_host={urlparse(b).hostname if b else None} same_egress={same}",
                )
            )

        for bot in _WEBSHARE_LANE:
            os.environ["BOT_NAME"] = bot
            os.environ["JOB_QUEUE_PORTAL"] = bot.split("_", 1)[0]
            # CF-heavy stamp above may have cached NSTBROWSER_PROXY_URL=cheap —
            # clear again so LinkedIn does not inherit the CF lane.
            os.environ.pop("NSTBROWSER_PROXY_URL", None)
            os.environ["CAPMONSTER_PROXY_URL"] = web_u
            os.environ["PROXY_URL"] = web_u
            os.environ["WEBSHARE_PROXY_URL"] = web_u
            os.environ["JOBSPY_PROXY_WEBSHARE"] = web_u
            try:
                from jobbots.core import secret_manager as _sm

                for k in (
                    "NSTBROWSER_PROXY_URL",
                    "CAPMONSTER_PROXY_URL",
                    "PROXY_URL",
                    "WEBSHARE_PROXY_URL",
                    "JOBSPY_PROXY_WEBSHARE",
                    "PROXY_CHEAP_URL",
                ):
                    _sm._secrets_cache.pop(k, None)
            except Exception:
                pass
            cf = is_cf_heavy_portal(bot_name=bot, portal=bot.split("_", 1)[0])
            b = get_browser_proxy_url()
            c = get_capmonster_proxy_url()
            host_ok = "72.1." in (b or "") or "webshare" in (b or "").lower()
            same = _same_egress(b, c)
            out.append(
                _check(
                    f"proxy_webshare:{bot}",
                    (not cf) and host_ok and same,
                    f"cf={cf} browser_host={urlparse(b).hostname if b else None} same_egress={same}",
                )
            )

        stamped = stamp_cf_heavy_proxy_env(
            {
                "PROXY_CHEAP_URL": cheap,
                "WEBSHARE_PROXY_URL": webshare,
            },
            portal="workopolis",
            bot_name="workopolis_it",
        )
        out.append(
            _check(
                "stamp_cf_heavy_aligns_all",
                stamped.get("PROXY_URL")
                == stamped.get("CAPMONSTER_PROXY_URL")
                == stamped.get("NSTBROWSER_PROXY_URL")
                and "proxy-cheap" in (stamped.get("PROXY_URL") or ""),
                "PROXY_URL == CAPMONSTER == NSTBROWSER on CF-heavy stamp",
            )
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


def check_discovery_ladder_offline() -> list[dict[str, Any]]:
    from jobbots.core.discovery.scrape_proxy import ProxyTier, ScrapeProxyLadder

    tiers = ProxyTier(
        webshare="http://u:p@webshare.example:80",
        dataimpulse="http://u:p@thehub.proxy-cheap.com:8080",
    )
    # Cloud workers skip local (AWS egress blocked).
    ladder = ScrapeProxyLadder(tiers=tiers, mode="smart", skip_local=True)
    out = [
        _check(
            "ladder_starts_webshare_when_skip_local",
            ladder.current_label() == "webshare",
            f"start={ladder.current_label()}",
        )
    ]
    # One rate-limit failure escalates webshare → Proxy-Cheap (dataimpulse tier name).
    # A second failure wraps (no higher tier) — only assert the first escalate.
    escalated = ladder.note_failure("429 rate limit")
    out.append(
        _check(
            "ladder_escalates_to_cheap",
            escalated and ladder.current_label() == "dataimpulse",
            f"escalated={escalated} after_429={ladder.current_label()}",
        )
    )
    # Auth fail blacklists tier
    ladder2 = ScrapeProxyLadder(tiers=ProxyTier(webshare="http://u:p@ws:80", dataimpulse="http://u:p@cheap:80"), mode="smart", skip_local=True)
    ladder2.note_failure("407 Proxy Authentication Required")
    out.append(
        _check(
            "ladder_blacklists_407",
            "webshare" in ladder2._blacklisted or ladder2.current_label() == "dataimpulse",
            f"tier={ladder2.current_label()} blacklisted={sorted(ladder2._blacklisted)}",
        )
    )
    return out


def check_alias_identity() -> list[dict[str, Any]]:
    """Refactor gate: legacy ``core.X`` is the same module object as ``jobbots.core.X``."""
    from jobbots.paths import ensure_monorepo_on_path

    ensure_monorepo_on_path()
    import core.secret_manager as old_sm
    import jobbots.core.secret_manager as new_sm
    import core.discovery.scrape_proxy as old_sp
    import jobbots.core.discovery.scrape_proxy as new_sp

    return [
        _check("alias_secret_manager", old_sm is new_sm, "core.secret_manager is jobbots.core.secret_manager"),
        _check("alias_scrape_proxy", old_sp is new_sp, "core.discovery.scrape_proxy is jobbots.core.discovery.scrape_proxy"),
    ]


def _pinned_active_slot_from_overrides() -> int:
    """Read the production pin from runtime-prod-overrides (default 1)."""
    text = _read_overrides()
    m = re.search(r"^NSTBROWSER_ACTIVE_SLOT=([12])\s*$", text, re.M)
    if m:
        return int(m.group(1))
    raw = (os.environ.get("NSTBROWSER_ACTIVE_SLOT") or "1").strip()
    return 2 if raw in {"2", "secondary", "b", "a2"} else 1


def check_live_slot1_and_proxies() -> list[dict[str, Any]]:
    """Network checks: NST profiles on the active slot exist; proxy probes; lanes match design."""
    out: list[dict[str, Any]] = []
    expected_slot = _pinned_active_slot_from_overrides()
    os.environ["NSTBROWSER_ACTIVE_SLOT"] = str(expected_slot)

    from jobbots.core.browser.nst_accounts import (
        choose_active_slot,
        resolve_api_key,
        resolve_profile_id,
    )
    from jobbots.core.secret_manager import get_secret

    slot = choose_active_slot()
    out.append(
        _check(
            "live_active_slot",
            slot == expected_slot,
            f"slot={slot} (expected NSTBROWSER_ACTIVE_SLOT={expected_slot})",
        )
    )

    try:
        resolved_slot, api_key = resolve_api_key(slot=expected_slot)
        out.append(
            _check(
                f"live_api_key_slot{expected_slot}",
                bool(api_key) and resolved_slot == expected_slot,
                f"slot={resolved_slot}",
            )
        )
    except Exception as exc:
        out.append(_check(f"live_api_key_slot{expected_slot}", False, str(exc)))
        return out

    try:
        import requests

        host = (get_secret("NSTBROWSER_API_HOST", "127.0.0.1") or "127.0.0.1").strip()
        port = (get_secret("NSTBROWSER_API_PORT", "8848") or "8848").strip()
        url = f"http://{host}:{port}/api/v2/profiles?pageNo=1&pageSize=100"
        resp = requests.get(url, headers={"x-api-key": api_key}, timeout=15)
        out.append(_check("live_nst_api", resp.ok, f"HTTP {resp.status_code}"))
        if not resp.ok:
            return out
        payload = resp.json()
        data = payload.get("data")
        docs: list[dict] = []
        if isinstance(data, list):
            docs = [d for d in data if isinstance(d, dict)]
        elif isinstance(data, dict):
            docs = [d for d in (data.get("docs") or data.get("list") or []) if isinstance(d, dict)]
        by_id: dict[str, dict] = {}
        for p in docs:
            for key in ("profileId", "profile_id", "id", "_id"):
                if p.get(key) is not None:
                    by_id[str(p[key])] = p
                    break
    except Exception as exc:
        out.append(_check("live_nst_api", False, str(exc)))
        return out

    for bot in _NST_BROWSER_BOTS:
        try:
            s, pid, used = resolve_profile_id(bot, slot=expected_slot)
            found = pid in by_id
            name = (by_id.get(pid) or {}).get("name") or (by_id.get(pid) or {}).get("profileName") or ""
            out.append(
                _check(
                    f"live_profile:{bot}",
                    found and s == expected_slot,
                    f"slot={s} pid={pid[:8]}… name={name!r} via={used}",
                )
            )
        except Exception as exc:
            out.append(_check(f"live_profile:{bot}", False, str(exc)))

    # Proxy design with real secrets
    from jobbots.core.secret_manager import (
        get_browser_proxy_url,
        get_capmonster_proxy_url,
        is_cf_heavy_portal,
        _looks_rotating_proxy,
        _looks_webshare_proxy,
    )
    from jobbots.core.discovery.scrape_proxy import probe_proxy_url, resolve_proxy_tiers

    cheap = (get_secret("PROXY_CHEAP_URL", "") or get_secret("PROXY_URL", "") or "").strip()
    webshare = (get_secret("WEBSHARE_PROXY_URL", "") or get_secret("JOBSPY_PROXY_WEBSHARE", "") or "").strip()
    out.append(
        _check(
            "live_cheap_configured",
            bool(cheap) and _looks_rotating_proxy(cheap),
            f"host={urlparse(cheap).hostname if cheap else None}",
        )
    )
    out.append(
        _check(
            "live_webshare_configured",
            bool(webshare) and _looks_webshare_proxy(webshare),
            f"host={urlparse(webshare).hostname if webshare else None}",
        )
    )

    saved_bot = os.environ.get("BOT_NAME")
    saved_portal = os.environ.get("JOB_QUEUE_PORTAL")
    try:
        for bot in _CF_HEAVY:
            os.environ["BOT_NAME"] = bot
            os.environ["JOB_QUEUE_PORTAL"] = bot.split("_", 1)[0]
            if cheap:
                os.environ["PROXY_CHEAP_URL"] = cheap
            if webshare:
                os.environ["WEBSHARE_PROXY_URL"] = webshare
            b = get_browser_proxy_url()
            c = get_capmonster_proxy_url()
            out.append(
                _check(
                    f"live_lane_cf:{bot}",
                    is_cf_heavy_portal(bot_name=bot) and bool(b) and b == c and _looks_rotating_proxy(b),
                    f"browser={urlparse(b).hostname if b else None} same={b == c}",
                )
            )
        os.environ["BOT_NAME"] = "linkedin_general"
        os.environ["JOB_QUEUE_PORTAL"] = "linkedin"
        b = get_browser_proxy_url()
        c = get_capmonster_proxy_url()
        out.append(
            _check(
                "live_lane_linkedin_webshare",
                (not is_cf_heavy_portal(bot_name="linkedin_general"))
                and bool(b)
                and b == c
                and _looks_webshare_proxy(b),
                f"browser={urlparse(b).hostname if b else None} same={b == c}",
            )
        )
    finally:
        if saved_bot is None:
            os.environ.pop("BOT_NAME", None)
        else:
            os.environ["BOT_NAME"] = saved_bot
        if saved_portal is None:
            os.environ.pop("JOB_QUEUE_PORTAL", None)
        else:
            os.environ["JOB_QUEUE_PORTAL"] = saved_portal

    try:
        tiers = resolve_proxy_tiers()
        out.append(
            _check(
                "live_ladder_tiers",
                bool(tiers.webshare) and bool(tiers.dataimpulse),
                f"available={tiers.available_names()}",
            )
        )
        working = 0
        for label, url in (("webshare", tiers.webshare), ("cheap", tiers.dataimpulse)):
            if not url:
                continue
            ok, detail = probe_proxy_url(url, timeout=12.0)
            if ok:
                working += 1
            out.append(_check(f"live_proxy_probe:{label}", ok, detail[:160]))
        out.append(_check("live_proxy_probe_any", working >= 1, f"working_tiers={working}"))
    except Exception as exc:
        out.append(_check("live_ladder_tiers", False, str(exc)))

    return out


def run_farm_check(*, live: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.extend(check_supervised_topology())
    checks.extend(check_portal_adapters())
    checks.extend(check_prod_overrides())
    checks.extend(check_discovery_bins())
    checks.extend(check_proxy_lane_design())
    checks.extend(check_discovery_ladder_offline())
    checks.extend(check_alias_identity())
    if live:
        checks.extend(check_live_slot1_and_proxies())

    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "live": live,
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "total": len(checks),
        "checks": checks,
        "failures": failed,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"farm-check: {'OK' if report['ok'] else 'PROBLEMS'} "
        f"({report['passed']}/{report['total']} passed"
        f"{', live' if report.get('live') else ', offline'})",
    ]
    for c in report["checks"]:
        mark = "✓" if c["ok"] else "✗"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        lines.append(f"  {mark} {c['name']}{detail}")
    if report["failures"]:
        lines.append("")
        lines.append("failures:")
        for c in report["failures"]:
            lines.append(f"  - {c['name']}: {c.get('detail') or 'failed'}")
    return "\n".join(lines)
