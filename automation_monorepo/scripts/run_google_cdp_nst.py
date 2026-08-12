#!/usr/bin/env python3
"""Run Google CDP Greenhouse/Lever discovery via Nstbrowser (avoiding Google block).

Uses the local warm Nstbrowser profile for 'linkedin_discovery' aligned with
your proxy and CapMonster settings, running Google searches stealthily.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_log = logging.getLogger("run_google_cdp_nst")

_DEFAULT_TERMS = [
    # Prefer hero terms from config/it/hero_terms when available.
]


def _default_it_terms() -> list[str]:
    """Hero IT search titles — same list discovery planner uses for IT."""
    try:
        from config.it.hero_terms import HERO_SEARCH_TERMS

        terms = [t for t in HERO_SEARCH_TERMS if t and str(t).strip()]
        if terms:
            return terms
    except Exception:
        pass
    return [
        "QA Analyst",
        "QA Engineer",
        "SDET",
        "Software Test Engineer",
        "IT Support",
        "IT Support Analyst",
        "Help Desk Analyst",
        "Help Desk Technician",
        "Service Desk Analyst",
        "Desktop Support",
        "Technical Support Analyst",
        "Systems Administrator",
        "IT Analyst",
        "IT Intern",
        "IT Co-op",
        "Junior Software Developer",
    ]


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms", nargs="+", default=None)
    parser.add_argument("--location", default="Vancouver, BC")
    parser.add_argument("--max-per", type=int, default=15)
    parser.add_argument(
        "--mode",
        choices=["web", "jobs", "both", "tavily"],
        default="web",
        help=(
            "web=Playwright site:greenhouse|lever (default); "
            "jobs=Google Jobs widget; both; "
            "tavily=Tavily API fail-safe (no browser/CAPTCHA)"
        ),
    )
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

    # 1. Load env and Infisical secrets
    from core.supervisor_runtime import merge_dotenv_into_env
    merge_dotenv_into_env(os.environ, ROOT / ".env", override=True)

    # Ensure IT IMAP secrets are loaded (optional but good practice)
    try:
        from core.secret_manager import get_secret
        for k in (
            "IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT",
            "IMAP_EMAIL", "IMAP_APP_PASSWORD",
        ):
            v = (get_secret(k) or "").strip()
            if v and not (os.environ.get(k) or "").strip():
                os.environ[k] = v
    except Exception:
        pass

    # Force Nstbrowser profile for discovery (not required for pure Tavily mode)
    profile_id = (os.environ.get("NSTBROWSER_PROFILE_ID_LINKEDIN_DISCOVERY") or "").strip()
    if args.mode != "tavily" and not profile_id:
        print(
            "[Error] NSTBROWSER_PROFILE_ID_LINKEDIN_DISCOVERY is not configured in .env or Secrets.",
            file=sys.stderr,
        )
        return 1

    os.environ["BOT_NAME"] = "linkedin_discovery"
    if profile_id:
        os.environ["NSTBROWSER_PROFILE_ID"] = profile_id
    os.environ["BROWSER_VENDOR"] = "nstbrowser"
    os.environ["KEEP_BROWSER"] = "0"  # Close browser at exit

    terms = args.terms or _default_it_terms()
    # Cap default hero list for Tavily cost/latency unless user passed terms.
    if not args.terms and args.mode == "tavily":
        # First 20 hero terms cover QA/support/sysadmin/entry software.
        terms = terms[:20]
    print(
        f"Starting Google ATS discovery ({args.mode}) for {len(terms)} term(s) "
        f"in {args.location}...",
        flush=True,
    )

    from core.discovery.providers.base import DiscoveryRequest
    from core.discovery.providers.google_cdp_provider import (
        GoogleCDPProvider,
        canonicalize_ats_url,
    )

    request = DiscoveryRequest(
        profile="it",
        search_terms=terms,
        locations=[args.location],
        max_results_per_term=args.max_per,
        freshness_days=7,
        radius_km=40,
        easy_apply_only=False,
    )

    apply_urls: list[str] = []
    seen: set[str] = set()
    targets: list[dict] = []
    raw_jobs = []
    blocked = False

    # Pure Tavily path — no Nstbrowser / CapMonster.
    if args.mode == "tavily":
        print("Mode=tavily → Tavily API only (CAPTCHA fail-safe, no browser).", flush=True)
        provider = GoogleCDPProvider(mode="tavily")
        raw_jobs = provider.discover(request)
    else:
        # 2. Launch Nstbrowser session
        from core.browser.open_chrome import createBrowserSession

        sb, page, context, browser, pw = createBrowserSession(bot_name="linkedin_discovery")

        # Enable CapMonster if needed (CapMonster key should be in env/secrets)
        os.environ.setdefault("USE_CAPMONSTER_CAPTCHA_SOLVER", "1")
        os.environ.setdefault("USE_CAPMONSTER", "1")

        provider = GoogleCDPProvider(mode=args.mode)

        try:
            # Run scraping directly on the Playwright page launched via Nstbrowser
            # This will use Nstbrowser's proxy, fingerprint, cookies, etc.
            for term in terms:
                term_request = DiscoveryRequest(
                    profile="it",
                    search_terms=[term],
                    locations=[args.location],
                    max_results_per_term=args.max_per,
                    freshness_days=7,
                    radius_km=40,
                    easy_apply_only=False,
                )
                print(f"Scraping term: {term}...", flush=True)
                try:
                    if args.mode == "web":
                        term_jobs, term_blocked = provider._scrape_web(page, term_request)
                        # Per-term Tavily fail-safe when this term's browser scrape is empty.
                        if not term_jobs and _truthy_env("GOOGLE_CDP_TAVILY_FALLBACK", True):
                            t_jobs = provider._tavily_failsafe(
                                term_request, reason=f"web_empty:{term}"
                            )
                            term_jobs = list(term_jobs) + list(t_jobs)
                    elif args.mode == "jobs":
                        term_jobs, term_blocked = provider._scrape_jobs(page, term_request)
                    else:
                        raw_jobs_web, blocked_web = provider._scrape_web(page, term_request)
                        if not raw_jobs_web and _truthy_env("GOOGLE_CDP_TAVILY_FALLBACK", True):
                            raw_jobs_web = list(raw_jobs_web) + list(
                                provider._tavily_failsafe(
                                    term_request, reason=f"web_empty:{term}"
                                )
                            )
                        raw_jobs_jobs, blocked_jobs = provider._scrape_jobs(page, term_request)
                        term_jobs = raw_jobs_web + raw_jobs_jobs
                        term_blocked = blocked_web or blocked_jobs

                    raw_jobs.extend(term_jobs)
                    if term_blocked:
                        blocked = True
                    print(f"  → Found {len(term_jobs)} raw jobs for '{term}'.", flush=True)
                except Exception as exc:
                    print(f"  [Error] Failed to scrape '{term}': {exc}", flush=True)
                    # Fail-safe: still try Tavily for this term
                    if args.mode in {"web", "both"} and _truthy_env("GOOGLE_CDP_TAVILY_FALLBACK", True):
                        try:
                            t_jobs = provider._tavily_failsafe(
                                term_request, reason=f"error:{term}"
                            )
                            raw_jobs.extend(t_jobs)
                            print(f"  → Tavily fail-safe added {len(t_jobs)} for '{term}'.", flush=True)
                        except Exception as t_exc:
                            print(f"  [Error] Tavily fail-safe failed: {t_exc}", flush=True)

            if blocked:
                print("[Warning] Scraper encountered blocks on some queries.", flush=True)
        finally:
            # Close session
            try:
                browser.close()
            except Exception:
                pass

    # Dedup and parse raw jobs
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
            "mode": (job.raw_extras or {}).get("google_mode") or args.mode,
            "discovered_by": (job.raw_extras or {}).get("discovered_by") or (
                "tavily_ats" if args.mode == "tavily" else "google_cdp"
            ),
        })

    OUT_DIR = ROOT / "artifacts" / "wave-google-ats"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

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
        # If only IT filter requested without dedupe, still URL-dedupe lightly via finalize.
        apply_urls = [t["apply_url"] for t in targets if t.get("apply_url")]
        print(
            f"Post-filter: fresh={stats.get('fresh')} skipped={stats.get('skipped')} "
            f"title_reject={stats.get('skipped_title')} applied_index={stats.get('applied_index')}",
            flush=True,
        )
        for s in skipped[:20]:
            print(
                f"  skip {s.get('dedupe_skip') or s.get('filter_skip')}: "
                f"{(s.get('title') or '')[:50]} @ {(s.get('company') or '')[:30]} "
                f"{(s.get('canonical_url') or s.get('apply_url') or '')[:90]}",
                flush=True,
            )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "ts": stamp,
        "provider": "google_cdp_nstbrowser",
        "mode": args.mode,
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
                "canonical_url": s.get("canonical_url"),
                "reason": s.get("dedupe_skip") or s.get("filter_skip"),
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
            "title": t.get("title") or "",
            "company": t.get("company") or "",
            "location": t.get("location") or "",
            "apply_url": t.get("apply_url") or "",
            "search_term": t.get("search_term"),
            "source": "google_cdp",
        })

    latest_report = {
        "stamp": stamp,
        "cleared_prior_polluted_list": True,
        "method": "google_cdp_provider via Nstbrowser",
        "filters": (
            "Hero IT titles; Google/Tavily site:greenhouse|lever; "
            "Metro Van geo policy + hard_screen_job IT gates; URL+IMAP dedupe"
        ),
        "kept": len(latest_jobs),
        "skipped": len(skipped),
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

    print(json.dumps(report["counts"], indent=2))
    print(f"wrote {out_json}")
    print(f"wrote {out_txt}")
    print(f"wrote {latest_json} and {latest_json_stamped}")
    print(f"wrote {latest_urls} and {latest_urls_stamped}")
    if latest_jobs:
        print("\n--- Fresh IT GH/Lever leads ---")
        for j in latest_jobs:
            print(f"  {j.get('title')} @ {j.get('company')} | {j.get('apply_url')}")

    return 0 if apply_urls else 2


if __name__ == "__main__":
    raise SystemExit(main())
