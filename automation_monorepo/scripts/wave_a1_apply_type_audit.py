#!/usr/bin/env python3
"""Wave A.1 — Apply-type detection audit + two-pass dry-run reporter.

Does NOT mutate the queue. Does NOT enable DISCOVERY_ENGINE=new in production.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.classification.location_policy import decide_job_policy  # noqa: E402
from core.discovery.contracts import NormalizedJob  # noqa: E402
from core.discovery.deduplicator import deduplicate  # noqa: E402
from core.discovery.normalizer import normalize_raw_job  # noqa: E402
from core.discovery.providers.base import DiscoveryRequest  # noqa: E402
from core.discovery.providers.jobspy_provider import JobSpyProvider  # noqa: E402


MANUAL_CASES = [
    {"needle_title": "QA Analyst", "needle_company": "Traction", "expected": "EASY_APPLY",
     "label": "Traction Rec QA Analyst — Vancouver"},
    {"needle_title": "Network Analyst", "needle_company": "UBC|British Columbia|University of British", "expected": "COMPANY_APPLY",
     "label": "UBC Network Analyst — Vancouver"},
    {"needle_title": "Software Engineer, Tools", "needle_company": "Basis", "expected": "COMPANY_APPLY",
     "label": "Basis Software Engineer — Vancouver"},
    {"needle_title": "Software Engineer", "needle_company": "InvestorCOM", "expected": "EASY_APPLY_IF_CONFIRMED",
     "label": "InvestorCOM — Toronto remote"},
    {"needle_title": "Site Reliability", "needle_company": "Smile", "expected": "COMPANY_APPLY",
     "label": "Smile Digital Health — Toronto remote"},
    {"needle_title": "GTM Systems", "needle_company": "Ashby", "expected": "COMPANY_APPLY",
     "label": "Ashby — Canada remote"},
]


def _match_manual(job: NormalizedJob, case: dict) -> bool:
    t = (job.job_title or "").lower()
    c = (job.company_name or "").lower()
    if case["needle_title"].lower() not in t:
        return False
    company_needles = [n.strip().lower() for n in case["needle_company"].split("|") if n.strip()]
    return any(n in c for n in company_needles)


def _row_report(job: NormalizedJob, *, search_pass: str = "") -> dict:
    decision = decide_job_policy(job)
    return {
        "title": job.job_title,
        "company": job.company_name,
        "location": job.location,
        "search_pass": search_pass or (job.query_id or ""),
        "listing_url": job.listing_url,
        "destination_url": job.destination_url,
        "apply_type": job.apply_type,
        "apply_type_source": job.apply_type_source,
        "apply_type_confirmed": bool(getattr(job, "apply_type_confirmed", False)),
        "apply_type_confidence": job.apply_type_confidence,
        "is_remote_hint": job.is_remote_hint,
        "policy_action": decision.action,
        "policy_method": decision.application_method,
        "policy_reason": decision.reason,
        "evidence": job.apply_type_source,
    }


def audit_live_scrape(*, terms: list[str], locations: list[str], max_per: int, freshness: int) -> dict:
    provider = JobSpyProvider(portals=["indeed"])
    request = DiscoveryRequest(
        profile="it",
        search_terms=terms,
        locations=locations,
        max_results_per_term=max_per,
        freshness_days=freshness,
        radius_km=25,
        easy_apply_only=False,
    )
    raw = provider.discover(request)
    normalized = []
    pass_counts: Counter = Counter()
    raw_apply_flags: Counter = Counter()
    for r in raw:
        sp = (r.raw_extras or {}).get("search_pass", "")
        pass_counts[sp or "unknown"] += 1
        flag = (r.raw_extras or {}).get("jobspy_easy_apply_row")
        raw_apply_flags[str(flag)] += 1
        job = normalize_raw_job(
            r, discovery_engine="jobspy",
            search_term=(r.raw_extras or {}).get("search_term", ""),
            location=r.location,
            freshness_days=freshness,
        )
        # Stash pass on query_id suffix for reporting
        if sp:
            job.query_id = f"{job.query_id}|{sp}"
        normalized.append(job)

    before = len(normalized)
    deduped = deduplicate(normalized)

    type_counts = Counter(j.apply_type for j in deduped)
    confirmed_ea = sum(1 for j in deduped if j.apply_type == "EASY_APPLY" and j.apply_type_confirmed)
    company = sum(1 for j in deduped if j.apply_type == "COMPANY_APPLY")
    unknown = sum(1 for j in deduped if j.apply_type == "UNKNOWN")

    policy = Counter()
    reports = []
    for j in deduped:
        sp = ""
        if "|" in (j.query_id or ""):
            sp = j.query_id.split("|", 1)[1]
        rep = _row_report(j, search_pass=sp)
        policy[rep["policy_action"]] += 1
        reports.append(rep)

    # Prefer rows that still carry easy-apply pass evidence after dedup
    reports.sort(key=lambda r: (0 if r["apply_type"] == "EASY_APPLY" else 1, r["title"] or ""))

    manual = []
    for case in MANUAL_CASES:
        hits = [j for j in deduped if _match_manual(j, case)]
        if not hits:
            manual.append({**case, "found": False, "note": "not in this scrape window"})
            continue
        # Prefer confirmed Easy Apply hit if multiple
        hits.sort(key=lambda j: (0 if j.apply_type == "EASY_APPLY" else 1))
        j = hits[0]
        sp = j.query_id.split("|", 1)[1] if "|" in (j.query_id or "") else ""
        manual.append({
            "label": case["label"],
            "expected": case["expected"],
            "found": True,
            **_row_report(j, search_pass=sp),
        })

    return {
        "raw_count": len(raw),
        "normalized_count": before,
        "deduped_count": len(deduped),
        "counts_by_search_pass": dict(pass_counts),
        "raw_jobspy_easy_apply_row_values": dict(raw_apply_flags),
        "apply_type_counts": dict(type_counts),
        "confirmed_easy_apply": confirmed_ea,
        "company_site": company,
        "unknown_verify": unknown,
        "policy_counts": dict(policy),
        "manual_cases": manual,
        "sample_20": reports[:20],
    }


def full_dry_run(*, bypass_screening: bool, terms: list[str]) -> dict:
    os.environ["DISCOVERY_GEO_POLICY"] = "1"
    if bypass_screening:
        os.environ["BYPASS_SCREENING"] = "1"
    else:
        os.environ.pop("BYPASS_SCREENING", None)

    from core.discovery import planner
    from core.discovery.classification import location_policy as lp

    decisions = []
    orig = lp.decide_job_policy

    def wrap(job):
        d = orig(job)
        decisions.append({
            "title": job.job_title,
            "company": job.company_name,
            "location": job.location,
            "apply_type": job.apply_type,
            "apply_type_source": job.apply_type_source,
            "apply_type_confirmed": bool(getattr(job, "apply_type_confirmed", False)),
            "action": d.action,
            "method": d.application_method,
            "reason": d.reason,
        })
        return d

    # Planner binds the name at import time — patch both.
    lp.decide_job_policy = wrap
    planner.decide_job_policy = wrap

    result = planner.run_discovery(
        profile="it",
        portals=["indeed"],
        dry_run=True,
        max_results_per_term=12,
        freshness_days=7,
        search_terms=terms,
    )

    by_action = Counter(d["action"] for d in decisions)
    by_type = Counter(d["apply_type"] for d in decisions)
    confirmed = sum(1 for d in decisions if d["apply_type"] == "EASY_APPLY" and d["apply_type_confirmed"])

    manual = []
    for case in MANUAL_CASES:
        title_n = case["needle_title"].lower()
        company_ns = [n.strip().lower() for n in case["needle_company"].split("|") if n.strip()]
        hits = [
            d for d in decisions
            if title_n in (d["title"] or "").lower()
            and any(n in (d["company"] or "").lower() for n in company_ns)
        ]
        manual.append({
            "label": case["label"],
            "expected": case["expected"],
            "found": bool(hits),
            "hits": hits[:3],
        })

    return {
        "bypass_screening": bypass_screening,
        "planner_result": result,
        "policy_decision_counts": dict(by_action),
        "apply_type_counts_pre_screen": dict(by_type),
        "confirmed_easy_apply_pre_screen": confirmed,
        "it_screen_passed": result.get("passed"),
        "it_screen_rejected": result.get("rejected"),
        "manual_cases": manual,
        "sample_decisions": decisions[:15],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["audit", "dry-policy", "dry-screen", "all"], default="all")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "wave-a1-apply-type-audit.json"))
    args = ap.parse_args()

    terms = ["QA Analyst", "IT Support", "Help Desk Analyst", "Network Analyst", "Software Engineer"]
    locations = ["Vancouver, BC", "Surrey, BC", ""]  # metro + remote pass

    out: dict = {"wave": "A.1", "modes": {}}
    if args.mode in ("audit", "all"):
        print("[A.1] Live JobSpy apply-type audit (two-pass)…", flush=True)
        out["modes"]["audit"] = audit_live_scrape(
            terms=terms, locations=locations, max_per=15, freshness=7,
        )
    if args.mode in ("dry-policy", "all"):
        print("[A.1] Policy-only discovery dry-run (BYPASS_SCREENING=1)…", flush=True)
        out["modes"]["dry_policy"] = full_dry_run(bypass_screening=True, terms=terms)
    if args.mode in ("dry-screen", "all"):
        print("[A.1] Real Phase I dry-run with IT screening…", flush=True)
        out["modes"]["dry_screen"] = full_dry_run(bypass_screening=False, terms=terms)

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "wrote": str(path),
        "audit_summary": {
            k: out["modes"].get("audit", {}).get(k)
            for k in (
                "raw_count", "deduped_count", "counts_by_search_pass",
                "apply_type_counts", "confirmed_easy_apply", "company_site",
                "unknown_verify", "policy_counts",
            )
        } if "audit" in out["modes"] else None,
        "dry_policy": out["modes"].get("dry_policy", {}).get("planner_result"),
        "dry_screen": out["modes"].get("dry_screen", {}).get("planner_result"),
        "manual_audit": out["modes"].get("audit", {}).get("manual_cases"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
