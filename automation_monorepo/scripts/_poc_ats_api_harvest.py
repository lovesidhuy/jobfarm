#!/usr/bin/env python3
"""PoC: harvest ALL open GH/Lever jobs via official public APIs.

Slug sources: prior artifacts in artifacts/wave-google-ats/*.json
APIs (no auth, no key, no bot-fight):
  GH:    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false
  Lever: https://api.lever.co/v0/postings/{slug}?mode=json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "wave-google-ats"

LOC_RE = re.compile(r"vancouver|british columbia|,\s*bc\b|burnaby|richmond|surrey|coquitlam|"
                    r"remote.*(canada|bc)|canada.*remote", re.I)
TERMS_RE = re.compile(r"\b(qa|quality assurance|sdet|it support|help ?desk|service desk|"
                      r"desktop support|technical support|sysadmin|systems administrator|"
                      r"it intern|it co-?op|software engineer|developer|devops|sre|"
                      r"site reliability|data engineer|network|security analyst)\b", re.I)


def collect_slugs() -> tuple[set[str], set[str]]:
    gh, lever = set(), set()
    for f in ART.glob("*.json"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r"(?:boards|job-boards)\.greenhouse\.io/([a-z0-9_-]{2,60})", text):
            gh.add(m.group(1))
        for m in re.finditer(r"jobs\.lever\.co/([a-z0-9_-]{2,60})", text):
            lever.add(m.group(1))
    return gh, lever


def gh_jobs(token: str, session: requests.Session) -> list[dict] | None:
    try:
        r = session.get(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            params={"content": "false"}, timeout=10,
        )
        if r.status_code != 200:
            return None
        return r.json().get("jobs", [])
    except Exception:
        return None


def lever_jobs(slug: str, session: requests.Session) -> list[dict] | None:
    try:
        r = session.get(f"https://api.lever.co/v0/postings/{slug}",
                        params={"mode": "json"}, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def main() -> int:
    gh_slugs, lever_slugs = collect_slugs()
    print(f"slugs discovered from artifacts: GH={len(gh_slugs)} Lever={len(lever_slugs)}")

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (compatible; job-harvest-poc/1.0)"

    stats = {"gh_boards_ok": 0, "gh_boards_dead": 0, "lever_ok": 0, "lever_dead": 0,
             "total_jobs": 0, "van_bc_jobs": 0}
    van_jobs: list[dict] = []

    t0 = time.time()
    for tok in sorted(gh_slugs):
        jobs = gh_jobs(tok, s)
        if jobs is None:
            stats["gh_boards_dead"] += 1
            continue
        stats["gh_boards_ok"] += 1
        stats["total_jobs"] += len(jobs)
        for j in jobs:
            loc = (j.get("location") or {}).get("name", "")
            title = j.get("title", "")
            if LOC_RE.search(loc) and TERMS_RE.search(title):
                van_jobs.append({
                    "ats": "greenhouse", "board": tok, "title": title,
                    "location": loc, "apply_url": j.get("absolute_url"),
                })
                stats["van_bc_jobs"] += 1

    for slug in sorted(lever_slugs):
        jobs = lever_jobs(slug, s)
        if jobs is None:
            stats["lever_dead"] += 1
            continue
        stats["lever_ok"] += 1
        stats["total_jobs"] += len(jobs)
        for j in jobs:
            loc = (j.get("categories") or {}).get("location", "")
            title = j.get("text", "")
            if LOC_RE.search(loc or "") and TERMS_RE.search(title or ""):
                van_jobs.append({
                    "ats": "lever", "board": slug, "title": title,
                    "location": loc, "apply_url": j.get("hostedUrl"),
                })
                stats["van_bc_jobs"] += 1

    dt = time.time() - t0
    print(f"\n--- results in {dt:.1f}s ({stats['gh_boards_ok']+stats['gh_boards_dead']+stats['lever_ok']+stats['lever_dead']} boards) ---")
    print(json.dumps(stats, indent=2))
    print(f"\nMetro-Van/BC + IT-role leads: {len(van_jobs)}")
    for v in van_jobs[:25]:
        print(f"  [{v['ats'][:2]}] {v['title'][:45]:45} | {v['location'][:28]:28} | {v['apply_url']}")

    out = ART / "poc_ats_api_harvest.json"
    out.write_text(json.dumps({"stats": stats, "leads": van_jobs}, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
