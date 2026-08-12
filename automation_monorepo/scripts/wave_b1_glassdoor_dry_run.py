#!/usr/bin/env python3
"""Wave B.1 — Glassdoor policy dry-run (no queue mutate).

Counts Metro Van EA applies vs rejected (outside-metro / non-EA / Indeed-sync).
Work-mode breakdown is for awareness only (not a reject reason in Metro Van).

Usage:
  python scripts/wave_b1_glassdoor_dry_run.py
  python scripts/wave_b1_glassdoor_dry_run.py --terms "QA Analyst" --max-per 10
  python scripts/wave_b1_glassdoor_dry_run.py --synthetic-only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.classification.location_policy import (  # noqa: E402
    decide_job_policy,
    detect_work_mode,
)
from core.discovery.contracts import NormalizedJob  # noqa: E402
from core.discovery.indeed_sync import IndeedSyncIndex, glassdoor_already_on_indeed  # noqa: E402
from core.discovery.normalizer import normalize_raw_job  # noqa: E402
from core.discovery.planner import (  # noqa: E402
    _load_search_locations,
    _load_search_policy,
    _load_search_terms,
)
from core.discovery.providers.base import DiscoveryRequest  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "wave-b1-glassdoor"


def _gd_job(
    location: str,
    *,
    apply_type: str = "EASY_APPLY",
    description: str = "",
    is_remote_hint: bool = False,
    title: str = "QA Analyst",
    company: str = "Acme",
    source_refs: list | None = None,
    listing_url: str = "https://www.glassdoor.com/job-listing/j123",
) -> NormalizedJob:
    return NormalizedJob(
        source_platform="glassdoor",
        source_job_id="gd-dry",
        discovery_engine="jobspy",
        query_id="q",
        job_title=title,
        company_name=company,
        location=location,
        description=description,
        date_posted=None,
        listing_url=listing_url,
        destination_url=None,
        apply_type=apply_type,
        is_remote_hint=is_remote_hint,
        source_refs=source_refs or [{"platform": "glassdoor", "job_id": "gd-dry"}],
    )


def synthetic_matrix() -> list[dict]:
    """Locked Wave B.1 policy cases (no network)."""
    cases = [
        ("metro_onsite_ea", _gd_job("Vancouver, BC"), "APPLY"),
        ("metro_hybrid_ea", _gd_job("Burnaby, BC", description="Hybrid work"), "APPLY"),
        ("metro_remote_ea", _gd_job("Surrey, BC", description="Fully remote", is_remote_hint=True), "APPLY"),
        ("toronto_remote_ea", _gd_job("Toronto, ON", is_remote_hint=True), "REJECT"),
        ("canada_remote_ea", _gd_job("Canada", is_remote_hint=True), "REJECT"),
        ("metro_company_site", _gd_job("Vancouver, BC", apply_type="COMPANY_APPLY"), "REJECT"),
        ("metro_unknown", _gd_job("Vancouver, BC", apply_type="UNKNOWN"), "REJECT"),
    ]
    rows = []
    for label, job, expected in cases:
        d = decide_job_policy(job)
        rows.append({
            "label": label,
            "expected": expected,
            "action": d.action,
            "reason": d.reason,
            "work_mode": d.work_mode,
            "ok": d.action == expected,
        })
    return rows


def classify_batch(jobs: list[NormalizedJob], sync_index: IndeedSyncIndex) -> dict:
    counters: Counter = Counter()
    by_location: Counter = Counter()
    by_work_mode: Counter = Counter()
    samples: list[dict] = []

    for job in jobs:
        if (job.source_platform or "").lower() != "glassdoor":
            continue
        decision = decide_job_policy(job)
        wm = detect_work_mode(
            job.location, job.description, is_remote_hint=bool(job.is_remote_hint)
        )
        by_work_mode[wm] += 1
        by_location[(job.location or "")[:60]] += 1

        skip, sync_reason = glassdoor_already_on_indeed(job, index=sync_index)
        row = {
            "title": job.job_title,
            "company": job.company_name,
            "location": job.location,
            "apply_type": job.apply_type,
            "work_mode": wm,
            "policy_action": decision.action,
            "policy_reason": decision.reason,
            "indeed_sync": sync_reason if skip else None,
        }
        if len(samples) < 40:
            samples.append(row)

        if skip:
            counters["glassdoor_skipped_indeed_sync"] += 1
            continue
        if decision.action == "APPLY":
            counters["glassdoor_enqueued_ea"] += 1
        elif decision.reason == "glassdoor_outside_metro":
            counters["glassdoor_rejected_outside_metro"] += 1
        elif decision.reason == "glassdoor_non_easy_apply":
            counters["glassdoor_rejected_non_ea"] += 1
        else:
            counters[f"other_{decision.reason}"] += 1

    return {
        "counters": dict(counters),
        "by_location": dict(by_location.most_common(30)),
        "by_work_mode": dict(by_work_mode),
        "samples": samples,
    }


def live_scrape(*, terms: list[str], max_per: int, freshness: int) -> list[NormalizedJob]:
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.deduplicator import deduplicate

    locations = _load_search_locations("it", ["glassdoor"])
    policy = _load_search_policy("it", ["glassdoor"])
    provider = JobSpyProvider(portals=["glassdoor"])
    request = DiscoveryRequest(
        profile="it",
        search_terms=terms,
        locations=locations,
        max_results_per_term=max_per,
        freshness_days=freshness,
        radius_km=int(policy.get("radius_km") or 25),
        easy_apply_only=bool(policy.get("easy_apply_only", True)),
    )
    raw = provider.discover(request)
    jobs: list[NormalizedJob] = []
    for r in raw:
        job = normalize_raw_job(
            r,
            discovery_engine="jobspy",
            search_term=(r.raw_extras or {}).get("search_term", ""),
            location=r.location,
            freshness_days=freshness,
        )
        jobs.append(job)
    return deduplicate(jobs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synthetic-only", action="store_true")
    ap.add_argument("--terms", nargs="*", default=None)
    ap.add_argument("--max-per", type=int, default=15)
    ap.add_argument("--freshness-days", type=int, default=7)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "locations": _load_search_locations("it", ["glassdoor"]),
        "policy": _load_search_policy("it", ["glassdoor"]),
        "synthetic": synthetic_matrix(),
    }
    assert all(r["ok"] for r in report["synthetic"]), report["synthetic"]

    # Empty sync index for synthetic; live path loads queue/history when possible.
    sync = IndeedSyncIndex(queue=None, history_ids=set(), load_history=False)

    if not args.synthetic_only:
        terms = args.terms or _load_search_terms("it")[:3]
        try:
            from core.job_queue import JobQueue
            sync = IndeedSyncIndex(queue=JobQueue(), load_history=True)
        except Exception as exc:
            report["sync_index_error"] = str(exc)
            sync = IndeedSyncIndex(queue=None, load_history=True)

        try:
            jobs = live_scrape(terms=terms, max_per=args.max_per, freshness=args.freshness_days)
            report["live"] = {
                "terms": terms,
                "raw_count": len(jobs),
                **classify_batch(jobs, sync),
            }
        except Exception as exc:
            report["live_error"] = str(exc)

    out = args.out or (OUT_DIR / f"dry_run_{report['ts']}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(out),
        "locations": report["locations"],
        "synthetic_ok": all(r["ok"] for r in report["synthetic"]),
        "live_counters": (report.get("live") or {}).get("counters"),
        "live_error": report.get("live_error"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
