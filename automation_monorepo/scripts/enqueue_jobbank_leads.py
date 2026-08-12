#!/usr/bin/env python3
"""Enqueue Job Bank / scraper_leads into application_queue (portal=jobbank).

Optionally generates missing screening answers with AI (form_answers bank + DeepSeek)
before enqueue so email apply includes custom Job Bank questions.

Usage:
  python scripts/enqueue_jobbank_leads.py              # enqueue unsent leads
  python scripts/enqueue_jobbank_leads.py --dry-run
  python scripts/enqueue_jobbank_leads.py --generate-answers
  python scripts/enqueue_jobbank_leads.py --limit 5
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "scrapers"))

import core.secret_manager  # noqa: F401
from core.job_queue import JobQueue


def _source_job_id(url: str, email: str, lead_id) -> str:
    m = re.search(r"/jobposting/(\d+)", url or "")
    if m:
        return m.group(1)
    if lead_id is not None:
        return f"lead-{lead_id}"
    return re.sub(r"[^a-zA-Z0-9]+", "-", (email or "unknown").lower())[:80]


def _is_jobbank_url(url: str) -> bool:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        host = ""
    return "jobbank.gc.ca" in host or "jobbank" in (url or "").lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Max leads to enqueue (0=all)")
    ap.add_argument(
        "--generate-answers",
        action="store_true",
        help="If screening_answers missing, fetch Job Bank page + AI-answer questions",
    )
    ap.add_argument(
        "--include-sent",
        action="store_true",
        help="Also enqueue leads already marked Sent (usually skip)",
    )
    ap.add_argument("--profile", default="it", choices=["it", "general"])
    args = ap.parse_args()

    import lss_helper

    lss_helper.init_db()
    # Do not call import_from_markdown here — it can collide on lead ids after
    # restore and is unnecessary for queue enqueue from scraper_leads.
    db = lss_helper.get_mongo_db()
    rows = list(db.scraper_leads.find().sort("id", 1))
    print(f"[enqueue-jobbank] scraper_leads total={len(rows)}")

    q = JobQueue()
    enqueued = skipped = failed = 0
    for r in rows:
        if args.limit and enqueued >= args.limit:
            break
        status = (r.get("status") or "").strip().lower()
        if not args.include_sent and status in {"sent", "emailed"}:
            skipped += 1
            continue
        email = (r.get("email") or "").strip().lower()
        url = (r.get("url") or "").strip()
        role = (r.get("role") or r.get("title") or "").strip()
        company = (r.get("company") or "").strip()
        if not email or "@" not in email:
            skipped += 1
            continue
        # Prefer jobbank URLs; also allow auto-scraper with empty url only if source is jobbank-ish
        source = (r.get("source") or "").strip()
        if url and not _is_jobbank_url(url) and "eluta" in source.lower():
            # Eluta is a different email lane — skip here
            skipped += 1
            continue
        if not url and source.lower() not in {"auto-scraper", "jobbank"}:
            skipped += 1
            continue

        screening = (r.get("screening_answers") or "").strip()
        if args.generate_answers and not screening and url:
            from core.jobbank_email import ensure_screening_answers

            print(f"  generating screening answers for {role!r} @ {company!r}…")
            screening = ensure_screening_answers(
                existing="",
                url=url,
                title=role,
                company=company,
                force_refresh=True,
            )
            if screening and not args.dry_run:
                db.scraper_leads.update_one(
                    {"_id": r["_id"]},
                    {"$set": {"screening_answers": screening}},
                )

        sid = _source_job_id(url, email, r.get("id"))
        meta = {
            "application_method": "email",
            "to_email": email,
            "email": email,
            "scraper_source": source or "jobbank",
            "source": "jobbank",
            "subject": r.get("subject") or f"Application for {role}",
            "screening_answers": screening,
            "lead_id": r.get("id"),
            "location": r.get("location") or "",
        }
        if args.dry_run:
            print(
                f"  DRY-RUN enqueue jobbank {role!r} @ {company!r} → {email} "
                f"screening={bool(screening)} sid={sid}"
            )
            enqueued += 1
            continue
        try:
            jid, created = q.enqueue(
                portal="jobbank",
                profile=args.profile,
                source_job_id=sid,
                title=role or "Job Bank role",
                company=company or "Employer",
                url=url or f"mailto:{email}",
                location=r.get("location") or "",
                description=screening[:4000] if screening else "",
                gate_score=80,
                gate_reason="jobbank scraper lead (email apply)",
                resume_policy="default",
                priority=120,
                metadata=meta,
            )
            print(
                f"  {'NEW' if created else 'exists'} id={jid} {role!r} @ {company!r} → {email}"
            )
            if created:
                enqueued += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            print(f"  FAIL {role!r}: {exc}")

    print(
        f"[enqueue-jobbank] done enqueued={enqueued} skipped={skipped} failed={failed} "
        f"dry_run={args.dry_run}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
