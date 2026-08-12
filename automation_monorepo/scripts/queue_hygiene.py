#!/usr/bin/env python3
"""Queue hygiene for pre-policy / stranded application_queue records.

Wave A: **report / dry-run only by default**. Never deletes. Migration requires
``--apply`` *and* an explicit ``--confirm-migrate`` after a backup export.

Categorizes existing records against the current Phase I-B location policy:

  * ``queued`` jobs (any method)
  * ``status=unverified`` stranded records (non-leasable status)
  * outside-Metro company-site (should be rejected under current policy)
  * would_retain / would_reject / would_bookmark / would_migrate_to_verify

Usage:
  python scripts/queue_hygiene.py report
  python scripts/queue_hygiene.py report --export-dir /tmp/queue-hygiene-export
  python scripts/queue_hygiene.py backup --export-dir /tmp/queue-hygiene-export
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.classification.location_policy import (  # noqa: E402
    REGION_METRO_VAN,
    REGION_OTHER,
    classify_region,
    decide_job_policy,
)
from core.discovery.contracts import NormalizedJob  # noqa: E402
from core.job_queue import JobQueue  # noqa: E402


def _apply_type_from_method(method: str) -> str:
    m = (method or "").strip().lower()
    if m in ("easy_apply", "easy-apply"):
        return "EASY_APPLY"
    if m in ("company_site", "company-site", "external"):
        return "COMPANY_APPLY"
    return "UNKNOWN"


def _row_to_normalized(row: dict) -> NormalizedJob:
    meta = row.get("metadata") or {}
    method = meta.get("application_method") or ""
    # status=unverified with no method → treat as UNKNOWN apply type
    if (row.get("status") or "").lower() == "unverified" and not method:
        method = "unverified"
    return NormalizedJob(
        source_platform=row.get("portal") or "indeed",
        source_job_id=str(row.get("source_job_id") or row.get("_id") or ""),
        discovery_engine="hygiene",
        query_id="queue_hygiene",
        job_title=row.get("title") or "",
        company_name=row.get("company") or "",
        location=row.get("location") or "",
        description=row.get("description") or "",
        date_posted=None,
        listing_url=row.get("url") or "",
        destination_url=None,
        apply_type=_apply_type_from_method(method),
        is_remote_hint=bool(meta.get("is_remote") or meta.get("is_remote_hint")),
    )


def _classify_row(row: dict) -> dict:
    """Return hygiene classification for one queue document."""
    status = (row.get("status") or "").strip().lower()
    meta = row.get("metadata") or {}
    method = (meta.get("application_method") or "").strip().lower()
    region = classify_region(row.get("location") or "")
    job = _row_to_normalized(row)
    decision = decide_job_policy(job)

    category = "other"
    action = "retain"
    note = ""

    if status == "unverified":
        category = "stranded_status_unverified"
        if decision.action == "REJECT":
            action = "reject"
            note = f"stranded status=unverified; policy would REJECT ({decision.reason})"
        elif decision.action == "VERIFY":
            action = "migrate_to_verify"
            note = (
                "stranded status=unverified → migrate to status=queued + "
                "application_method=unverified (lease-and-verify)"
            )
        elif decision.action == "SAVE":
            action = "bookmark"
            note = "stranded status=unverified → migrate to queued + company_site (bookmark)"
        elif decision.action == "APPLY":
            action = "migrate_to_apply"
            note = "stranded status=unverified → migrate to queued + easy_apply"
        else:
            action = "manual_review"
            note = f"stranded status=unverified; unexpected policy {decision.action}"
    elif status in ("queued", "retry"):
        category = "queued"
        if decision.action == "REJECT":
            action = "reject"
            note = f"queued but current policy REJECT ({decision.reason})"
        elif decision.action == "SAVE":
            if method != "company_site":
                action = "bookmark"
                note = f"queued method={method or 'missing'} → set company_site (SAVE)"
            else:
                action = "retain"
                note = "queued company_site matches SAVE policy"
        elif decision.action == "VERIFY":
            if method != "unverified":
                action = "migrate_to_verify"
                note = f"queued method={method or 'missing'} → set unverified (VERIFY)"
            else:
                action = "retain"
                note = "queued+unverified matches VERIFY policy"
        elif decision.action == "APPLY":
            if method != "easy_apply":
                action = "migrate_to_apply"
                note = f"queued method={method or 'missing'} → set easy_apply (APPLY)"
            else:
                action = "retain"
                note = "queued easy_apply matches APPLY policy"
    elif status in ("applied", "bookmarked", "dead", "manual_review", "rejected", "leased"):
        category = f"terminal_or_active_{status}"
        action = "retain"
        note = f"leave {status} unchanged"
    else:
        category = f"status_{status or 'empty'}"
        action = "manual_review"
        note = f"unrecognized status={status!r}"

    # Explicit bucket for the report the user asked for.
    outside_metro_company = (
        region == REGION_OTHER
        and (method in ("company_site", "company-site", "external")
             or job.apply_type == "COMPANY_APPLY")
        and status in ("queued", "retry", "unverified")
    )

    return {
        "id": row.get("_id") or row.get("id"),
        "status": status,
        "portal": row.get("portal"),
        "profile": row.get("profile"),
        "title": row.get("title"),
        "company": row.get("company"),
        "location": row.get("location"),
        "stored_method": method or None,
        "stored_region": meta.get("region"),
        "classified_region": region,
        "policy_action": decision.action,
        "policy_method": decision.application_method,
        "policy_reason": decision.reason,
        "hygiene_category": category,
        "hygiene_action": action,
        "note": note,
        "outside_metro_company_site": outside_metro_company,
        "metro_van": region == REGION_METRO_VAN,
    }


def _iter_relevant(q: JobQueue):
    """All non-terminal-or-interesting rows for hygiene (queued/retry/unverified + sample terminals)."""
    return list(
        q.jobs.find(
            {"status": {"$in": ["queued", "retry", "unverified", "leased"]}},
            {
                "_id": 1,
                "status": 1,
                "portal": 1,
                "profile": 1,
                "title": 1,
                "company": 1,
                "location": 1,
                "url": 1,
                "description": 1,
                "source_job_id": 1,
                "metadata": 1,
                "attempts": 1,
            },
        )
    )


def _summarize(rows: list[dict]) -> dict:
    by_action: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    outside_cs = 0
    for r in rows:
        by_action[r["hygiene_action"]] = by_action.get(r["hygiene_action"], 0) + 1
        by_category[r["hygiene_category"]] = by_category.get(r["hygiene_category"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["outside_metro_company_site"]:
            outside_cs += 1
    return {
        "total_scanned": len(rows),
        "by_status": by_status,
        "by_hygiene_action": by_action,
        "by_hygiene_category": by_category,
        "outside_metro_company_site": outside_cs,
        "would_retain": by_action.get("retain", 0),
        "would_reject": by_action.get("reject", 0),
        "would_bookmark": by_action.get("bookmark", 0),
        "would_migrate_to_verify": by_action.get("migrate_to_verify", 0),
        "would_migrate_to_apply": by_action.get("migrate_to_apply", 0),
        "would_manual_review": by_action.get("manual_review", 0),
    }


def _export(rows: list[dict], export_dir: Path, *, label: str) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = export_dir / f"queue_hygiene_{label}_{stamp}.json"
    payload = {
        "exported_at": stamp,
        "mode": label,
        "summary": _summarize(rows),
        "records": rows,
        "ids": [r["id"] for r in rows],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    # Also write a plain ID list for easy restore/audit.
    ids_path = export_dir / f"queue_hygiene_{label}_{stamp}.ids.txt"
    ids_path.write_text("\n".join(str(i) for i in payload["ids"]) + "\n", encoding="utf-8")
    return path


def cmd_report(args) -> int:
    q = JobQueue()
    raw = _iter_relevant(q)
    classified = [_classify_row(r) for r in raw]
    summary = _summarize(classified)
    export_path = None
    if args.export_dir:
        export_path = _export(classified, Path(args.export_dir), label="report")
    out = {
        "mode": "report_dry_run",
        "database": q.database,
        "summary": summary,
        "samples": {
            "would_reject": [r for r in classified if r["hygiene_action"] == "reject"][:10],
            "would_retain": [r for r in classified if r["hygiene_action"] == "retain"][:10],
            "would_bookmark": [r for r in classified if r["hygiene_action"] == "bookmark"][:10],
            "would_migrate_to_verify": [
                r for r in classified if r["hygiene_action"] == "migrate_to_verify"
            ][:10],
            "outside_metro_company_site": [
                r for r in classified if r["outside_metro_company_site"]
            ][:10],
            "stranded_status_unverified": [
                r for r in classified if r["hygiene_category"] == "stranded_status_unverified"
            ][:10],
        },
        "export_path": str(export_path) if export_path else None,
        "note": (
            "DRY-RUN only. No records were modified. "
            "Run `backup` then review before any future --apply migration."
        ),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_backup(args) -> int:
    """Full export of affected IDs + documents before any future migration."""
    if not args.export_dir:
        print("backup requires --export-dir", file=sys.stderr)
        return 2
    q = JobQueue()
    raw = _iter_relevant(q)
    # Full documents for restore safety.
    ids = [r["_id"] for r in raw]
    full = list(q.jobs.find({"_id": {"$in": ids}}))
    for doc in full:
        doc["_id"] = str(doc["_id"])
        for k, v in list(doc.items()):
            if hasattr(v, "isoformat"):
                doc[k] = v.isoformat()
    classified = [_classify_row(r) for r in raw]
    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    full_path = export_dir / f"queue_hygiene_backup_full_{stamp}.json"
    full_path.write_text(
        json.dumps(
            {
                "exported_at": stamp,
                "database": q.database,
                "count": len(full),
                "documents": full,
                "classification_summary": _summarize(classified),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    class_path = _export(classified, export_dir, label="backup_classified")
    print(
        json.dumps(
            {
                "mode": "backup",
                "database": q.database,
                "full_documents": str(full_path),
                "classified_report": str(class_path),
                "count": len(full),
                "summary": _summarize(classified),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Queue hygiene (Wave A: report/dry-run)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("report", "backup"):
        p = sub.add_parser(name)
        p.add_argument(
            "--export-dir",
            default="",
            help="Directory for JSON export of IDs + classifications (required for backup)",
        )
    args = ap.parse_args()
    if args.command == "report":
        return cmd_report(args)
    if args.command == "backup":
        return cmd_backup(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
