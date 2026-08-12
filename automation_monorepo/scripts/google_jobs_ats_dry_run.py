#!/usr/bin/env python3
"""Google CDP → Greenhouse/Lever dry-run (JobSpy Google replacement).

Uses Chromium via Playwright. Preferred: attach to a warm Chrome session
(``EXISTING_CDP_PORT`` / ``GOOGLE_CDP_URL``) — fresh headless Chromium hits
Google ``/sorry`` even after CapMonster token inject.

Fallback (cold launch): scrape proxy ladder + CapMonster on ``/sorry``.

Usage:
  # Warm Chrome (recommended — you already have one on :9222)
  EXISTING_CDP_PORT=9222 .venv/bin/python scripts/google_jobs_ats_dry_run.py

  .venv/bin/python scripts/google_jobs_ats_dry_run.py --mode web --terms "software engineer"
  .venv/bin/python scripts/google_jobs_ats_dry_run.py --mode jobs --headed
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "artifacts" / "wave-google-ats"

_DEFAULT_TERMS = [
    # IT / QA first — Google site:greenhouse|lever (not Indeed JobSpy)
    "QA Analyst",
    "QA Engineer",
    "SDET",
    "IT Support",
    "Help Desk Analyst",
    "Service Desk Analyst",
    "Desktop Support",
    "Technical Support Analyst",
    "Systems Administrator",
    "IT Intern",
    "IT Co-op",
]


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms", nargs="+", default=None)
    parser.add_argument("--location", default="Vancouver, BC")
    parser.add_argument("--max-per", type=int, default=15)
    parser.add_argument(
        "--mode",
        choices=["web", "jobs", "both", "tavily"],
        default=None,
        help="web=site:greenhouse|lever (default), jobs=Google Jobs widget, both, tavily=API fail-safe",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep already-applied / IMAP-matched URLs",
    )
    parser.add_argument(
        "--no-it-filter",
        action="store_true",
        help="Do not apply IT persona title filter",
    )
    parser.add_argument(
        "--refresh-imap",
        action="store_true",
        help="Refresh email_applied_history from IMAP before dedupe",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _load_dotenv()

    # CapMonster on for this path
    os.environ.setdefault("USE_CAPMONSTER_CAPTCHA_SOLVER", "1")
    os.environ.setdefault("USE_CAPMONSTER", "1")
    if args.headed:
        os.environ["GOOGLE_CDP_HEADLESS"] = "0"
    if args.mode:
        os.environ["GOOGLE_CDP_MODE"] = args.mode

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    from core.discovery.providers.base import DiscoveryRequest
    from core.discovery.providers.google_cdp_provider import (
        GoogleCDPProvider,
        canonicalize_ats_url,
    )

    terms = args.terms or _DEFAULT_TERMS
    request = DiscoveryRequest(
        profile="it",
        search_terms=terms,
        locations=[args.location],
        max_results_per_term=args.max_per,
        freshness_days=7,
        radius_km=40,
        easy_apply_only=False,
    )
    provider = GoogleCDPProvider(mode=args.mode)
    raw_jobs = provider.discover(request)

    apply_urls: list[str] = []
    seen: set[str] = set()
    targets: list[dict] = []
    for job in raw_jobs:
        url = canonicalize_ats_url(job.destination_url or job.listing_url)
        if not url or url in seen:
            continue
        seen.add(url)
        apply_urls.append(url)
        targets.append({
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "apply_url": url,
            "search_term": (job.raw_extras or {}).get("search_term"),
            "mode": (job.raw_extras or {}).get("google_mode"),
        })

    skipped: list[dict] = []
    stats: dict = {}
    if not args.no_dedupe or not args.no_it_filter:
        from core.shared_modules.ats_lead_dedupe import finalize_ats_leads

        targets, skipped, stats = finalize_ats_leads(
            targets,
            artifacts_dir=OUT_DIR,
            include_mongo=False,
            include_email=not args.no_dedupe,
            refresh_imap=bool(args.refresh_imap),
            it_only=not args.no_it_filter,
        )
        apply_urls = [t["apply_url"] for t in targets if t.get("apply_url")]
        print(
            f"Post-filter: fresh={stats.get('fresh')} skipped={stats.get('skipped')} "
            f"title_reject={stats.get('skipped_title')}",
            flush=True,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "ts": stamp,
        "provider": "google_cdp",
        "mode": provider.mode,
        "location": args.location,
        "terms": terms,
        "counts": {
            "raw_jobs": len(raw_jobs),
            "unique_apply_urls_before_filter": len(seen),
            "unique_apply_urls": len(apply_urls),
            "skipped": len(skipped),
            **{f"filter_{k}": v for k, v in (stats or {}).items()},
        },
        "apply_urls": apply_urls,
        "targets": targets,
        "skipped": [
            {
                "title": s.get("title"),
                "company": s.get("company"),
                "apply_url": s.get("apply_url"),
                "reason": s.get("dedupe_skip") or s.get("filter_skip"),
            }
            for s in skipped
        ],
    }
    out_json = OUT_DIR / f"cdp_dry_run_{stamp}.json"
    out_txt = OUT_DIR / f"apply_urls_{stamp}.txt"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_txt.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")

    latest_jobs = [
        {
            "title": t.get("title") or "",
            "company": t.get("company") or "",
            "location": t.get("location") or "",
            "apply_url": t.get("apply_url") or "",
            "search_term": t.get("search_term"),
            "source": "google_cdp",
        }
        for t in targets
    ]
    latest_report = {
        "stamp": stamp,
        "cleared_prior_polluted_list": True,
        "method": "google_cdp_provider",
        "filters": "Google site:greenhouse|lever; Metro Van; URL+IMAP dedupe; IT titles only",
        "kept": len(latest_jobs),
        "skipped": len(skipped),
        "urls_file": f"artifacts/wave-google-ats/metro_van_it_urls_{stamp}.txt",
        "jobs": latest_jobs,
    }
    (OUT_DIR / "metro_van_it_latest.json").write_text(
        json.dumps(latest_report, indent=2), encoding="utf-8"
    )
    (OUT_DIR / f"metro_van_it_{stamp}.json").write_text(
        json.dumps(latest_report, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "metro_van_it_urls_latest.txt").write_text(
        "\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8"
    )
    (OUT_DIR / f"metro_van_it_urls_{stamp}.txt").write_text(
        "\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8"
    )

    print(json.dumps(report["counts"], indent=2))
    print(f"mode={provider.mode}")
    print(f"wrote {out_json}")
    print(f"wrote {out_txt}")
    print(f"wrote {OUT_DIR / 'metro_van_it_latest.json'}")
    if apply_urls:
        print("\n--- Fresh IT Greenhouse / Lever apply URLs ---")
        for j in latest_jobs:
            print(f"  {j.get('title')} @ {j.get('company')} | {j.get('apply_url')}")
    else:
        print("\nNo fresh IT Greenhouse/Lever apply URLs found in this run.")
    return 0 if apply_urls else 2


if __name__ == "__main__":
    raise SystemExit(main())
