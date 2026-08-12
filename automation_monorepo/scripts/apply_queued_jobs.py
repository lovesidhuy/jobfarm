#!/usr/bin/env python3
"""Apply to queued GH/Lever jobs by navigating then applying directly.

Bypasses the restrictive URL filtering by using apply_on_page after
manual navigation. Works for custom-domain Greenhouse/Lever pages.
"""

import os
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("JOB_PROFILE", "IT")
try:
    from core.shared_modules.ats_apply import apply_on_page
    from playwright.sync_api import sync_playwright
except Exception as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(1)

# Job URLs (all GH/Lever leads from queue, excluding Kabam)
DEFAULT_URLS = [
    "https://www.asana.com/jobs/apply/7968162?gh_jid=7968162",
    "https://www.asana.com/jobs/apply/7979618?gh_jid=7979618",
    "https://www.asana.com/jobs/apply/7961454?gh_jid=7961454",
    "http://block.xyz/careers/jobs/5196175008?gh_jid=5196175008",
    "https://www.brex.com/careers/8523430002?gh_jid=8523430002",
    "https://www.brex.com/careers/8603327002?gh_jid=8603327002",
    "https://www.d2l.com/careers/jobs/?job_id=260466&gh_jid=260466",
    "https://www.d2l.com/careers/jobs/?job_id=7455458&gh_jid=7455458",
    "https://app.careerpuck.com/job-board/prenuvo/job/4698993005?gh_jid=4698993005",
]

def main():
    urls = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_URLS
    if not urls:
        print("No URLs provided.", file=sys.stderr)
        sys.exit(1)

    headless = bool(os.getenv("ATS_HEADLESS", "True"))
    print(f"Applying to {len(urls)} job(s); headless={headless}")

    shot_dir = ROOT / "artifacts" / "apply_screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="en-CA", viewport={"width": 1280, "height": 900})

        for i, url in enumerate(urls, 1):
            title = f"Job {i}"
            company = "Unknown"
            start_time = time.time()
            print(f"\n[{i}/{len(urls)}] {title} @ {company}")
            print(f"  URL: {url}")

            page = context.new_page()
            page.set_default_timeout(45000)

            ok = False
            result_url = ""
            reason = ""

            try:
                # Navigate to the job application page
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                print(f"  Page loaded: {page.url[:100]}")

                # Apply on the already-opened page
                ok, result_url, reason = apply_on_page(page, title=title, company=company)
            except Exception as exc:
                ok = False
                result_url = url
                reason = f"Exception: {type(exc).__name__}: {exc}"
            finally:
                elapsed = round(time.time() - start_time, 1)
                # Always handle screenshot on failure
                if not ok:
                    try:
                        safe_name = "".join(ch if ch.isalnum() else "_" for ch in url[:60]) + f"_fail_{int(time.time())}.png"
                        shot_path = shot_dir / safe_name
                        page.screenshot(path=str(shot_path), full_page=True)
                        reason += f" | Screenshot: {shot_path}"
                        print(f"  FAILED ({elapsed}s): {reason} | Screenshot saved")
                    except Exception as e:
                        print(f"  FAILED ({elapsed}s): {reason} | Screenshot error: {e}")
                else:
                    print(f"  SUCCESS ({elapsed}s): {reason}")

                page.close()

            results.append({
                "url": url,
                "ok": ok,
                "reason": reason,
                "elapsed": elapsed,
            })
            time.sleep(1.5)  # Polite delay between applications

        browser.close()

    # Write results
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = ROOT / "artifacts" / "wave-google-ats" / f"custom_apply_results_{stamp}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": stamp,
        "headless": headless,
        "counts": {
            "attempted": len(urls),
            "ok": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
        },
        "results": results,
    }
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_file}")
    print(f"Summary: {payload['counts']['ok']}/{payload['counts']['attempted']} successful")

    return 0 if all(r["ok"] for r in results) else 1

if __name__ == "__main__":
    sys.exit(main())
