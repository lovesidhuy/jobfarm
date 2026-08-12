#!/usr/bin/env python3
"""
Quick stats CLI for the bot_events stream.

Examples
--------
    # Last 24h, all bots
    python scripts/event_stats.py

    # Specific bot, last 6 hours
    python scripts/event_stats.py --bot linkedin_it --hours 6

    # Group skipped reasons
    python scripts/event_stats.py --bot indeed_general --reasons
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ALL_BOTS = ["indeed_it", "indeed_general", "glassdoor_it",
            "linkedin_it", "linkedin_general"]


def _try_mongo():
    try:
        from pymongo import MongoClient
        uri = os.environ.get("MONGODB_URI") or "mongodb://localhost:27017"
        db_name = (os.environ.get("MONGODB_EVENTS_DB")
                   or os.environ.get("MONGODB_DB_NAME")
                   or "auto_job_applier_events")
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        return client[db_name]["bot_events"]
    except Exception:
        return None


def _read_jsonl(bot: str) -> list[dict]:
    path = ROOT / "logs" / bot / "events.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _filter(rows, since: datetime | None):
    if not since:
        return rows
    iso = since.isoformat()
    return [r for r in rows if r.get("ts", "") >= iso]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", help="Bot name; default = all")
    ap.add_argument("--hours", type=int, default=24,
                    help="Look-back window in hours (default 24, 0 = all-time)")
    ap.add_argument("--reasons", action="store_true",
                    help="Group skipped/failed by reason")
    ap.add_argument("--mongo-only", action="store_true",
                    help="Only query Mongo; skip JSONL fallback")
    args = ap.parse_args()

    bots = [args.bot] if args.bot else ALL_BOTS
    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)
             if args.hours and args.hours > 0 else None)

    coll = _try_mongo()
    print(f"\nEvent stats — bots: {', '.join(bots)} | "
          f"window: {('last ' + str(args.hours) + 'h') if since else 'all-time'} | "
          f"source: {'mongo+jsonl' if coll is not None and not args.mongo_only else ('mongo' if args.mongo_only else 'jsonl')}\n")

    grand: Counter = Counter()
    for bot in bots:
        events: list[dict] = []
        if coll is not None:
            try:
                match = {"bot_name": bot}
                if since:
                    match["ts"] = {"$gte": since}
                events.extend(coll.find(match, {
                    "_id": 0, "event": 1, "reason": 1, "company": 1, "title": 1,
                }))
            except Exception:
                pass
        if not args.mongo_only:
            events.extend(_filter(_read_jsonl(bot), since))

        if not events:
            print(f"  [{bot}]  (no events)")
            continue

        ev_counts = Counter(e.get("event", "") for e in events)
        line = "  ".join(f"{k}={ev_counts[k]}" for k in
                          ("applied", "saved", "skipped", "failed",
                           "filter_rejected", "manual_review")
                          if ev_counts.get(k))
        print(f"  [{bot}]  total={len(events)}   {line or '(no decisions)'}")
        grand.update(ev_counts)

        if args.reasons:
            for ev in ("skipped", "failed"):
                rc = Counter(e.get("reason", "") for e in events
                             if e.get("event") == ev and e.get("reason"))
                if rc:
                    print(f"     {ev} reasons:")
                    for reason, n in rc.most_common(10):
                        print(f"        {n:5d}  {reason}")

    if len(bots) > 1:
        print("\n  GRAND TOTAL:  " + "   ".join(
            f"{k}={grand[k]}" for k in
            ("applied", "saved", "skipped", "failed",
             "filter_rejected", "manual_review")
            if grand.get(k)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
