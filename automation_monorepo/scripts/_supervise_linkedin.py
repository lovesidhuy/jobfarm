#!/usr/bin/env python3
"""Supervised LinkedIn runner — one job at a time until 10 confirmed."""
import os, sys, subprocess, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.job_queue import JobQueue, _now

TARGET = 10
LOG_PATH = "/tmp/supervise_li.log"

def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

def queue_snapshot():
    jq = JobQueue()
    li_filter = {"portal": "linkedin"}
    q = jq.jobs.count_documents({**li_filter, "status": "queued"})
    a = jq.jobs.count_documents({**li_filter, "status": "applied"})
    d = jq.jobs.count_documents({**li_filter, "status": "dead"})
    s = jq.jobs.count_documents({**li_filter, "status": "skipped"})
    aa = jq.jobs.count_documents({**li_filter, "status": "already_applied"})
    r = jq.jobs.count_documents({**li_filter, "status": "retry"})
    le = jq.jobs.count_documents({**li_filter, "status": "leased"})
    return q, a, d, s, aa, r, le

def main():
    os.makedirs(os.path.dirname(LOG_PATH) if os.path.dirname(LOG_PATH) else ".", exist_ok=True)
    log("=== SUPERVISED LINKEDIN RUNNER STARTED ===")
    
    # Release any stuck jobs
    jq = JobQueue()
    now = _now()
    jq.jobs.update_many(
        {"portal": "linkedin", "status": "leased"},
        {"$set": {"status": "queued", "lease_owner": None, "lease_expires_at": None, "updated_at": now}}
    )
    jq.jobs.update_many(
        {"portal": "linkedin", "status": "retry", "attempts": {"$lt": 3}},
        {"$set": {"status": "queued", "updated_at": now}}
    )
    
    applied = jq.jobs.count_documents({"portal": "linkedin", "status": "applied"})
    log(f"Starting applied count: {applied}")
    
    round_num = 0
    max_rounds = 40
    
    while applied < TARGET and round_num < max_rounds:
        round_num += 1
        q, a, d, s, aa, r, le = queue_snapshot()
        log(f"--- ROUND {round_num} | queued={q} applied={a} dead={d} skipped={s} already={aa} retry={r} ---")
        
        if q == 0:
            log("No more queued LinkedIn jobs. Stopping.")
            break
        
        # Take snapshot of top queued job BEFORE running
        top = jq.jobs.find_one(
            {"portal": "linkedin", "status": "queued"},
            sort=[("priority", -1), ("discovered_at", -1)]
        )
        if not top:
            break
        top_id = top["_id"]
        log(f"  Target: {top_id} | {top.get('title','?')} | {top.get('company','?')}")
        
        # Run the worker
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["NSTBROWSER_ACTIVE_SLOT"] = "1"
        env["NSTBROWSER_FORBID_CREATE"] = "1"
        env["KEEP_BROWSER"] = "1"
        env["NSTBROWSER_KEEP_ALIVE"] = "1"
        env["BROWSER_VENDOR"] = "nstbrowser"
        
        worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "application_worker.py")
        
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, worker_script, "--portal", "linkedin", "--once", "--keep-browser", "--poll-seconds", "10"],
            env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            timeout=180  # 3 minutes max per job
        )
        elapsed = time.time() - t0
        
        # Determine outcome — check the most recently updated LinkedIn job
        recent = jq.jobs.find_one(
            {"portal": "linkedin"},
            sort=[("updated_at", -1)]
        )
        
        status = recent.get("status", "unknown") if recent else "unknown"
        title = recent.get("title", "?") if recent else "?"
        company = recent.get("company", "?") if recent else "?"
        error = (recent.get("last_error", "") or "")[:120] if recent else ""
        attempts = recent.get("attempts", 0) if recent else 0
        
        log(f"  Outcome: {status} | {title} | {company} | att={attempts} | {elapsed:.0f}s")
        if error:
            log(f"  Error: {error}")
        
        # Extract key lines from worker output
        for line in proc.stdout.split("\n") + proc.stderr.split("\n"):
            if any(kw in line for kw in ["✓", "⚠️", "applied", "Submit", "confirm", "Giving up", "dead", "skipped"]):
                log(f"  LOG: {line.strip()[:160]}")
        
        if status == "applied":
            applied = jq.jobs.count_documents({"portal": "linkedin", "status": "applied"})
            log(f"  ✅ CONFIRMED #{applied - a} new! Total: {applied}/{TARGET}")
        elif status in ("dead", "skipped", "already_applied"):
            log(f"  ⏭️ Skipped ({status})")
        else:
            log(f"  ⚠️ Unusual: {status}")
    
    q, a, d, s, aa, r, le = queue_snapshot()
    log(f"=== FINAL: applied={a} queued={q} dead={d} skipped={s} already_applied={aa} retry={r} ===")
    log(f"=== CONFIRMED LINKEDIN APPLICATIONS THIS SESSION: {max(0, a - jq.jobs.count_documents({portal:'linkedin', status:'applied', applied_day: time.strftime('%Y-%m-%d')}))} ===")


if __name__ == "__main__":
    main()