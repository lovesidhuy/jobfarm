#!/usr/bin/env python3
"""Create/update the jobbots Datadog monitors (idempotent, keyed by name).

Run from anywhere with network access to Datadog (your Mac is fine):

    python scripts/setup_datadog_monitors.py            # upsert all monitors
    python scripts/setup_datadog_monitors.py --list     # show current state

Requires TWO keys (both resolvable via env / .env / Infisical):
    DD_API_KEY  - org API key (you already added this)
    DD_APP_KEY  - an Application Key. Create one in Datadog:
                  Organization Settings -> Application Keys -> New Key.
                  The monitors API rejects requests without it.
Optional:
    DD_SITE     - defaults to datadoghq.com (use datadoghq.eu for EU orgs)

Metric names carry the DogStatsD namespace prefix "jobbots." (see
core/datadog_metrics.py). Notification routing (@-handles) is left to the
Datadog UI so re-running this script never clobbers your recipients.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.secret_manager import get_secret  # noqa: E402

MSG = (
    "{{#is_alert}}Check the jobbots VM / supervisor logs.{{/is_alert}} "
    "Managed by scripts/setup_datadog_monitors.py - edit thresholds there."
)

MONITORS: list[dict] = [
    {
        "name": "[jobbots] No applies in 3h",
        "type": "query alert",
        "query": "sum(last_3h):sum:jobbots.bot.applications{event:applied}.as_count() < 1",
        "message": "No successful applications across the whole fleet in 3 hours. " + MSG,
        "options": {
            # VM is off most of the day - no data must NOT page.
            "notify_no_data": False,
            "thresholds": {"critical": 1},
        },
    },
    {
        "name": "[jobbots] Bot marked UNHEALTHY",
        "type": "query alert",
        "query": "sum(last_10m):sum:jobbots.supervisor.unhealthy{*} by {bot}.as_count() >= 1",
        "message": "{{bot.name}} hit the crash budget (3 crashes/10 min) and is blocked for 30 min. " + MSG,
        "options": {"notify_no_data": False, "thresholds": {"critical": 1}},
    },
    {
        "name": "[jobbots] Crash spike",
        "type": "query alert",
        "query": "sum(last_30m):sum:jobbots.supervisor.bot_exit{outcome:crash} by {bot}.as_count() >= 3",
        "message": "{{bot.name}} crashed 3+ times in 30 minutes. " + MSG,
        "options": {"notify_no_data": False, "thresholds": {"critical": 3}},
    },
    {
        "name": "[jobbots] Captcha failures piling up",
        "type": "query alert",
        "query": "sum(last_1h):sum:jobbots.captcha.solve{outcome:failed} by {bot,kind}.as_count() > 10",
        "message": "{{bot.name}} failed {{kind.name}} solves >10x in 1h - check CapMonster key/proxy alignment. " + MSG,
        "options": {"notify_no_data": False, "thresholds": {"critical": 10}},
    },
    {
        "name": "[jobbots] Disk usage high",
        "type": "query alert",
        "query": "avg(last_5m):avg:system.disk.in_use{*} by {host,device} > 0.85",
        "message": "Disk >85% on {{host.name}} {{device.name}} - Mongo/logs may be filling C:. " + MSG,
        "options": {"notify_no_data": False, "thresholds": {"critical": 0.85, "warning": 0.75}},
    },
    {
        "name": "[jobbots] Supervisor process down",
        "type": "service check",
        "query": "'process.up'.over('process:jobbots_supervisor').by('host').last(4).count_by_status()",
        "message": "supervisor.py is not running on {{host.name}}. " + MSG,
        "options": {
            # No data here just means the VM is stopped - don't page.
            "notify_no_data": False,
            "thresholds": {"critical": 3, "warning": 2, "ok": 1},
        },
    },
]


def _resolve(name: str) -> str:
    return (get_secret(name, "") or "").strip()


def _api(site: str) -> str:
    return f"https://api.{site}/api/v1"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="List managed monitors and exit")
    args = ap.parse_args()

    api_key = _resolve("DD_API_KEY")
    app_key = _resolve("DD_APP_KEY")
    site = _resolve("DD_SITE") or "datadoghq.com"

    if not api_key:
        print("ERROR: DD_API_KEY not found (env / .env / Infisical).")
        return 1
    if not app_key:
        print("ERROR: DD_APP_KEY not found. Create one in Datadog:")
        print("  Organization Settings -> Application Keys -> New Key")
        print("  then add it to Infisical as DD_APP_KEY.")
        return 1

    headers = {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }
    base = _api(site)

    # Fetch existing monitors once; match ours by exact name.
    resp = requests.get(f"{base}/monitor", headers=headers,
                        params={"name": "[jobbots]"}, timeout=30)
    resp.raise_for_status()
    existing = {m["name"]: m["id"] for m in resp.json()}

    if args.list:
        for mon in MONITORS:
            state = f"exists (id {existing[mon['name']]})" if mon["name"] in existing else "missing"
            print(f"  {mon['name']}: {state}")
        return 0

    for mon in MONITORS:
        payload = {**mon, "tags": ["managed-by:jobbots-repo"]}
        if mon["name"] in existing:
            mid = existing[mon["name"]]
            r = requests.put(f"{base}/monitor/{mid}", headers=headers, json=payload, timeout=30)
            action = "updated"
        else:
            r = requests.post(f"{base}/monitor", headers=headers, json=payload, timeout=30)
            action = "created"
        if r.status_code >= 300:
            print(f"  FAILED {mon['name']}: {r.status_code} {r.text[:200]}")
        else:
            print(f"  {action}: {mon['name']} (id {r.json().get('id')})")

    print("\nDone. Add notification @-handles (email/Slack) to each monitor in the Datadog UI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
