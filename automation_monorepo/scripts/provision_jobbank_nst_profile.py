#!/usr/bin/env python3
"""Provision or bind the official Job Bank Direct Apply NST profile.

The normal path is to bind an existing profile after an operator signs in.
Creation is intentionally opt-in because NST profile quotas are limited.
The script always configures the profile with the static Webshare lane and
prints the secret mapping required by the GCP worker; it never writes secrets.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
for path in (REPO, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


BOT_NAME = "jobbank_it"
PROFILE_NAME = "Nst_jobbank_it"
STARTUP_URL = "https://www.jobbank.gc.ca/dashboard"


def _api_headers() -> dict[str, str]:
    from core.secret_manager import get_secret

    key = (os.getenv("NSTBROWSER_API_KEY") or get_secret("NSTBROWSER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("NSTBROWSER_API_KEY is required to provision the Job Bank profile")
    return {"x-api-key": key, "Content-Type": "application/json"}


def _webshare_proxy() -> str:
    old_bot = os.environ.get("BOT_NAME")
    old_portal = os.environ.get("JOB_QUEUE_PORTAL")
    try:
        os.environ["BOT_NAME"] = BOT_NAME
        os.environ["JOB_QUEUE_PORTAL"] = "jobbank"
        from core.secret_manager import _looks_webshare_proxy, get_browser_proxy_url

        proxy = (get_browser_proxy_url() or "").strip()
        if not _looks_webshare_proxy(proxy):
            raise RuntimeError("Job Bank requires a static Webshare proxy; configure WEBSHARE_PROXY_URL")
        return proxy
    finally:
        if old_bot is None:
            os.environ.pop("BOT_NAME", None)
        else:
            os.environ["BOT_NAME"] = old_bot
        if old_portal is None:
            os.environ.pop("JOB_QUEUE_PORTAL", None)
        else:
            os.environ["JOB_QUEUE_PORTAL"] = old_portal


def _create_profile(api_url: str, headers: dict[str, str], proxy_payload: dict[str, str]) -> str:
    from core.browser.nst_profile_safety import refuse_profile_creation

    refuse_profile_creation(context="provision_jobbank_nst_profile --create")
    payload = {
        "name": PROFILE_NAME,
        "platform": "linux",
        "kernel": "chromium",
        "groupName": "Default",
        "startupUrls": [STARTUP_URL],
        "proxyConfig": proxy_payload,
    }
    response = requests.post(f"{api_url}/api/v2/profiles", json=payload, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("code") not in {0, 200}:
        raise RuntimeError(f"NST profile creation failed: {data.get('msg') or data}")
    body = data.get("data") or {}
    profile_id = str(body.get("profileId") or body.get("id") or "").strip()
    if not profile_id:
        raise RuntimeError("NST profile creation response did not include a profile ID")
    return profile_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id", help="Existing signed-in NST Job Bank profile UUID")
    parser.add_argument("--create", action="store_true", help="Create a fresh Job Bank NST profile")
    parser.add_argument("--confirm-create", action="store_true", help="Required with --create")
    parser.add_argument("--host", default=os.getenv("NSTBROWSER_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NSTBROWSER_API_PORT", "8848")))
    args = parser.parse_args()
    if bool(args.profile_id) == bool(args.create):
        parser.error("provide exactly one of --profile-id or --create")
    if args.create and not args.confirm_create:
        parser.error("--create requires --confirm-create")

    from core.browser.nst_proxy import nst_proxy_payload, safe_proxy_host

    api_url = f"http://{args.host}:{args.port}"
    headers = _api_headers()
    proxy_url = _webshare_proxy()
    payload = nst_proxy_payload(proxy_url)
    profile_id = args.profile_id.strip() if args.profile_id else _create_profile(api_url, headers, payload)
    response = requests.put(
        f"{api_url}/api/v2/profiles/{profile_id}/proxy",
        json=payload,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") not in {0, 200}:
        raise RuntimeError(f"NST proxy configuration failed: {data.get('msg') or data}")
    print(f"Job Bank NST profile ready: {profile_id} (Webshare {safe_proxy_host(proxy_url)})")
    print(f"Set in GCP secrets: NSTBROWSER_PROFILE_ID_JOBBANK_IT={profile_id}")
    print("Log in to Job Bank and upload the configured resume/cover letter before enabling workers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
