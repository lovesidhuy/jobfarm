#!/usr/bin/env python3
"""Isolated Indeed IT Phase II canary — allowlisted job IDs only.

Does NOT claim the full queue. Runs one ``application_worker.py --job-ids …``
process so all allowlisted jobs share a single NST browser session
(``KEEP_BROWSER`` between jobs; last job closes the profile).

Usage:
  python scripts/wave_a2_canary.py --ids-file artifacts/queue-hygiene-migrate/canary_allowlist.txt
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

from core.job_queue import JobQueue  # noqa: E402

DEFAULT_ALLOWLIST = [
    # Metro-Van confirmed Easy Apply
    "d7adb7b5-0cc3-4df3-9269-26bdf379ab4c",  # Traction Rec QA Analyst
    "d20e1456-27e1-4f60-8827-5b7ce15f0d4a",  # Brownlee Help Desk
    # Metro-Van company-site bookmark
    "8ab12fbd-b4c5-4082-baa4-9f20271f7f51",  # UBC Network Analyst II
    # Metro-Van unverified verification
    "f9965bb7-3830-46ba-bb20-cb3914485ead",  # Compugen Helpdesk
    "3aa38fef-62fb-4ca2-a083-2f75c2c40cbf",  # ICBC QA Analyst
    # Outside-metro confirmed remote Easy Apply
    "c0a9a75e-3067-4277-b4f5-e4eea8722d22",  # InvestorCOM Software Engineer
]

OUT_DIR = ROOT / "artifacts" / "queue-hygiene-migrate" / "canary"


def _snap(q: JobQueue, jid: str) -> dict | None:
    r = q.jobs.find_one({"_id": jid})
    if not r:
        return None
    meta = r.get("metadata") or {}
    return {
        "id": jid,
        "status": r.get("status"),
        "method": meta.get("application_method"),
        "region": meta.get("region"),
        "title": r.get("title"),
        "company": r.get("company"),
        "location": r.get("location"),
        "url": r.get("url"),
        "result_url": r.get("result_url"),
        "last_error": r.get("last_error"),
        "attempts": r.get("attempts"),
        "lease_owner": r.get("lease_owner"),
        "hygiene_outcome": (meta.get("hygiene") or {}).get("outcome"),
        "gate_score": r.get("gate_score"),
        "gate_reason": r.get("gate_reason"),
    }


def expected_env_flags(method: str | None) -> dict:
    m = (method or "").lower()
    flags = {"JOB_QUEUE_BOOKMARK_FIRST": "1"}
    if m == "company_site":
        flags["JOB_QUEUE_BOOKMARK_ONLY"] = "1"
    if m in ("unverified", "verify", "unknown"):
        flags["JOB_QUEUE_VERIFY_APPLY_TYPE"] = "1"
    return flags


def _collect_result_payload(jid: str):
    result_artifacts = sorted(Path("/tmp").glob(f"jobbots-result-{jid}-*.json"))
    if not result_artifacts:
        return None
    try:
        return json.loads(result_artifacts[-1].read_text())
    except Exception:
        return {"path": str(result_artifacts[-1]), "parse_error": True}


def run_batch(ids: list[str], timeout_s: int) -> list[dict]:
    """Process allowlisted IDs in ONE worker / ONE NST session."""
    q = JobQueue()
    befores = {jid: _snap(q, jid) for jid in ids}
    results_meta = []
    leasable = []
    for jid in ids:
        before = befores.get(jid)
        if not before:
            results_meta.append({"id": jid, "error": "not_found", "before": None})
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
        return [
            {
                **m,
                "worker_rc": None,
                "elapsed_s": 0,
                "after": m.get("before"),
                "final_status": (m.get("before") or {}).get("status"),
                "final_method": (m.get("before") or {}).get("method"),
                "result_url": (m.get("before") or {}).get("result_url"),
                "retry_or_terminal_reason": (m.get("before") or {}).get("last_error"),
                "result_payload": None,
                "log_path": None,
                "diagnostic_lines": [],
            }
            for m in results_meta
        ]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = OUT_DIR / f"canary_batch_{stamp}.log"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "application_worker.py"),
        "--job-ids", ",".join(leasable),
        "--portal", "indeed",
        "--profile", "it",
    ]
    env = os.environ.copy()
    # Ensure we do not flip discovery engine
    env.pop("DISCOVERY_ENGINE", None)
    env["JOBBOT_MODE"] = "apply"
    # Quota-critical: reuse NSTBROWSER_PROFILE_ID_INDEED_IT only — never create.
    env["NSTBROWSER_FORBID_CREATE"] = "1"
    env.pop("NSTBROWSER_ROTATE_PROFILE", None)
    # Worker keeps NST open between jobs automatically when --job-ids has more
    # than one remaining; do not force KEEP_BROWSER for the final job.
    from core.browser.nst_profile_safety import require_existing_nst_profile_id
    from core.supervisor_runtime import merge_dotenv_into_env

    merge_dotenv_into_env(env, ROOT / ".env")
    indeed_it_pid = (
        env.get("NSTBROWSER_PROFILE_ID")
        or env.get("NSTBROWSER_PROFILE_ID_INDEED_IT")
        or ""
    ).strip()
    require_existing_nst_profile_id(
        indeed_it_pid,
        bot_name="indeed_it",
        env_key="NSTBROWSER_PROFILE_ID_INDEED_IT",
    )
    env["NSTBROWSER_PROFILE_ID"] = indeed_it_pid
    env.setdefault("NSTBROWSER_PROFILE_ID_INDEED_IT", indeed_it_pid)
    print(
        f"[canary] Reusing existing NST profile NSTBROWSER_PROFILE_ID_INDEED_IT="
        f"{indeed_it_pid} (FORBID_CREATE=1)",
        flush=True,
    )

    per_job_timeout = max(timeout_s, 120)
    batch_timeout = per_job_timeout * len(leasable)
    print(
        f"[canary] One NST session for {len(leasable)} jobs "
        f"(timeout={batch_timeout}s): {leasable}",
        flush=True,
    )

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=batch_timeout,
        )
        log_path.write_text(
            (proc.stdout or "") + "\n--- STDERR ---\n" + (proc.stderr or ""),
            encoding="utf-8",
        )
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"")
        err = (exc.stderr or b"")
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="ignore")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="ignore")
        log_path.write_text(out + "\n--- STDERR ---\n" + err + "\nTIMEOUT\n", encoding="utf-8")
        for jid in leasable:
            q.jobs.update_one(
                {"_id": jid, "status": "leased"},
                {"$set": {
                    "status": "retry",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_error": "canary_timeout",
                }},
            )
        rc = -1

    elapsed = round(time.time() - t0, 1)
    diag = []
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            low = line.lower()
            if (
                "screenshot" in low
                or "artifact" in low
                or "/tmp/" in line
                or "keep_browser" in low
                or "nstbrowser" in low
                or "resume tailor" in low
            ):
                diag.append(line.strip()[:300])
    except Exception:
        pass

    out_results = []
    for meta in results_meta:
        jid = meta["id"]
        if meta.get("error") or meta.get("skipped"):
            out_results.append({
                **meta,
                "worker_rc": None if meta.get("skipped") or meta.get("error") else rc,
                "elapsed_s": 0,
                "after": meta.get("before"),
                "final_status": (meta.get("before") or {}).get("status"),
                "final_method": (meta.get("before") or {}).get("method"),
                "result_url": (meta.get("before") or {}).get("result_url"),
                "retry_or_terminal_reason": (meta.get("before") or {}).get("last_error"),
                "result_payload": None,
                "log_path": str(log_path),
                "diagnostic_lines": diag[:40],
            })
            continue
        after = _snap(q, jid)
        out_results.append({
            **meta,
            "worker_rc": rc,
            "elapsed_s": elapsed,
            "after": after,
            "result_payload": _collect_result_payload(jid),
            "log_path": str(log_path),
            "diagnostic_lines": diag[:40],
            "final_status": (after or {}).get("status"),
            "final_method": (after or {}).get("method"),
            "result_url": (after or {}).get("result_url"),
            "retry_or_terminal_reason": (after or {}).get("last_error"),
            "single_nst_session": True,
        })
    return out_results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file", default="")
    ap.add_argument("--timeout-seconds", type=int, default=600,
                    help="Per-job timeout budget; batch timeout = this × leasable count")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    if args.ids_file:
        ids = [
            ln.strip() for ln in Path(args.ids_file).read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    else:
        ids = list(DEFAULT_ALLOWLIST)
        allow = OUT_DIR.parent / "canary_allowlist.txt"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        allow.write_text("\n".join(ids) + "\n", encoding="utf-8")

    ids = ids[: args.limit]
    print(f"[canary] Allowlist ({len(ids)}): {ids}", flush=True)
    print("[canary] DISCOVERY_ENGINE will remain unset/legacy (not set to new)", flush=True)
    print("[canary] Mode: single worker --job-ids (one NST window)", flush=True)

    results = run_batch(ids, args.timeout_seconds)
    for r in results:
        print(
            json.dumps({
                "id": r.get("id"),
                "final": r.get("final_status"),
                "method": r.get("final_method"),
                "rc": r.get("worker_rc"),
                "skipped": r.get("skipped"),
                "error": r.get("error"),
            }),
            flush=True,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"canary_report_{stamp}.json"
    payload = {
        "wave": "A.2-canary",
        "stamp": stamp,
        "discovery_engine_env": os.getenv("DISCOVERY_ENGINE", ""),
        "single_nst_session": True,
        "allowlist": ids,
        "results": results,
        "summary": {
            "ran": len(results),
            "by_final_status": {
                s: sum(1 for r in results if r.get("final_status") == s)
                for s in sorted({r.get("final_status") for r in results})
            },
        },
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"wrote": str(out), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
