#!/usr/bin/env python3
"""Import a bounded Feashliaa board-slug batch into the ATS registry.

The import stores board identifiers only.  It does not enqueue jobs or run an
application.  Boards are later revalidated by the direct ATS API poller.

Examples
--------
  python scripts/import_feashliaa_seeds.py --dry-run
  python scripts/import_feashliaa_seeds.py --per-platform 250 --offset 0
  python scripts/import_feashliaa_seeds.py --per-platform 250 --offset 250
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.external_seeds import (  # noqa: E402
    FEASHLIAA_SOURCE,
    fetch_feashliaa_lists,
    seed_feashliaa_lists,
)
from core.discovery.slug_registry import get_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-platform", type=int, default=250,
                        help="maximum slugs to activate per ATS (default: 250)")
    parser.add_argument("--offset", type=int, default=0,
                        help="start at this position in every ATS list (default: 0)")
    parser.add_argument("--backend", choices=["auto", "mongo", "json"], default="auto")
    parser.add_argument("--dry-run", action="store_true", help="download and validate only")
    args = parser.parse_args()

    try:
        lists = fetch_feashliaa_lists()
    except Exception as exc:
        print(f"Feashliaa seed download failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        preview = {
            platform: {"available": len(slugs), "selected": len(slugs[args.offset:args.offset + args.per_platform])}
            for platform, slugs in lists.items()
        }
        print(json.dumps({"source": FEASHLIAA_SOURCE, "dry_run": True, "platforms": preview}, indent=2))
        return 0

    try:
        registry = get_registry(force_backend=args.backend)
        report = seed_feashliaa_lists(
            registry, lists, per_platform=args.per_platform, offset=args.offset,
        )
    except (ValueError, OSError) as exc:
        print(f"Feashliaa seed import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["totals"]["inserted"] or report["totals"]["updated"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
