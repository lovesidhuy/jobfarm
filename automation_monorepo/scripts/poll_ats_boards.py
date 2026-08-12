#!/usr/bin/env python3
"""Cron entry: poll GH/Lever boards via the slug registry (ats_board_api).

The flywheel's periodic driver. Runs the ``ats_board_api`` provider through
the standard Phase I discovery pipeline (normalize → dedupe → geo policy →
IT-fit gate → enqueue), so board leads land in the same application queue as
Indeed/Glassdoor leads with identical screening guarantees.

Flow
----
1. ``AtsBoardApiProvider`` reads active slugs from the registry (seeded by
   harvesters / footprint sensor / ``seed_slug_registry.py``).
2. Boards are polled concurrently (bounded semaphore); dead slugs (404/410
   xN) are deactivated in the registry automatically.
3. Jobs are geo-normalised (``"Vancouver - Hybrid"`` → Metro Van) and emitted
   as COMPANY_APPLY RawJobs.
4. ``run_discovery`` normalises, dedupes, policy-screens, AI-gates, enqueues.

Usage
-----
  # Full poll (enqueue):
  .venv/bin/python scripts/poll_ats_boards.py

  # Dry run — scrape + screen, no enqueue:
  .venv/bin/python scripts/poll_ats_boards.py --dry-run

  # Registry stats only:
  .venv/bin/python scripts/poll_ats_boards.py --stats-only

Suggested crontab (every 6h, 15 min after the Indeed discovery):
  15 */6 * * * cd /path/to/automation_monorepo && .venv/bin/python scripts/poll_ats_boards.py >> logs/ats_board_poll.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.supervisor_runtime import merge_dotenv_into_env  # noqa: E402

merge_dotenv_into_env(os.environ, ROOT / ".env", override=False)  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="it", choices=["it", "general"])
    ap.add_argument("--dry-run", action="store_true", help="Scrape + screen, no enqueue")
    ap.add_argument("--stats-only", action="store_true", help="Print registry stats and exit")
    ap.add_argument("--backend", default=None, choices=["auto", "mongo", "json"])
    ap.add_argument("--freshness-days", type=int, default=None,
                    help="Not used by the board API (boards are always live) — kept for parity")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from core.discovery.slug_registry import get_registry

    registry = get_registry(force_backend=args.backend)
    stats = registry.stats()
    print(f"slug registry: {json.dumps(stats)}")
    if args.stats_only:
        return 0

    if stats.get("total", 0) == 0:
        print(
            "registry is empty — seed it first:\n"
            "  .venv/bin/python scripts/seed_slug_registry.py <file> --platform greenhouse\n"
            "  or run harvesters (jobspy/google/tavily) to auto-capture slugs."
        )
        return 2

    from core.discovery.planner import run_discovery

    result = run_discovery(
        profile=args.profile,
        portals=["ats_board_api"],
        dry_run=args.dry_run,
    )
    print("\n--- ATS board poll result ---")
    print(json.dumps(result, indent=2, default=str))

    print(f"\nregistry after poll: {json.dumps(registry.stats())}")
    return 0 if result.get("raw_count", 0) else 2


if __name__ == "__main__":
    raise SystemExit(main())
