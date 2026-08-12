#!/usr/bin/env python3
"""Screen harvested Greenhouse/Lever leads using the AI gate.

Reads the collected Metro Van IT leads and filters them to keep only those
that match the candidate's IT profile (IT Support, QA, SysAdmin, etc.) and
experience level.

Usage:
  .venv/bin/python scripts/screen_harvested_leads.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "master" / "it_indeed cwgeopy" / "Auto_indeed"))

# Meta-programming mapping trick to resolve modules.indeed imports
import importlib.util
def mock_import(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

mock_import("modules.indeed._bootstrap", str(ROOT.parent / "jobbots/core/shared_modules/indeed/_bootstrap.py"))
mock_import("modules.indeed.navigation", str(ROOT.parent / "jobbots/core/shared_modules/indeed/navigation.py"))

from core.supervisor_runtime import merge_dotenv_into_env
merge_dotenv_into_env(os.environ, ROOT / ".env", override=True)

# Set up CapMonster env
os.environ.setdefault("USE_CAPMONSTER_CAPTCHA_SOLVER", "1")
os.environ.setdefault("USE_CAPMONSTER", "1")

from core.discovery._gate_adapter import screen_job

LEAD_JSON = ROOT / "artifacts" / "wave-google-ats" / "metro_van_it_latest.json"
OUT_DIR = ROOT / "artifacts" / "wave-google-ats"

def main() -> int:
    if not LEAD_JSON.is_file():
        print(f"Error: Lead file not found at: {LEAD_JSON}", file=sys.stderr)
        return 1

    data = json.loads(LEAD_JSON.read_text(encoding="utf-8"))
    jobs = data.get("jobs") or []
    if not jobs:
        print("No jobs found in the lead file.", file=sys.stderr)
        return 2

    print(f"Loaded {len(jobs)} jobs for AI screening filter...")
    passed_jobs = []
    rejected_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-CA", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(15000)

        for i, job in enumerate(jobs, 1):
            url = job["apply_url"]
            title = job.get("title") or ""
            company = job.get("company") or ""
            location = job.get("location") or ""
            
            print(f"\n[{i}/{len(jobs)}] Checking {title} @ {company}...")
            try:
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(0.5)
                desc = page.locator("body").inner_text() or ""
                
                # Screen via existing IT AI gate
                passed, score, reason = screen_job(
                    title=title,
                    company=company,
                    description=desc,
                    location=location,
                    profile="it"
                )
                
                if passed:
                    print(f"  → PASSED (Score: {score})")
                    passed_jobs.append(job)
                else:
                    print(f"  → REJECTED (Reason: {reason})")
                    rejected_count += 1
            except Exception as exc:
                print(f"  [Error] Failed to fetch or screen: {exc}")
                # Keep defensively on navigation timeouts
                passed_jobs.append(job)
                
        browser.close()

    # Rewrite output files
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    apply_urls = [j["apply_url"] for j in passed_jobs]

    report = {
        "ts": stamp,
        "provider": "jobspy_indeed_direct_screened",
        "location": data.get("location", "Vancouver, BC"),
        "terms": data.get("terms", []),
        "counts": {
            "raw_jobs": data.get("counts", {}).get("raw_jobs", 0),
            "unique_apply_urls": len(apply_urls),
        },
        "apply_urls": apply_urls,
        "targets": [{**j, "mode": "jobspy_direct_screened"} for j in passed_jobs],
    }

    out_json = OUT_DIR / f"cdp_dry_run_{stamp}.json"
    out_txt = OUT_DIR / f"apply_urls_{stamp}.txt"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_txt.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")

    latest_report = {
        "stamp": stamp,
        "cleared_prior_polluted_list": True,
        "method": "jobspy_indeed_direct_screened",
        "filters": "IT/QA/support/sysadmin only; Metro Van BC; AI Screened",
        "kept": len(passed_jobs),
        "urls_file": f"artifacts/wave-google-ats/metro_van_it_urls_{stamp}.txt",
        "jobs": passed_jobs
    }

    latest_json = OUT_DIR / "metro_van_it_latest.json"
    latest_json_stamped = OUT_DIR / f"metro_van_it_{stamp}.json"
    latest_json.write_text(json.dumps(latest_report, indent=2), encoding="utf-8")
    latest_json_stamped.write_text(json.dumps(latest_report, indent=2), encoding="utf-8")

    latest_urls = OUT_DIR / "metro_van_it_urls_latest.txt"
    latest_urls_stamped = OUT_DIR / f"metro_van_it_urls_{stamp}.txt"
    latest_urls.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")
    latest_urls_stamped.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")

    print("\n--- Screening Complete ---")
    print(f"Total input jobs: {len(jobs)}")
    print(f"Passed: {len(passed_jobs)}")
    print(f"Rejected: {rejected_count}")
    print(f"Updated: {latest_json}")
    
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
