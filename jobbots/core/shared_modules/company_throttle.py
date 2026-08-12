"""Company Application Throttle & Exact Lead Deduplication.

Enforces:
1. Exact Job/Title Deduplication: Never re-apply to the exact same (company, title) or URL if already applied/already_applied in MongoDB or IMAP receipts.
2. Company Rate Limiting / Throttle: Limit total applications to any single company (default max 1 per 14 days) to prevent spamming recruiters.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from jobbots.core.shared_modules.ats_lead_dedupe import (
    canonicalize_ats_url,
    companies_soft_match,
    load_email_applied_index,
    soft_company,
    soft_title,
)

_UNKNOWN_COMPANIES = frozenset({"", "unknown", "n/a", "none", "null", "do not reply", "noreply"})


def is_unknown_company(company: str) -> bool:
    sc = soft_company(company)
    return not sc or sc in _UNKNOWN_COMPANIES or len(sc) < 2


def check_company_throttle_and_dedupe(q: Any, job: dict[str, Any]) -> tuple[str | None, str]:
    """Check if a job or company should be blocked/skipped before dispatching.

    Returns:
        (action, reason) if deduplicated or throttled:
            action: 'already_applied' or 'skipped'
            reason: explanatory string
        (None, '') if clear to proceed.
    """
    job_id = str(job.get("id") or job.get("_id") or "")
    company = str(job.get("company") or "").strip()
    title = str(job.get("title") or "").strip()
    raw_url = str(job.get("url") or "").strip()
    portal = str(job.get("portal") or "").strip().lower()
    canon_url = canonicalize_ats_url(raw_url)

    sc = soft_company(company)
    st = soft_title(title)

    # -------------------------------------------------------------------------
    # 1. Exact Job / Title Deduplication
    # -------------------------------------------------------------------------
    # A) Check Mongo 'jobs' collection for existing applied/already_applied status
    if hasattr(q, "jobs"):
        query_or = []
        if canon_url:
            query_or.append({"url": raw_url})
            query_or.append({"url": canon_url})
            query_or.append({"result_url": raw_url})
            query_or.append({"result_url": canon_url})
        if sc and st and not is_unknown_company(company):
            # Regex match soft company and soft title
            query_or.append({
                "company": {"$regex": f"^{re.escape(company)}$", "$options": "i"},
                "title": {"$regex": f"^{re.escape(title)}$", "$options": "i"},
            })

        if query_or:
            query = {
                "_id": {"$ne": job_id},
                "status": {"$in": ["applied", "already_applied"]},
                "$or": query_or,
            }
            existing = q.jobs.find_one(query, {"_id": 1, "company": 1, "title": 1, "status": 1})
            if existing:
                return "already_applied", f"dedupe: exact job/title already applied in queue (matched doc {existing['_id']})"

    # B) Check IMAP confirmation history
    try:
        email_idx = load_email_applied_index(include_mongo=True)
        match_reason = email_idx.match_reason(company, title)
        if match_reason:
            if match_reason == "email_company_ats":
                return "skipped", f"company_rate_limit: matched IMAP confirmation history for company ({match_reason})"
            return "already_applied", f"dedupe: matched IMAP confirmation history ({match_reason})"
    except Exception:
        pass

    # -------------------------------------------------------------------------
    # 2. Company Rate Limiting / Application Throttle
    # -------------------------------------------------------------------------
    # LinkedIn application volume is deliberately managed by its own daily
    # target and platform safety controls. A cross-portal 14-day company cap
    # prematurely drains that queue when a recruiter has several relevant
    # LinkedIn openings. Exact job/title and receipt dedupe above remain on.
    linkedin_throttle = os.environ.get("LINKEDIN_COMPANY_THROTTLE", "0")
    if portal == "linkedin" and linkedin_throttle.strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return None, ""

    # Skip company throttle if company name is unknown/missing
    if is_unknown_company(company):
        return None, ""

    max_per_company = int(os.environ.get("MAX_APPLICATIONS_PER_COMPANY", "1"))
    cooldown_days = int(os.environ.get("COMPANY_COOLDOWN_DAYS", "14"))

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    cutoff_ts = time.time() - (cooldown_days * 86400)
    cutoff_day = cutoff_dt.strftime("%Y-%m-%d")

    applied_company_count = 0

    if hasattr(q, "jobs"):
        # Count all applied jobs for this soft company within cooldown window
        # We fetch applied jobs and run soft company match in Python to handle company name variants
        cursor = q.jobs.find(
            {
                "_id": {"$ne": job_id},
                "status": "applied",
                "$or": [
                    {"applied_at": {"$gte": cutoff_dt}},
                    {"applied_day": {"$gte": cutoff_day}},
                    {"updated_at": {"$gte": cutoff_dt}},
                    {"terminal_at": {"$gte": cutoff_dt}},
                ],
            },
            {"_id": 1, "company": 1, "title": 1, "applied_at": 1},
        )
        for doc in cursor:
            other_company = str(doc.get("company") or "").strip()
            if companies_soft_match(sc, soft_company(other_company)):
                applied_company_count += 1
                if applied_company_count >= max_per_company:
                    return (
                        "skipped",
                        f"company_rate_limit: max applications ({max_per_company}) per company reached for '{company}' within {cooldown_days} days (prior app: {doc.get('title')})",
                    )

    # Also check email_applied_history for company matches within cooldown window
    try:
        if hasattr(q, "db") and q.db is not None:
            email_docs = q.db["email_applied_history"].find(
                {},
                {"company_name": 1, "job_title": 1, "date": 1, "received_at": 1},
            ).limit(2000)
            for doc in email_docs:
                other_company = str(doc.get("company_name") or "").strip()
                if companies_soft_match(sc, soft_company(other_company)):
                    applied_company_count += 1
                    if applied_company_count >= max_per_company:
                        return (
                            "skipped",
                            f"company_rate_limit: max applications ({max_per_company}) per company reached for '{company}' via email receipt within {cooldown_days} days",
                        )
    except Exception:
        pass

    return None, ""
