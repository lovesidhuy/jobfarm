#!/usr/bin/env python3
"""Export failed / dead / retry queue jobs for offline improvement analysis.

Usage (on worker or with MONGODB_URI pointed at the factory DB):

  python scripts/export_queue_failures.py
  python scripts/export_queue_failures.py --hours 48 --out /tmp/failures.jsonl

Writes one JSON object per line with portal, title, company, url, status,
last_error, attempts, and timestamps. Safe to run before die/destroy so the
learning set survives ephemeral workers (copy file off-box or to S3).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=168, help="Lookback window (default 7d)")
    ap.add_argument(
        "--out",
        default="",
        help="Output JSONL path (default automation_monorepo/artifacts/queue_failures.jsonl)",
    )
    ap.add_argument(
        "--statuses",
        default="dead,retry,manual_review,skipped,already_applied",
        help="Comma-separated statuses to export (includes non-win terminals)",
    )
    args = ap.parse_args()

    try:
        from pymongo import MongoClient
    except ImportError:
        print("pymongo required", file=sys.stderr)
        return 2

    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("JOBBOTS_MONGO_DATABASE", "jobbots")
    q = MongoClient(uri)[db_name].application_queue

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else root / "artifacts" / "queue_failures.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    since = datetime.now(timezone.utc) - timedelta(hours=max(1, args.hours))
    statuses = [s.strip().lower() for s in args.statuses.split(",") if s.strip()]
    query = {"status": {"$in": statuses}, "updated_at": {"$gte": since}}

    fields = {
        "portal": 1,
        "profile": 1,
        "title": 1,
        "company": 1,
        "location": 1,
        "url": 1,
        "status": 1,
        "last_error": 1,
        "attempts": 1,
        "max_attempts": 1,
        "priority": 1,
        "gate_score": 1,
        "gate_reason": 1,
        "source_job_id": 1,
        "discovered_at": 1,
        "updated_at": 1,
        "applied_at": 1,
        "result_url": 1,
        "metadata": 1,
        "retry_reason": 1,
    }

    n = 0
    by_portal: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as fh:
        for doc in q.find(query, fields).sort("updated_at", -1):
            doc["_id"] = str(doc.get("_id", ""))
            for k, v in list(doc.items()):
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            portal = (doc.get("portal") or "?")
            reason = (doc.get("last_error") or "unknown")[:120]
            by_portal[portal] = by_portal.get(portal, 0) + 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
            fh.write(json.dumps(doc, default=str) + "\n")
            n += 1

    summary = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "hours": args.hours,
        "statuses": statuses,
        "count": n,
        "by_portal": by_portal,
        "top_reasons": sorted(by_reason.items(), key=lambda x: -x[1])[:25],
        "path": str(out),
    }
    summary_path = out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {n} rows → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
