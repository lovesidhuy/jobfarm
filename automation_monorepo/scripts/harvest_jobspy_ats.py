#!/usr/bin/env python3
"""Harvest direct Greenhouse/Lever IT job leads from Indeed via JobSpy.

This replaces the error-prone Google Search scraping method by finding jobs on Indeed,
and extracting the direct Greenhouse/Lever application links from them.

Usage:
  .venv/bin/python scripts/harvest_jobspy_ats.py
  .venv/bin/python scripts/harvest_jobspy_ats.py --max-per 30 --freshness-days 7
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

from core.supervisor_runtime import merge_dotenv_into_env
merge_dotenv_into_env(os.environ, ROOT / ".env", override=False)

from config.it.hero_terms import HERO_SEARCH_TERMS
_DEFAULT_TERMS = HERO_SEARCH_TERMS

def is_greenhouse_or_lever_or_short_url(url: str | None, is_greenhouse_or_lever_url) -> bool:
    if not url:
        return False
    if is_greenhouse_or_lever_url(url):
        return True
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host in {"grnh.se", "gh.io"}
    except Exception:
        return False

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms", nargs="+", default=None, help="Search terms to query")
    parser.add_argument("--location", default="Vancouver, BC", help="Search location (centre)")
    parser.add_argument("--radius-km", type=int, default=40, help="Search radius in KM")
    parser.add_argument("--max-per", type=int, default=100, help="Max results wanted per search term")
    parser.add_argument("--freshness-days", type=int, default=30, help="Age of jobs in days (0 for all)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose log output")
    parser.add_argument(
        "--include-mongo-dedupe",
        action="store_true",
        help="Also skip GH/Lever URLs marked applied in Mongo application_queue",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep already-applied URLs in the harvested list",
    )
    parser.add_argument(
        "--no-email-dedupe",
        action="store_true",
        help="Do not skip leads matching IMAP email_applied_history",
    )
    parser.add_argument(
        "--refresh-imap",
        action="store_true",
        help="Refresh email_applied_history from IMAP before dedupe",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from jobspy import scrape_jobs
    from core.discovery.providers.google_cdp_provider import canonicalize_ats_url
    from core.shared_modules.ats_apply import is_greenhouse_or_lever_url
    from core.discovery.scrape_proxy import build_scrape_proxy_ladder

    terms = args.terms or _DEFAULT_TERMS
    print(f"Starting JobSpy ATS lead harvest for {len(terms)} term(s) in {args.location}...")

    ladder = build_scrape_proxy_ladder()
    raw_jobs_count = 0
    apply_urls: list[str] = []
    seen: set[str] = set()
    targets: list[dict] = []

    for term in terms:
        print(f"Scraping term: '{term}'...")
        proxies = ladder.current_proxies()
        proxy = proxies[0] if proxies else None
        
        scrape_kwargs = {
            "site_name": ["indeed"],
            "search_term": term,
            "location": args.location,
            "distance": args.radius_km,
            "results_wanted": args.max_per,
            "country_indeed": "canada",
            "proxies": [proxy] if proxy else None,
            "full_description": False,
        }
        if args.freshness_days > 0:
            scrape_kwargs["hours_old"] = args.freshness_days * 24

        try:
            df = scrape_jobs(**scrape_kwargs)
            ladder.note_success()
            term_raw_count = len(df)
            raw_jobs_count += term_raw_count
            print(f"  → Scraped {term_raw_count} raw Indeed jobs.")
            
            term_leads_count = 0
            for _, row in df.iterrows():
                title = str(row.get("title") or "").strip()
                company = str(row.get("company") or "").strip()
                location = str(row.get("location") or "").strip()
                direct_url = str(row.get("job_url_direct") or "").strip()
                listing_url = str(row.get("job_url") or "").strip()
                
                url = direct_url or listing_url
                if not url:
                    continue
                    
                clean_url = canonicalize_ats_url(url)
                if not clean_url:
                    continue

                # Flywheel: capture the board slug for direct API polling.
                try:
                    from core.discovery.slug_registry import register_slugs_from_url

                    register_slugs_from_url(clean_url, source="jobspy")
                except Exception:
                    pass
                    
                if direct_url:
                    print(f"    - {title} @ {company}: {clean_url}")
                    
                if is_greenhouse_or_lever_or_short_url(clean_url, is_greenhouse_or_lever_url):
                    if clean_url in seen:
                        continue
                    seen.add(clean_url)
                    apply_urls.append(clean_url)
                    targets.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "apply_url": clean_url,
                        "search_term": term,
                        "mode": "jobspy_direct"
                    })
                    term_leads_count += 1
            print(f"  → Extracted {term_leads_count} unique Greenhouse/Lever leads from this term.")
        except Exception as exc:
            print(f"  [Error] Failed to scrape '{term}': {exc}")
            ladder.note_failure(exc)

    # Dedupe against prior successful ATS applies (local artifacts; Mongo optional).
    skipped: list[dict] = []
    applied: set[str] = set()
    if not args.no_dedupe:
        from core.shared_modules.ats_lead_dedupe import filter_fresh_jobs, load_applied_ats_urls

        applied = load_applied_ats_urls(
            artifacts_dir=ROOT / "artifacts" / "wave-google-ats",
            include_mongo=bool(args.include_mongo_dedupe),
        )
        fresh_targets, skipped, _ = filter_fresh_jobs(
            targets,
            applied_urls=applied,
            include_mongo=False,
            include_email=not args.no_email_dedupe,
            refresh_imap=bool(args.refresh_imap),
        )
        print(
            f"Dedupe vs prior applied+IMAP: index={len(applied)} "
            f"raw_unique={len(targets)} fresh={len(fresh_targets)} skipped={len(skipped)}",
            flush=True,
        )
        for s in skipped[:15]:
            print(
                f"  skip {s.get('dedupe_skip')}: {(s.get('title') or '')[:50]} @ {(s.get('company') or '')[:30]} "
                f"{(s.get('canonical_url') or '')[:90]}",
                flush=True,
            )
        targets = fresh_targets
        apply_urls = [t["apply_url"] for t in targets]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT_DIR = ROOT / "artifacts" / "wave-google-ats"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "ts": stamp,
        "provider": "jobspy_indeed_direct",
        "location": args.location,
        "terms": terms,
        "counts": {
            "raw_jobs": raw_jobs_count,
            "unique_apply_urls_before_dedupe": len(seen),
            "unique_apply_urls": len(apply_urls),
            "skipped_already": len(skipped),
            "applied_index_size": len(applied),
        },
        "apply_urls": apply_urls,
        "targets": targets,
        "skipped_already": [
            {
                "title": s.get("title"),
                "company": s.get("company"),
                "apply_url": s.get("apply_url"),
                "canonical_url": s.get("canonical_url"),
                "dedupe_skip": s.get("dedupe_skip"),
            }
            for s in skipped
        ],
    }

    # Write report and apply_urls files
    out_json = OUT_DIR / f"cdp_dry_run_{stamp}.json"
    out_txt = OUT_DIR / f"apply_urls_{stamp}.txt"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_txt.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")

    # Generate files for the applier (metro_van_it_latest.json)
    latest_jobs = []
    for t in targets:
        latest_jobs.append({
            "title": t["title"],
            "company": t["company"],
            "location": t["location"],
            "apply_url": t["apply_url"]
        })

    latest_report = {
        "stamp": stamp,
        "cleared_prior_polluted_list": True,
        "method": "jobspy_indeed_direct",
        "filters": "IT/QA/support/sysadmin only; Metro Van BC from search; deduped vs prior ATS applies",
        "kept": len(latest_jobs),
        "skipped_already": len(skipped),
        "urls_file": f"artifacts/wave-google-ats/metro_van_it_urls_{stamp}.txt",
        "jobs": latest_jobs,
    }

    latest_json = OUT_DIR / "metro_van_it_latest.json"
    latest_json_stamped = OUT_DIR / f"metro_van_it_{stamp}.json"
    latest_json.write_text(json.dumps(latest_report, indent=2), encoding="utf-8")
    latest_json_stamped.write_text(json.dumps(latest_report, indent=2), encoding="utf-8")

    latest_urls = OUT_DIR / "metro_van_it_urls_latest.txt"
    latest_urls_stamped = OUT_DIR / f"metro_van_it_urls_{stamp}.txt"
    latest_urls.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")
    latest_urls_stamped.write_text("\n".join(apply_urls) + ("\n" if apply_urls else ""), encoding="utf-8")

    print("\n--- Harvest Summary ---")
    print(json.dumps(report["counts"], indent=2))
    print(f"wrote {out_json}")
    print(f"wrote {out_txt}")
    print(f"wrote {latest_json} and {latest_json_stamped}")
    print(f"wrote {latest_urls} and {latest_urls_stamped}")

    return 0 if apply_urls else 2

if __name__ == "__main__":
    raise SystemExit(main())
