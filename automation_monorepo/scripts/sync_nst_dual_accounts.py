#!/usr/bin/env python3
"""Sync two NSTBrowser accounts: list profiles, create missing on slot 2,
align DataImpulse proxies, write .env + Infisical.

Usage
-----
  # Dry map (no create / no Infisical write)
  python scripts/sync_nst_dual_accounts.py --scan-only

  # Create missing slot-2 profiles + proxy sync + write .env
  NSTBROWSER_FORBID_CREATE=0 python scripts/sync_nst_dual_accounts.py --sync \\
      --api-key-2 "$KEY2" --write-env --write-infisical

  # Record dashboard daily opens for auto rotation (primary at 29/30)
  python scripts/sync_nst_dual_accounts.py --set-opens-1 29 --set-opens-2 0
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.browser.nst_accounts import (  # noqa: E402
    REQUIRED_BOTS,
    env_key_for_bot,
    set_daily_opens,
)
from core.secret_manager import get_secret, resolve_proxy_url  # noqa: E402

STARTUP_URL_BY_BOT = {
    "indeed_it": "https://ca.indeed.com/account/login",
    "indeed_general": "https://ca.indeed.com/account/login",
    "glassdoor_it": "https://www.glassdoor.ca/profile/login_input.htm",
    "glassdoor_general": "https://www.glassdoor.ca/profile/login_input.htm",
    "workopolis_it": "https://www.workopolis.com/",
    "workopolis_general": "https://www.workopolis.com/",
    "linkedin_it": "https://www.linkedin.com/login",
    "linkedin_general": "https://www.linkedin.com/login",
}

NAME_ALIASES = {
    "linkedin_it": ("LinkedIn_IT_Apply", "Nst_linkedin_it", "LinkedIn_IT"),
    "linkedin_general": ("LinkedIn_IT_Apply", "Nst_linkedin_general", "LinkedIn_General", "LinkedIn_IT"),
}


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def list_profiles(api_url: str, api_key: str) -> list[dict]:
    r = requests.get(
        f"{api_url}/api/v2/profiles",
        headers=_headers(api_key),
        params={"pageNo": 1, "pageSize": 100},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json().get("data") or {}
    if isinstance(data, dict):
        return data.get("docs") or data.get("list") or []
    if isinstance(data, list):
        return data
    return []


def map_bot_profiles(profiles: list[dict]) -> dict[str, str]:
    by_name: dict[str, str] = {}
    for p in profiles:
        name = (p.get("name") or "").strip()
        pid = p.get("profileId") or p.get("id")
        if name and pid and name not in by_name:
            by_name[name] = str(pid)

    mapped: dict[str, str] = {}
    for bot in REQUIRED_BOTS:
        # Prefer canonical Nst_{bot}
        canonical = f"Nst_{bot}"
        if canonical in by_name:
            mapped[bot] = by_name[canonical]
            continue
        for alt in NAME_ALIASES.get(bot, ()):
            if alt in by_name:
                mapped[bot] = by_name[alt]
                break
    return mapped


def create_profile(api_url: str, api_key: str, bot: str, proxy_url: str | None) -> str | None:
    from core.browser.nst_profile_safety import refuse_profile_creation

    refuse_profile_creation(context=f"sync_nst_dual_accounts create {bot}")
    name = f"Nst_{bot}"
    startup = STARTUP_URL_BY_BOT.get(bot, "https://example.com")
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    payload: dict = {
        "name": name,
        "platform": "mac",
        "kernel": "chromium",
        "kernelVersion": "126",
        "groupName": "Default",
        "startupUrls": [startup],
        "fingerprint": {
            "restoreLastSession": True,
            "doNotTrack": True,
            "userAgent": ua,
            "chromeVersion": "126",
            "navigator": {"webdriver": "false", "languages": ["en-US", "en"]},
        },
    }
    if proxy_url:
        payload["proxy"] = proxy_url
    r = requests.post(
        f"{api_url}/api/v2/profiles",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        print(f"  FAIL create {name}: HTTP {r.status_code} {r.text[:200]}")
        return None
    body = r.json()
    if body.get("code") not in (200, 0, None) and body.get("code") != 200:
        # some APIs use code 200 only
        if body.get("code") and body.get("code") != 200:
            print(f"  FAIL create {name}: {body.get('msg') or body}")
            return None
    inner = body.get("data") or {}
    pid = inner.get("profileId") or inner.get("id")
    if pid:
        print(f"  OK created {name} → {pid}")
        return str(pid)
    print(f"  FAIL create {name}: no id in {body}")
    return None


def sync_proxy(api_url: str, api_key: str, pid: str, proxy_url: str) -> bool:
    r = requests.put(
        f"{api_url}/api/v2/profiles/{pid}/proxy",
        headers=_headers(api_key),
        json={"url": proxy_url},
        timeout=15,
    )
    if not r.ok:
        print(f"  proxy FAIL {pid[:8]}… HTTP {r.status_code} {r.text[:120]}")
        return False
    body = r.json() if r.content else {}
    if isinstance(body, dict) and body.get("code") not in (None, 200, 0):
        print(f"  proxy FAIL {pid[:8]}… {body.get('msg')}")
        return False
    print(f"  proxy OK {pid[:8]}…")
    return True


def _update_env_file(vars_map: dict[str, str]) -> None:
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
    existing: dict[str, int] = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            existing[k] = i
    for k, v in vars_map.items():
        newline = f"{k}={v}\n"
        if k in existing:
            lines[existing[k]] = newline
        else:
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append(newline)
    env_path.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {len(vars_map)} keys to {env_path}")


def _write_infisical(vars_map: dict[str, str]) -> None:
    project = (
        os.getenv("INFISICAL_PROJECT_ID")
        or get_secret("INFISICAL_PROJECT_ID", "")
        or "a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe"
    )
    env_slug = os.getenv("INFISICAL_ENV") or get_secret("INFISICAL_ENV", "dev") or "dev"
    ok = 0
    for k, v in vars_map.items():
        if not v:
            continue
        cmd = [
            "infisical",
            "secrets",
            "set",
            f"{k}={v}",
            f"--projectId={project}",
            f"--env={env_slug}",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                ok += 1
                print(f"  Infisical set {k}")
            else:
                # try upsert via set with type
                print(f"  Infisical FAIL {k}: {(res.stderr or res.stdout)[:200]}")
        except Exception as exc:
            print(f"  Infisical ERROR {k}: {exc}")
    print(f"Infisical updated {ok}/{len(vars_map)} secrets")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync dual NSTBrowser accounts")
    ap.add_argument("--scan-only", action="store_true")
    ap.add_argument("--sync", action="store_true", help="Create missing on slot 2 + proxy sync")
    ap.add_argument("--write-env", action="store_true")
    ap.add_argument("--write-infisical", action="store_true")
    ap.add_argument("--api-key-1", default=None)
    ap.add_argument("--api-key-2", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--set-opens-1", type=int, default=None)
    ap.add_argument("--set-opens-2", type=int, default=None)
    ap.add_argument("--no-create", action="store_true")
    args = ap.parse_args()

    if args.set_opens_1 is not None:
        set_daily_opens(1, args.set_opens_1)
        print(f"Recorded daily opens slot1={args.set_opens_1}")
    if args.set_opens_2 is not None:
        set_daily_opens(2, args.set_opens_2)
        print(f"Recorded daily opens slot2={args.set_opens_2}")

    api_url = f"http://{args.host}:{args.port}"
    key1 = (args.api_key_1 or os.getenv("NSTBROWSER_API_KEY") or get_secret("NSTBROWSER_API_KEY", "") or "").strip()
    key2 = (args.api_key_2 or os.getenv("NSTBROWSER_API_KEY_2") or get_secret("NSTBROWSER_API_KEY_2", "") or "").strip()
    if not key1:
        print("Missing NSTBROWSER_API_KEY (slot 1)")
        return 1

    proxy = resolve_proxy_url(None) or get_secret("DATAIMPULSE_PROXY_URL", "") or get_secret("CAPMONSTER_PROXY_URL", "")
    proxy = (proxy or "").strip() or None
    print(f"API {api_url}")
    print(f"Proxy configured: {bool(proxy)} host={proxy.split('@')[-1] if proxy and '@' in proxy else (proxy or '-')}")

    print("\n=== Slot 1 (primary / tested) ===")
    p1 = list_profiles(api_url, key1)
    m1 = map_bot_profiles(p1)
    print(f"profiles total={len(p1)} mapped_bots={len(m1)}")
    for bot in REQUIRED_BOTS:
        print(f"  {bot:20} {m1.get(bot, 'MISSING')}")

    m2: dict[str, str] = {}
    if key2:
        print("\n=== Slot 2 (quota spare) ===")
        p2 = list_profiles(api_url, key2)
        m2 = map_bot_profiles(p2)
        print(f"profiles total={len(p2)} mapped_bots={len(m2)}")
        for bot in REQUIRED_BOTS:
            print(f"  {bot:20} {m2.get(bot, 'MISSING')}")
    else:
        print("\n(no slot 2 API key — pass --api-key-2 or set NSTBROWSER_API_KEY_2)")

    if args.scan_only and not args.sync:
        return 0

    if args.sync and key2:
        missing = [b for b in REQUIRED_BOTS if b not in m2]
        if missing and not args.no_create:
            # Allow create only for this explicit ops script when user set FORBID=0
            if os.getenv("NSTBROWSER_FORBID_CREATE", "1").strip() not in {"0", "false", "no", "off"}:
                print(
                    "\nREFUSED create on slot 2: set NSTBROWSER_FORBID_CREATE=0 for this run "
                    f"(missing: {missing})"
                )
            else:
                print(f"\nCreating missing on slot 2: {missing}")
                for bot in missing:
                    pid = create_profile(api_url, key2, bot, proxy)
                    if pid:
                        m2[bot] = pid
                    time.sleep(0.4)
        elif missing:
            print(f"Missing on slot 2 (no-create): {missing}")

        if proxy:
            print("\nSyncing DataImpulse proxy on slot 2 profiles…")
            for bot, pid in m2.items():
                sync_proxy(api_url, key2, pid, proxy)
            print("Syncing DataImpulse proxy on slot 1 profiles…")
            for bot, pid in m1.items():
                sync_proxy(api_url, key1, pid, proxy)

    # Build env/infisical payload (never print secret values beyond key names)
    out: dict[str, str] = {
        "BROWSER_VENDOR": "nstbrowser",
        "NSTBROWSER_API_KEY": key1,
        "NSTBROWSER_ACTIVE_SLOT": os.getenv("NSTBROWSER_ACTIVE_SLOT", "auto"),
        "NSTBROWSER_QUOTA_SOFT_LIMIT": os.getenv("NSTBROWSER_QUOTA_SOFT_LIMIT", "28"),
        "NSTBROWSER_FORBID_CREATE": "1",  # leave create locked after ops
        "NSTBROWSER_ROTATE_PROXY": "true",
    }
    if key2:
        out["NSTBROWSER_API_KEY_2"] = key2
    for bot, pid in m1.items():
        out[env_key_for_bot(bot, slot=1)] = pid
    # linkedin discovery often shares linkedin_it on slot 1
    if "linkedin_it" in m1:
        out.setdefault("NSTBROWSER_PROFILE_ID_LINKEDIN_DISCOVERY", m1["linkedin_it"])
        out.setdefault("NSTBROWSER_PROFILE_ID_LINKEDIN_DISCOVERY_IT", m1["linkedin_it"])
    for bot, pid in m2.items():
        out[env_key_for_bot(bot, slot=2)] = pid
    if "linkedin_it" in m2:
        out.setdefault("NSTBROWSER_PROFILE_ID_2_LINKEDIN_DISCOVERY", m2["linkedin_it"])
        out.setdefault("NSTBROWSER_PROFILE_ID_2_LINKEDIN_DISCOVERY_IT", m2["linkedin_it"])

    # Soft-force slot 2 when primary opens high (user reported 29/30)
    opens1 = os.getenv("NSTBROWSER_DAILY_OPENS_1")
    if opens1:
        out["NSTBROWSER_DAILY_OPENS_1"] = opens1

    if args.write_env or args.sync:
        _update_env_file(out)
    if args.write_infisical:
        _write_infisical(out)

    # Summary artifact (no secrets)
    art = ROOT / "artifacts" / "nst_dual_account_sync.json"
    art.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "slot1_mapped": m1,
        "slot2_mapped": m2,
        "slot1_missing": [b for b in REQUIRED_BOTS if b not in m1],
        "slot2_missing": [b for b in REQUIRED_BOTS if b not in m2],
        "proxy_host": proxy.split("@")[-1] if proxy and "@" in proxy else proxy,
    }
    art.write_text(__import__("json").dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSummary → {art}")
    print("Slot2 missing:", summary["slot2_missing"] or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
