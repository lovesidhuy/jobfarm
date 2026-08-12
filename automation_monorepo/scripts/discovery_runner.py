#!/usr/bin/env python3
"""Standalone discovery runner — test and run discovery independently.

Usage
-----
  # Dry-run: scrape, normalise, screen, but do NOT enqueue
  python scripts/discovery_runner.py --profile it --portals indeed,glassdoor --dry-run

  # Production: scrape + normalise + screen + enqueue
  python scripts/discovery_runner.py --profile it --portals indeed,glassdoor,linkedin,workopolis

  # Single portal test
  python scripts/discovery_runner.py --profile it --portals indeed --dry-run --max-results 10

  # LinkedIn uses config linkedin_search_terms (main IT titles only) in batches of
  # LINKEDIN_DISCOVERY_TERM_BATCH (default 5); other portals keep full search_terms.
  python scripts/discovery_runner.py --profile it --portals linkedin --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Ensure monorepo root is on sys.path
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# Load .env
from core.supervisor_runtime import merge_dotenv_into_env
merge_dotenv_into_env(os.environ, _ROOT / ".env", override=False)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Dual-Engine Discovery Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--profile", type=str, default="it",
        choices=["it", "general"],
        help="Job profile (default: it)",
    )
    ap.add_argument(
        "--portals", type=str, default=None,
        help="Comma-separated portals to discover (default: all). "
             "Options: indeed, glassdoor, linkedin, workopolis, google, greenhouse, "
             "lever, ashby, bamboohr, ats_crossmatch, ats_board_api",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Scrape + normalise + screen but do NOT enqueue to MongoDB.",
    )
    ap.add_argument(
        "--max-results", type=int, default=50,
        help="Max results per search term per provider (default: 50)",
    )
    ap.add_argument(
        "--freshness-days", type=int, default=7,
        help="Date freshness filter in days (default: 7). Use 0 for all dates.",
    )
    ap.add_argument(
        "--timeout", type=int, default=14400,
        help="Per-provider timeout in seconds (default: 14400)",
    )
    ap.add_argument(
        "--keyword", "--terms", type=str, default=None,
        help="Comma-separated list of search terms to override config (e.g. 'QA Analyst')",
    )
    ap.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    args = ap.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    portals = None
    if args.portals:
        portals = [p.strip().lower() for p in args.portals.split(",") if p.strip()]

    freshness = args.freshness_days if args.freshness_days > 0 else None

    search_terms = None
    if args.keyword:
        search_terms = [t.strip() for t in args.keyword.split(",") if t.strip()]

    from core.discovery import run_discovery

    print(f"\n{'='*60}")
    print(f"  Discovery Runner")
    print(f"  Profile:    {args.profile}")
    print(f"  Portals:    {portals or 'all'}")
    print(f"  Dry-run:    {args.dry_run}")
    print(f"  Max/term:   {args.max_results}")
    print(f"  Freshness:  {freshness or 'all dates'}d")
    print(f"  Timeout:    {args.timeout}s")
    if search_terms:
        print(f"  Keywords:   {search_terms}")
    print(f"{'='*60}\n")

    result = run_discovery(
        profile=args.profile,
        portals=portals,
        dry_run=args.dry_run,
        max_results_per_term=args.max_results,
        freshness_days=freshness,
        timeout_seconds=args.timeout,
        search_terms=search_terms,
    )

    print(f"\n{'='*60}")
    print("  Discovery Results")
    print(f"{'='*60}")
    print(json.dumps(result, indent=2, default=str))

    if result.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
