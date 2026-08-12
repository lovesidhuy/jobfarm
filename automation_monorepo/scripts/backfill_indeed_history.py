#!/usr/bin/env python3
"""Import legacy Indeed applied exports and close exact duplicate queue rows.

The old Indeed workers wrote ``enriched_applied_jobs.json`` outside the
monorepo.  The current discovery gate only knows the canonical Mongo history,
so a rebuilt VM could rediscover listings that were already submitted.  This
tool is intentionally conservative: it only closes queue records when their
normalised Indeed job id is present in an applied export.  It never uses a
title/company similarity match to change queue state.

Run without ``--apply`` first.  ``--apply`` writes the history and changes
only ``queued`` / ``retry`` rows; every changed document is backed up first.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.indeed_sync import (  # noqa: E402
    _ctl_key,
    _soft_location,
    normalize_indeed_job_id,
)


def _default_sources() -> list[Path]:
    configured = os.getenv("INDEED_HISTORICAL_EXPORTS", "")
    if configured:
        return [Path(value).expanduser() for value in configured.split(os.pathsep) if value]
    # These paths are only defaults for the legacy VM layout.  New deployments
    # should pass --source or INDEED_HISTORICAL_EXPORTS explicitly.
    root = Path("/opt/jobbots/app/master")
    return sorted(root.glob("*indeed*/Auto_indeed/exports/enriched_applied_jobs.*"))


def _records(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        if isinstance(payload, list):
            yield from (row for row in payload if isinstance(row, dict))
        elif isinstance(payload, dict):
            rows = payload.get("records") or payload.get("jobs") or []
            yield from (row for row in rows if isinstance(row, dict))
        return
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="", errors="ignore") as handle:
            yield from csv.DictReader(handle)
        return
    raise ValueError(f"unsupported export format: {path}")


def _job_id(row: dict[str, Any]) -> str:
    return normalize_indeed_job_id(str(
        row.get("job_id") or row.get("Job ID") or row.get("id") or ""
    ))


def _canonical_record(row: dict[str, Any], job_id: str) -> dict[str, str]:
    return {
        "Job ID": job_id,
        "Title": str(row.get("title") or row.get("Title") or ""),
        "Company": str(row.get("company") or row.get("Company") or ""),
        "Work Location": str(row.get("work_location") or row.get("Work Location") or ""),
        "Date Applied": str(row.get("date_applied") or row.get("Date Applied") or ""),
        "Job Link": str(row.get("job_link") or row.get("Job Link") or ""),
    }


def load_applied_records(paths: Iterable[Path]) -> tuple[dict[str, dict[str, str]], list[str]]:
    records: dict[str, dict[str, str]] = {}
    notices: list[str] = []
    for path in paths:
        if not path.exists():
            notices.append(f"missing: {path}")
            continue
        try:
            count = 0
            for row in _records(path):
                job_id = _job_id(row)
                if job_id:
                    records[job_id] = _canonical_record(row, job_id)
                    count += 1
            notices.append(f"loaded {count} rows: {path}")
        except ValueError as exc:
            notices.append(str(exc))
    return records, notices


def _backup_and_close(records: dict[str, dict[str, str]], export_dir: Path, *, apply: bool) -> dict[str, Any]:
    from core.job_queue import JobQueue

    queue = JobQueue()
    rows = list(queue.jobs.find({
        "portal": "indeed",
        "status": {"$in": ["queued", "retry"]},
    }))
    ids = set(records)
    history_ctl = {
        _ctl_key(record["Company"], record["Title"], record["Work Location"])
        for record in records.values()
        if _soft_location(record["Work Location"])
    }
    matches: list[dict[str, Any]] = []
    for row in rows:
        job_id = normalize_indeed_job_id(str(row.get("source_job_id") or ""))
        ctl = _ctl_key(str(row.get("company") or ""), str(row.get("title") or ""), str(row.get("location") or ""))
        same_city_repost = bool(_soft_location(str(row.get("location") or "")) and ctl in history_ctl)
        if job_id in ids or same_city_repost:
            row["_history_match"] = "exact_job_id" if job_id in ids else "exact_company_title_city"
            matches.append(row)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = None
    if apply and matches:
        export_dir.mkdir(parents=True, exist_ok=True)
        backup_path = export_dir / f"indeed_exact_history_backup_{stamp}.json"
        backup_path.write_text(json.dumps({
            "created_at": stamp,
            "reason": "exact historical Indeed applied record",
            "documents": matches,
        }, indent=2, default=str), encoding="utf-8")
        now = datetime.now(timezone.utc)
        matched_ids = [str(row["_id"]) for row in matches]
        result = queue.jobs.update_many(
            {"_id": {"$in": matched_ids}, "status": {"$in": ["queued", "retry"]}},
            {"$set": {
                "status": "already_applied",
                "updated_at": now,
                "terminal_at": now,
                "already_applied_at": now,
                "terminal_day": now.strftime("%Y-%m-%d"),
                "last_error": "exact historical Indeed applied record",
                "outcome_reason": "exact historical Indeed applied record",
                "metadata.outcome": "already_applied",
                "lease_owner": None,
                "lease_expires_at": None,
            }},
        )
        for row in matches:
            queue._event(str(row["_id"]), "already_applied", "history_backfill", {
                "reason": "exact historical Indeed applied record",
            })
        changed = result.modified_count
    else:
        changed = 0
    return {
        "active_indeed_rows_checked": len(rows),
        "exact_history_matches": len(matches),
        "changed": changed,
        "backup_path": str(backup_path) if backup_path else None,
        "sample_matches": [{
            "id": str(row.get("_id")), "source_job_id": row.get("source_job_id"),
            "title": row.get("title"), "company": row.get("company"),
            "history_match": row.get("_history_match"),
        } for row in matches[:10]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, help="JSON or CSV applied-history export (repeatable)")
    parser.add_argument("--apply", action="store_true", help="write history and close exact duplicate queue rows")
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/lib/jobbots/queue_backups"))
    args = parser.parse_args()
    sources = args.source or _default_sources()
    records, notices = load_applied_records(sources)
    result: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "sources": [str(path) for path in sources],
        "unique_exact_indeed_ids": len(records),
        "notices": notices,
    }
    if args.apply:
        from core.portals.mongo_storage_legacy import save_job_record
        for record in records.values():
            save_job_record("indeed", "applied", record)
        result["history_rows_upserted"] = len(records)
    result["queue_scrub"] = _backup_and_close(records, args.backup_dir, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
