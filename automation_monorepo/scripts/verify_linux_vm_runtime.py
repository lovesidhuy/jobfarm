#!/usr/bin/env python3
"""Fail-closed preflight for the Linux NSTbrowser worker."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _profile_docs(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    profiles = data.get("docs") or data.get("list") or []
    return [item for item in profiles if isinstance(item, dict)]


def _profile_id(profile: dict[str, Any]) -> str:
    # The API also returns an internal Mongo-style ``_id``; browser launch
    # endpoints require the stable UUID in ``profileId``.
    for key in ("profileId", "profile_id", "id", "_id"):
        value = profile.get(key)
        if value is not None:
            return str(value)
    return ""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _warm_browser_runtime(
    api_url: str,
    headers: dict[str, str],
    profile_id: str,
    *,
    attempts: int = 12,
) -> None:
    try:
        # Check if profile is already running
        status_url = f"{api_url}/api/v2/browsers"
        status_resp = requests.get(status_url, headers=headers, timeout=10)
        if status_resp.ok:
            status_data = status_resp.json()
            if status_data.get("code") == 0 or status_data.get("code") == 200:
                active_browsers = status_data.get("data")
                if isinstance(active_browsers, list):
                    for browser_info in active_browsers:
                        if browser_info and str(browser_info.get("profileId")) == str(profile_id):
                            print(f"NSTbrowser profile {profile_id} is already running. Skipping warm-up.")
                            return
    except Exception as exc:
        print(f"Warning: Failed to check active browsers during warm-up check: {exc}")

    url = f"{api_url}/api/v2/browsers/{profile_id}"
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json={"headless": False, "autoClose": False},
                timeout=180,
            )
            if response.ok:
                requests.delete(url, headers=headers, timeout=30).raise_for_status()
                return
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code == 400 and '"code":6001' in response.text.replace(" ", ""):
                break
        except requests.RequestException as exc:
            last_error = str(exc)
        if attempt < attempts:
            time.sleep(10)
    raise RuntimeError(f"NSTbrowser runtime warm-up failed for {profile_id}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", action="append", required=True)
    parser.add_argument("--warm", action="store_true", help="Warm up the browser runtime")
    parser.add_argument(
        "--report-json",
        action="store_true",
        help="Emit a machine-readable readiness report without exposing credentials",
    )
    args = parser.parse_args()

    vendor = _required("BROWSER_VENDOR").lower()
    _local_chrome_vendors = {"chrome", "google-chrome", "regular-chrome", "normal-chrome", "local"}
    if vendor not in {"nstbrowser", "nst"} and vendor not in _local_chrome_vendors:
        raise RuntimeError(f"BROWSER_VENDOR must be nstbrowser or chrome, got {vendor!r}")

    if vendor in _local_chrome_vendors:
        # Check standard requirements for local Chrome run
        _required("INFISICAL_CLIENT_ID")
        _required("INFISICAL_CLIENT_SECRET")
        _required("INFISICAL_PROJECT_SLUG")
        _required("INFISICAL_ENV")
        from core.secret_manager import get_secret, normalize_proxy_url
        proxy_url = get_secret("PROXY_URL", "").strip()
        if not proxy_url:
            raise RuntimeError("PROXY_URL was not returned by Infisical")
        if not any(
            get_secret(key, "").strip()
            for key in ("CAPMONSTER_API_KEY", "CAPMONSTER_CLIENT_KEY", "capkey")
        ):
            raise RuntimeError("No CapMonster API key was returned by Infisical")
        
        # Verify package availability
        import seleniumbase
        import playwright
        
        print(f"Linux Chrome VM preflight passed for local Chromes.")
        return 0

    _required("INFISICAL_CLIENT_ID")
    _required("INFISICAL_CLIENT_SECRET")
    _required("INFISICAL_PROJECT_SLUG")
    _required("INFISICAL_ENV")
    _required("JOBBOTS_PROFILE_LEASE_TABLE")
    host = os.environ.get("NSTBROWSER_API_HOST", "127.0.0.1").strip()
    port = os.environ.get("NSTBROWSER_API_PORT", "8848").strip()
    api_url = f"http://{host}:{port}"

    # NSTBrowser does not expose a stable account-quota API in the local agent.
    # The daily counters are therefore operator/dashboard telemetry persisted in
    # Infisical or artifacts. Treat a known soft-limit breach as a hard block;
    # an unknown value is reported clearly rather than guessed.
    from core.browser.nst_accounts import (
        daily_opens_for_slot,
        resolve_api_key,
        resolve_profile_id,
        soft_quota_limit,
    )

    quota_limit = soft_quota_limit()
    quota: dict[str, dict[str, int | str | None]] = {}
    for slot in (1, 2):
        raw = os.environ.get(f"NSTBROWSER_DAILY_OPENS_{slot}", "").strip()
        opens = daily_opens_for_slot(slot)
        if raw and opens is None:
            raise RuntimeError(f"NSTBROWSER_DAILY_OPENS_{slot} must be an integer")
        quota[str(slot)] = {
            "opens": opens,
            "soft_limit": quota_limit,
            "status": "unknown" if opens is None else ("blocked" if opens >= quota_limit else "available"),
            "source": "dashboard_override" if raw else ("observed_launches" if opens is not None else "unknown"),
        }

    selected_profiles: list[tuple[str, int, str, str]] = []
    slots_in_use: set[int] = set()
    for bot_name in args.bot:
        try:
            slot, profile_id, key = resolve_profile_id(bot_name)
        except Exception as exc:
            raise RuntimeError(f"Failed to resolve profile ID for {bot_name}: {exc}")
        if not profile_id:
            raise RuntimeError(f"{key} is required")
        opens = quota[str(slot)]["opens"]
        if (
            isinstance(opens, int)
            and opens >= quota_limit
            and os.environ.get("NSTBROWSER_ALLOW_QUOTA_OVERRIDE", "").strip().lower()
            not in {"1", "true", "yes", "on"}
        ):
            raise RuntimeError(
                f"NSTBrowser slot {slot} is at its soft daily open limit "
                f"({opens}/{quota_limit}) for {bot_name}. Add healthy slot-{3 - slot} "
                "credentials/profile mapping or explicitly set NSTBROWSER_ALLOW_QUOTA_OVERRIDE=1."
            )
        selected_profiles.append((bot_name, slot, profile_id, key))
        slots_in_use.add(slot)

    profiles_by_slot: dict[int, dict[str, dict[str, Any]]] = {}
    headers_by_slot: dict[int, dict[str, str]] = {}
    url = f"{api_url}/api/v2/profiles?pageNo=1&pageSize=100"
    for slot in sorted(slots_in_use):
        try:
            resolved_slot, api_key = resolve_api_key(slot=slot)
        except Exception as exc:
            raise RuntimeError(f"Failed to resolve NST API key for slot {slot}: {exc}")
        if resolved_slot != slot:
            raise RuntimeError(f"NSTBrowser slot {slot} is selected but has no API key")
        headers = {"x-api-key": api_key}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        profiles_by_slot[slot] = {
            _profile_id(profile): profile for profile in _profile_docs(response.json())
        }
        headers_by_slot[slot] = headers

    from core.secret_manager import get_secret, normalize_proxy_url

    proxy_url = get_secret("PROXY_URL", "").strip()
    if not proxy_url:
        raise RuntimeError("PROXY_URL was not returned by Infisical")
    proxy_url = normalize_proxy_url(proxy_url)
    proxy_host = urlparse(proxy_url).hostname
    if not proxy_host:
        raise RuntimeError("PROXY_URL returned by Infisical has no hostname")
    if not any(
        get_secret(key, "").strip()
        for key in ("CAPMONSTER_API_KEY", "CAPMONSTER_CLIENT_KEY", "capkey")
    ):
        raise RuntimeError("No CapMonster API key was returned by Infisical")

    # Proxy probes are advisory for primary PROXY_URL (browser egress can work
    # even when a single httpbin-style probe flakes with 407). Hard-fail only
    # when *no* configured tier probes clean — that blocks discovery entirely.
    try:
        from core.discovery.scrape_proxy import probe_proxy_url, resolve_proxy_tiers

        tiers = resolve_proxy_tiers()
        probes: list[tuple[str, str]] = [("PROXY_URL", proxy_url)]
        if tiers.webshare:
            probes.append(("webshare", tiers.webshare))
        if tiers.dataimpulse and tiers.dataimpulse != proxy_url:
            probes.append(("dataimpulse", tiers.dataimpulse))
        working = 0
        primary_ok = False
        for label, url in probes:
            ok, detail = probe_proxy_url(url, timeout=15.0)
            if ok:
                working += 1
                if label == "PROXY_URL":
                    primary_ok = True
                print(f"Proxy probe OK {label}: {detail}")
            else:
                print(f"Proxy probe FAIL {label}: {detail}", file=sys.stderr)
                if label == "PROXY_URL" and ("407" in detail or "Authentication" in detail):
                    # Soft warn: NST profile may still have a working proxy-cheap
                    # session; hard-failing here previously left production dead
                    # after bootstrap step 5 stopped all bots (2026-07-25 incident).
                    print(
                        "Warning: primary PROXY_URL probe failed auth; "
                        "continuing if another discovery tier or NST profiles are healthy",
                        file=sys.stderr,
                    )
        if working == 0:
            raise RuntimeError(
                "No discovery/apply proxy passed the health probe "
                "(check WEBSHARE / Proxy-Cheap / DataImpulse credentials)"
            )
        if not primary_ok:
            print(
                "Warning: PROXY_URL probe failed but at least one other tier is OK",
                file=sys.stderr,
            )
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"Warning: proxy health probe skipped: {exc}", file=sys.stderr)

    missing: list[str] = []
    cookie_sync_disabled: list[str] = []
    profile_sync_disabled: list[str] = []
    missing_cloud_cookies: list[str] = []
    proxy_mismatches: list[str] = []
    for bot_name, slot, profile_id, key in selected_profiles:
        profile = profiles_by_slot[slot].get(profile_id)
        if profile is None:
            missing.append(f"{bot_name} ({key}, slot {slot})")
            continue
        group = profile.get("group") if isinstance(profile.get("group"), dict) else {}
        settings = group.get("settings") if isinstance(group.get("settings"), dict) else {}
        if settings.get("syncCookies") is not True:
            cookie_sync_disabled.append(f"{bot_name} ({profile_id})")
        if settings.get("syncProfileHistoryData") is not True:
            profile_sync_disabled.append(f"{bot_name} ({profile_id})")
        if not profile.get("cookies"):
            missing_cloud_cookies.append(f"{bot_name} ({profile_id})")
        proxy_result = profile.get("proxyResult")
        profile_proxy_ip = (
            str(proxy_result.get("ip", "")).strip()
            if isinstance(proxy_result, dict)
            else ""
        )
        if profile_proxy_ip and profile_proxy_ip != proxy_host:
            proxy_mismatches.append(
                f"{bot_name} ({profile_proxy_ip} != Infisical {proxy_host})"
            )

    if missing:
        raise RuntimeError(
            "Configured NSTbrowser profile IDs were not returned by the local API: "
            + ", ".join(missing)
        )
    if cookie_sync_disabled:
        print(
            "Warning: NSTbrowser cookie sync is disabled for: "
            + ", ".join(cookie_sync_disabled),
            file=sys.stderr,
        )
    if profile_sync_disabled:
        print(
            "Warning: NSTbrowser browser profile data storage is not set to cloud for: "
            + ", ".join(profile_sync_disabled),
            file=sys.stderr,
        )
    if missing_cloud_cookies:
        print(
            "Warning: NSTbrowser API returned no synchronized cloud cookies for: "
            + ", ".join(missing_cloud_cookies),
            file=sys.stderr,
        )
    if proxy_mismatches and os.environ.get("NSTBROWSER_SYNC_PROFILE_PROXY", "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        raise RuntimeError(
            "NSTbrowser profile proxy does not match Infisical PROXY_URL: "
            + ", ".join(proxy_mismatches)
        )
    if proxy_mismatches:
        print(
            "Warning: NSTbrowser profiles use their own saved proxies (preserved for session continuity): "
            + ", ".join(proxy_mismatches),
            file=sys.stderr,
        )

    if args.warm:
        # This is intentionally opt-in: opening a profile consumes scarce NST
        # quota. Normal farm readiness uses the non-invasive checks above.
        _, first_slot, first_profile_id, _ = selected_profiles[0]
        _warm_browser_runtime(api_url, headers_by_slot[first_slot], first_profile_id)
    else:
        print("Skipping browser runtime warm-up (use --warm to enable)")

    report = {
        "status": "ready",
        "browser_opened": bool(args.warm),
        "quota": quota,
        "profiles": [
            {"bot": bot_name, "slot": slot, "configured": True}
            for bot_name, slot, _, _ in selected_profiles
        ],
        "warnings": {
            "cookie_sync_disabled": cookie_sync_disabled,
            "profile_sync_disabled": profile_sync_disabled,
            "missing_cloud_cookies": missing_cloud_cookies,
            "saved_proxy_mismatches": proxy_mismatches,
        },
    }
    if args.report_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"Linux NSTbrowser VM preflight passed for: {', '.join(args.bot)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Linux NSTbrowser VM preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
