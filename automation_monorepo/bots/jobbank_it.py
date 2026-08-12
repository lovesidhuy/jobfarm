#!/usr/bin/env python3
"""Job Bank IT apply entrypoint (queue-driven).

Every Job Bank application uses authenticated Direct Apply in the dedicated
``jobbank_it`` NST profile.  Email instructions are discovery-only legacy
content and are deliberately not submitted by this worker.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.secret_manager  # noqa: F401
from core.jobbank_direct_apply import DIRECT_APPLY_METHOD, apply_jobbank_direct_queue_job


def main() -> int:
    raw = os.environ.get("JOB_QUEUE_DIRECT_JOB") or ""
    if not raw:
        print("[jobbank_it] missing JOB_QUEUE_DIRECT_JOB", flush=True)
        return 2
    try:
        job = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[jobbank_it] bad JOB_QUEUE_DIRECT_JOB: {exc}", flush=True)
        return 2

    dry = str(os.environ.get("JOBBANK_DRY_RUN") or "").lower() in {"1", "true", "yes", "on"}
    method = str((job.get("metadata") or {}).get("application_method") or "").strip().lower()
    if method != DIRECT_APPLY_METHOD:
        ok, reason, result_url = False, "jobbank_email_apply_retired", job.get("url") or ""
    else:
        ok, reason, result_url = apply_jobbank_direct_queue_job(job, dry_run=dry)
    # Write result file for application_worker when present
    result_path = (
        os.environ.get("JOB_QUEUE_RESULT_FILE")
        or os.environ.get("JOB_QUEUE_RESULT_PATH")
        or ""
    ).strip()
    payload = {
        "ok": ok,
        "status": "already_applied" if reason == "already_confirmed" else ("applied" if ok else "failed"),
        "reason": reason,
        "portal": "jobbank",
        "application_method": method,
        "result_url": result_url,
    }
    if result_path:
        try:
            Path(result_path).write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            print(f"[jobbank_it] result write failed: {exc}", flush=True)
    print(f"[jobbank_it] ok={ok} reason={reason}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
