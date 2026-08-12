#!/usr/bin/env python3
"""Isolated Glassdoor IT Phase II canary — allowlisted job IDs only (Wave B.1).

Does NOT claim the full queue. Runs one ``application_worker.py --job-ids …``
process so allowlisted jobs share a single NST browser session.

Usage:
  python scripts/wave_b1_canary.py --ids-file artifacts/wave-b1-glassdoor/canary_allowlist.txt
  python scripts/wave_b1_canary.py --ids id1,id2
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.supervisor_runtime import merge_dotenv_into_env  # noqa: E402
merge_dotenv_into_env(os.environ, ROOT / ".env", override=False)

from core.job_queue import JobQueue  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "wave-b1-glassdoor" / "canary"


def expected_env_flags(method: str | None, portal: str | None = "glassdoor") -> dict:
    """Glassdoor never sets bookmark/verify flags (Wave B.1)."""
    if (portal or "").lower() == "glassdoor":
        return {}
    m = (method or "").lower()
    flags = {"JOB_QUEUE_BOOKMARK_FIRST": "1"}
    if m == "company_site":
        flags["JOB_QUEUE_BOOKMARK_ONLY"] = "1"
    if m in ("unverified", "verify", "unknown"):
        flags["JOB_QUEUE_VERIFY_APPLY_TYPE"] = "1"
    return flags


def _snap(q: JobQueue, jid: str) -> dict | None:
    r = q.jobs.find_one({"_id": jid})
    if not r:
        return None
    meta = r.get("metadata") or {}
    return {
        "id": jid,
        "status": r.get("status"),
        "portal": r.get("portal"),
        "method": meta.get("application_method"),
        "region": meta.get("region"),
        "title": r.get("title"),
        "company": r.get("company"),
        "location": r.get("location"),
        "url": r.get("url"),
        "result_url": r.get("result_url"),
        "last_error": r.get("last_error"),
        "attempts": r.get("attempts"),
    }


def run_batch(ids: list[str], timeout_s: int) -> list[dict]:
    q = JobQueue()
    befores = {jid: _snap(q, jid) for jid in ids}
    results_meta = []
    leasable = []
    for jid in ids:
        before = befores.get(jid)
        if not before:
            results_meta.append({"id": jid, "error": "not_found", "before": None})
            continue
        if before.get("portal") != "glassdoor":
            results_meta.append({
                "id": jid,
                "skipped": True,
                "reason": f"portal={before.get('portal')} (want glassdoor)",
                "before": before,
            })
            continue
        if before.get("method") != "easy_apply":
            results_meta.append({
                "id": jid,
                "skipped": True,
                "reason": f"method={before.get('method')} (want easy_apply)",
                "before": before,
            })
            continue
        if before["status"] not in ("queued", "retry"):
            results_meta.append({
                "id": jid,
                "skipped": True,
                "reason": f"status={before['status']} not leasable",
                "before": before,
                "expected_env_flags": expected_env_flags(before.get("method")),
            })
            continue
        leasable.append(jid)
        results_meta.append({
            "id": jid,
            "before": before,
            "expected_env_flags": expected_env_flags(before.get("method")),
        })

    if not leasable:
        return results_meta

    env = dict(os.environ)
    env["NSTBROWSER_FORBID_CREATE"] = "1"
    env["KEEP_BROWSER"] = "1"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "application_worker.py"),
        "--portal", "glassdoor",
        "--profile", "it",
        "--job-ids", ",".join(leasable),
        "--once",
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, timeout=timeout_s)
    elapsed = round(time.time() - t0, 1)

    for m in results_meta:
        if m.get("skipped") or m.get("error"):
            continue
        jid = m["id"]
        m["worker_rc"] = proc.returncode
        m["elapsed_s"] = elapsed
        m["after"] = _snap(q, jid)

    return results_meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ids", default="")
    ap.add_argument("--ids-file", type=Path, default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    ids: list[str] = []
    if args.ids_file and args.ids_file.is_file():
        ids.extend(
            line.strip()
            for line in args.ids_file.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if args.ids:
        ids.extend(x.strip() for x in args.ids.split(",") if x.strip())
    # de-dupe preserve order
    seen = set()
    uniq = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    ids = uniq
    if not ids:
        print("No job IDs provided. Use --ids or --ids-file.", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = run_batch(ids, args.timeout)
    out = OUT_DIR / f"canary_report_{ts}.json"
    payload = {"ts": ts, "ids": ids, "results": results}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "n": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
