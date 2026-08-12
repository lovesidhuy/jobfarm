#!/usr/bin/env python3
"""Wave A.2 — Report-only queue reclassification (no mutation).

Reclassifies existing application_queue records with corrected apply-type
logic (live Easy Apply pass matching + ATS URL evidence), geo policy, and
active/expired checks. Does NOT apply hygiene changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.classification.apply_type import classify_apply_type  # noqa: E402
from core.discovery.classification.location_policy import (  # noqa: E402
    decide_job_policy,
    detect_work_mode,
    classify_region,
)
from core.discovery.contracts import NormalizedJob  # noqa: E402
from core.discovery.normalizer import normalize_raw_job  # noqa: E402
from core.discovery.providers.base import DiscoveryRequest  # noqa: E402
from core.discovery.providers.jobspy_provider import JobSpyProvider  # noqa: E402
from core.job_queue import JobQueue  # noqa: E402

ARTIFACTS = ROOT / "artifacts" / "wave-a2-reclassify"
HYGIENE_BACKUP = ROOT / "artifacts" / "queue-hygiene"
_JK_RE = re.compile(r"[?&]jk=([a-f0-9]+)", re.I)
_EXPIRED_MARKERS = (
    "this job has expired",
    "job has expired",
    "no longer available",
    "page not found",
    "we can't find this page",
    "cannot find this job",
    "this job is closed",
)


def _extract_jk(url: str) -> str:
    if not url:
        return ""
    m = _JK_RE.search(url)
    if m:
        return m.group(1).lower()
    try:
        qs = parse_qs(urlparse(url).query)
        return (qs.get("jk") or [""])[0].lower()
    except Exception:
        return ""


def _check_active(url: str, *, live_matched: bool = False, timeout: float = 12.0) -> str:
    """Return active | expired | unknown.

    A live JobSpy match for the same ``jk`` is strong evidence the posting is
    still active (Indeed HTML fetches are often blocked → unknown).
    """
    if live_matched:
        return "active"
    if not url or "indeed." not in url.lower():
        return "unknown"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; WaveA2Hygiene/1.0)"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(120_000).decode("utf-8", errors="ignore").lower()
            final = (resp.geturl() or url).lower()
        if any(m in body for m in _EXPIRED_MARKERS):
            return "expired"
        if "indeedapply" in body or "apply now" in body or "jobsearch-jobinfoheader" in body:
            return "active"
        if "expired" in body[:2000]:
            return "expired"
        if "viewjob" in final or "jk=" in final:
            return "active"
        return "unknown"
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return "expired"
        return "unknown"
    except Exception:
        return "unknown"


def _merge_live_index(index: dict[str, dict], raw_jobs) -> None:
    for r in raw_jobs:
        jk = _extract_jk(r.listing_url) or (r.source_job_id or "").lower()
        if not jk:
            continue
        job = normalize_raw_job(
            r, discovery_engine="jobspy",
            search_term=(r.raw_extras or {}).get("search_term", ""),
            location=r.location,
            freshness_days=14,
        )
        sp = (r.raw_extras or {}).get("search_pass", "")
        rank = {"EASY_APPLY": 0, "COMPANY_APPLY": 1, "UNKNOWN": 2}.get(job.apply_type, 9)
        prev = index.get(jk)
        prev_rank = 9
        if prev:
            prev_rank = {"EASY_APPLY": 0, "COMPANY_APPLY": 1, "UNKNOWN": 2}.get(
                prev["apply_type"], 9
            )
        if prev is None or rank < prev_rank:
            index[jk] = {
                "apply_type": job.apply_type,
                "apply_type_source": job.apply_type_source,
                "apply_type_confirmed": bool(job.apply_type_confirmed),
                "destination_url": job.destination_url,
                "is_remote_hint": bool(job.is_remote_hint),
                "search_pass": sp,
                "title": job.job_title,
                "company": job.company_name,
                "location": job.location,
            }


def _build_live_apply_index(*, terms: list[str], locations: list[str]) -> dict[str, dict]:
    """Map Indeed jk → corrected apply classification from two-pass JobSpy."""
    provider = JobSpyProvider(portals=["indeed"])
    req = DiscoveryRequest(
        profile="it",
        search_terms=terms,
        locations=locations,
        max_results_per_term=20,
        freshness_days=14,
        radius_km=50,
        easy_apply_only=False,
    )
    raw = provider.discover(req)
    index: dict[str, dict] = {}
    _merge_live_index(index, raw)
    return index


def _enrich_index_for_unmatched(index: dict[str, dict], rows: list[dict]) -> dict[str, dict]:
    """Second-pass company/title searches for queue rows still unmatched."""
    missing = []
    for row in rows:
        jk = _extract_jk(row.get("url") or "")
        if jk and jk not in index:
            company = (row.get("company") or "").strip()
            title = (row.get("title") or "").split("(")[0].strip()
            if company and company.lower() not in ("nan", "n/a"):
                missing.append(company)
            elif title:
                missing.append(title)
    terms = sorted(set(missing))[:30]
    if not terms:
        return index
    print(f"[A.2] Enriching live index for {len(terms)} unmatched companies/titles…", flush=True)
    provider = JobSpyProvider(portals=["indeed"])
    req = DiscoveryRequest(
        profile="it",
        search_terms=terms,
        locations=["Vancouver, BC", "Toronto, ON", "Canada", ""],
        max_results_per_term=10,
        freshness_days=30,
        radius_km=50,
        easy_apply_only=False,
    )
    _merge_live_index(index, provider.discover(req))
    return index


def _corrected_from_row(row: dict, live: dict[str, dict] | None) -> tuple[NormalizedJob, dict]:
    """Build corrected NormalizedJob; never trust stored method alone."""
    url = row.get("url") or ""
    jk = _extract_jk(url)
    meta = row.get("metadata") or {}
    stored_method = (meta.get("application_method") or "").strip().lower()
    live_hit = (live or {}).get(jk) if jk else None

    apply_type = "UNKNOWN"
    apply_source = "not_verified"
    confirmed = False
    dest = None
    is_remote_hint = bool(meta.get("is_remote") or meta.get("is_remote_hint"))
    evidence_note = "no_live_match_unknown"

    if live_hit:
        apply_type = live_hit["apply_type"]
        apply_source = live_hit["apply_type_source"] or "live_match"
        confirmed = bool(live_hit["apply_type_confirmed"])
        dest = live_hit.get("destination_url")
        is_remote_hint = is_remote_hint or bool(live_hit.get("is_remote_hint"))
        evidence_note = f"live_match:{live_hit.get('search_pass')}:{apply_source}"
    else:
        # Corrected logic: do NOT promote stored company_site without evidence.
        # Do NOT promote stored easy_apply without provenance either.
        # Keep UNKNOWN unless destination_url in metadata is a known ATS.
        dest = meta.get("destination_url") or meta.get("job_url_direct")
        probe = NormalizedJob(
            source_platform=row.get("portal") or "indeed",
            source_job_id=str(row.get("source_job_id") or jk or row.get("_id") or ""),
            discovery_engine="hygiene_a2",
            query_id="wave_a2",
            job_title=row.get("title") or "",
            company_name=row.get("company") or "",
            location=row.get("location") or "",
            description=row.get("description") or "",
            date_posted=None,
            listing_url=url,
            destination_url=dest,
            apply_type="UNKNOWN",
            apply_type_source="",
            is_remote_hint=is_remote_hint,
        )
        clf = classify_apply_type(probe)
        apply_type = clf.apply_type
        apply_source = clf.source
        confirmed = clf.confirmed
        evidence_note = f"no_live_match:{clf.source};stored_method={stored_method or 'none'}"

    job = NormalizedJob(
        source_platform=row.get("portal") or "indeed",
        source_job_id=str(row.get("source_job_id") or jk or row.get("_id") or ""),
        discovery_engine="hygiene_a2",
        query_id="wave_a2",
        job_title=row.get("title") or "",
        company_name=row.get("company") or "",
        location=row.get("location") or "",
        description=row.get("description") or "",
        date_posted=None,
        listing_url=url,
        destination_url=dest,
        apply_type=apply_type,
        apply_type_source=apply_source,
        apply_type_confirmed=confirmed,
        is_remote_hint=is_remote_hint,
    )
    return job, {
        "jk": jk,
        "live_matched": bool(live_hit),
        "evidence_note": evidence_note,
        "stored_method": stored_method or None,
    }


def _propose(row: dict, decision, job_state: str) -> dict:
    """Map policy + active state → proposed status/method (no mutation)."""
    status = (row.get("status") or "").strip().lower()
    meta = row.get("metadata") or {}
    cur_method = (meta.get("application_method") or "").strip().lower() or None

    # Expired jobs must never be migrated into queued.
    if job_state == "expired":
        return {
            "proposed_status": "rejected",
            "proposed_method": cur_method,
            "proposed_action": "terminal_policy_reject_expired",
            "would_mutate": True,
            "migrate_to_queued": False,
            "note": "expired — do not migrate to queued; mark terminal with policy reason",
        }

    if decision.action == "REJECT":
        return {
            "proposed_status": "rejected",
            "proposed_method": None,
            "proposed_action": "terminal_policy_reject",
            "would_mutate": True,
            "migrate_to_queued": False,
            "note": f"invalid under corrected policy ({decision.reason}); terminal, not deleted",
        }

    if decision.action == "APPLY":
        active_ok = job_state == "active"
        return {
            "proposed_status": "queued",
            "proposed_method": "easy_apply",
            "proposed_action": "migrate_to_queued_easy_apply",
            "would_mutate": active_ok,
            "migrate_to_queued": active_ok,
            "note": (
                "active confirmed Easy Apply → queued/easy_apply"
                if active_ok
                else f"job_state={job_state}: do not migrate to queued until active"
            ),
        }

    if decision.action == "SAVE":
        active_ok = job_state == "active"
        return {
            "proposed_status": "queued",
            "proposed_method": "company_site",
            "proposed_action": "migrate_to_queued_company_site_bookmark",
            "would_mutate": active_ok,
            "migrate_to_queued": active_ok,
            "note": (
                "active Metro company-site → queued/company_site (bookmark)"
                if active_ok
                else f"job_state={job_state}: hold bookmark migration"
            ),
        }

    # VERIFY
    active_ok = job_state == "active"
    return {
        "proposed_status": "queued",
        "proposed_method": "unverified",
        "proposed_action": "migrate_to_queued_unverified_verify",
        "would_mutate": active_ok,
        "migrate_to_queued": active_ok,
        "note": (
            "active Metro unknown → queued/unverified (lease-and-verify)"
            if active_ok
            else f"job_state={job_state}: hold verify migration"
        ),
    }


def _verify_backup_checksums() -> dict:
    sums_path = HYGIENE_BACKUP / "SHA256SUMS.txt"
    if not sums_path.exists():
        return {"ok": False, "error": "SHA256SUMS.txt missing"}
    checked = []
    for line in sums_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        expect, name = parts[0], parts[-1]
        fpath = HYGIENE_BACKUP / name
        if not fpath.exists():
            checked.append({"file": name, "ok": False, "error": "missing"})
            continue
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
        checked.append({"file": name, "ok": digest == expect, "sha256": digest})
    return {"ok": all(c["ok"] for c in checked), "files": checked, "sums_path": str(sums_path)}


def main() -> int:
    os.environ.setdefault("DISCOVERY_GEO_POLICY", "1")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("[A.2] Verifying durable hygiene backup checksums…", flush=True)
    backup = _verify_backup_checksums()

    print("[A.2] Loading queue records…", flush=True)
    q = JobQueue()
    raw = list(
        q.jobs.find(
            {"status": {"$in": ["queued", "retry", "unverified", "leased"]}},
        )
    )
    print(f"[A.2] Loaded {len(raw)} records from {q.database}", flush=True)

    # Live Easy Apply / all-leads index for corrected apply-type
    terms = sorted({
        (r.get("title") or "").split("(")[0].strip()
        for r in raw
        if (r.get("title") or "").strip()
    })
    # Cap term explosion — prefer distinctive tokens + common IT terms
    terms = [t for t in terms if len(t) >= 4][:40]
    if len(terms) < 5:
        terms = ["QA Analyst", "IT Support", "Help Desk", "Network Analyst", "Software Engineer"]
    locations = ["Vancouver, BC", "Surrey, BC", "Burnaby, BC", "Toronto, ON", ""]
    print(f"[A.2] Building live apply-type index ({len(terms)} terms)…", flush=True)
    live_index = _build_live_apply_index(terms=terms[:25], locations=locations)
    print(f"[A.2] Live index size: {len(live_index)} jk keys", flush=True)
    live_index = _enrich_index_for_unmatched(live_index, raw)
    print(f"[A.2] Live index size after enrich: {len(live_index)} jk keys", flush=True)

    records = []
    for row in raw:
        job, meta_info = _corrected_from_row(row, live_index)
        decision = decide_job_policy(job)
        work_mode = detect_work_mode(
            job.location, job.description, is_remote_hint=bool(job.is_remote_hint)
        )
        region = classify_region(job.location)
        job_state = _check_active(
            row.get("url") or "", live_matched=bool(meta_info["live_matched"])
        )
        proposal = _propose(row, decision, job_state)
        if job_state == "expired":
            proposal["migrate_to_queued"] = False
            proposal["would_mutate"] = True

        cur_method = (row.get("metadata") or {}).get("application_method")
        records.append({
            "id": str(row.get("_id")),
            "title": row.get("title"),
            "company": row.get("company"),
            "url": row.get("url"),
            "current_status": row.get("status"),
            "current_method": cur_method,
            "current_location": row.get("location"),
            "current_work_mode": work_mode,
            "classified_region": region,
            "corrected_apply_type": job.apply_type,
            "corrected_apply_type_source": job.apply_type_source,
            "corrected_apply_type_confirmed": bool(job.apply_type_confirmed),
            "evidence_note": meta_info["evidence_note"],
            "live_matched": meta_info["live_matched"],
            "jk": meta_info["jk"],
            "destination_url": job.destination_url,
            "policy_action": decision.action,
            "policy_method": decision.application_method,
            "policy_reason": decision.reason,
            "job_state": job_state,
            **proposal,
        })

    # Summaries
    before_status = Counter(r["current_status"] for r in records)
    before_method = Counter((r["current_method"] or "none") for r in records)
    proposed_action = Counter(r["proposed_action"] for r in records)
    by_state = Counter(r["job_state"] for r in records)
    by_corrected_type = Counter(r["corrected_apply_type"] for r in records)
    by_policy = Counter(r["policy_action"] for r in records)
    would_queued = sum(1 for r in records if r.get("migrate_to_queued"))
    would_terminal = sum(1 for r in records if "reject" in r["proposed_action"])
    would_hold = sum(
        1 for r in records
        if r["proposed_action"].startswith("migrate_") and not r.get("migrate_to_queued")
    )

    # Representative samples: Traction + mix of actions
    samples = []
    for needle in ("Traction", "UBC", "Basis", "Smile", "Ashby", "InvestorCOM"):
        for r in records:
            if needle.lower() in (r["company"] or "").lower() or needle.lower() in (r["title"] or "").lower():
                samples.append(r)
                break
    # Fill to ≥10 with diverse proposed actions
    for action in (
        "migrate_to_queued_easy_apply",
        "migrate_to_queued_company_site_bookmark",
        "migrate_to_queued_unverified_verify",
        "terminal_policy_reject",
        "terminal_policy_reject_expired",
    ):
        for r in records:
            if r["proposed_action"] == action and r not in samples:
                samples.append(r)
                if len(samples) >= 12:
                    break
        if len(samples) >= 12:
            break
    while len(samples) < 10 and len(samples) < len(records):
        for r in records:
            if r not in samples:
                samples.append(r)
            if len(samples) >= 10:
                break

    out = {
        "wave": "A.2",
        "mode": "report_only_no_mutation",
        "exported_at": stamp,
        "database": q.database,
        "backup_checksum_verification": backup,
        "live_index_size": len(live_index),
        "summary": {
            "total_scanned": len(records),
            "before_status": dict(before_status),
            "before_method": dict(before_method),
            "job_state": dict(by_state),
            "corrected_apply_type": dict(by_corrected_type),
            "policy_action": dict(by_policy),
            "proposed_action": dict(proposed_action),
            "would_migrate_to_queued": would_queued,
            "would_hold_inactive_or_unknown": would_hold,
            "would_terminal_reject": would_terminal,
            "would_mutate_any": sum(1 for r in records if r.get("would_mutate")),
        },
        "before_after_note": (
            "Queued migrations apply only when job_state=active (incl. live JobSpy match). "
            "Expired → terminal reject, never queued. "
            "Policy rejects → terminal rejected status, not deleted. "
            "No records were mutated."
        ),
        "representative_records": samples[:12],
        "records": records,
    }

    path = ARTIFACTS / f"wave_a2_reclassify_{stamp}.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    # Also write a slim CSV-like ids summary
    slim = ARTIFACTS / f"wave_a2_reclassify_{stamp}.summary.json"
    slim.write_text(
        json.dumps(
            {
                "summary": out["summary"],
                "backup_checksum_verification": backup,
                "representative_records": samples[:12],
                "full_report": str(path),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps({
        "wrote": str(path),
        "summary": out["summary"],
        "backup_ok": backup.get("ok"),
        "representative": [
            {
                "company": r["company"],
                "title": (r["title"] or "")[:50],
                "current": f"{r['current_status']}/{r['current_method']}",
                "corrected": r["corrected_apply_type"],
                "policy": r["policy_action"],
                "state": r["job_state"],
                "proposed": f"{r['proposed_status']}/{r['proposed_method']}",
                "action": r["proposed_action"],
                "migrate_to_queued": r.get("migrate_to_queued"),
                "would_mutate": r.get("would_mutate"),
            }
            for r in samples[:12]
        ],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
