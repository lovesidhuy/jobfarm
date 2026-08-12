#!/usr/bin/env python3
"""Watch local NST agent + single LinkedIn worker. No Docker. Stop on plan-limit thrash."""
from __future__ import annotations
import json, os, signal, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG = ROOT / "logs" / "monitor" / "local_agent_monitor.log"
STATUS = ROOT / "logs" / "monitor" / "local_agent_status.json"
WORKER_PID = ROOT / "logs" / "workers" / "linkedin_worker.pid"
WORKER_LOG = ROOT / "logs" / "workers" / "linkedin_worker.log"
_stop = False

def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")

def agent_ok(key: str) -> bool:
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8848/api/v2/browsers",
            headers={"x-api-key": key},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode())
            return d.get("code") in (0, 200)
    except Exception:
        return False

def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def plan_limit_in_log() -> bool:
    if not WORKER_LOG.is_file():
        return False
    t = WORKER_LOG.read_text(errors="replace")[-12000:].lower()
    return "exceeded plan limits" in t or "code=6001" in t or '"code":6001' in t

def ensure_worker(env: dict) -> int | None:
    if WORKER_PID.is_file():
        try:
            pid = int(WORKER_PID.read_text().strip())
            if pid_alive(pid):
                return pid
        except Exception:
            pass
    if plan_limit_in_log():
        log("plan-limit seen in log — NOT restarting linkedin worker")
        return None
    logf = open(WORKER_LOG, "a")
    p = subprocess.Popen(
        [sys.executable, "-u", "scripts/application_worker.py",
         "--portal", "linkedin", "--keep-browser", "--poll-seconds", "30"],
        cwd=str(ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    WORKER_PID.write_text(str(p.pid))
    log(f"started linkedin worker pid={p.pid}")
    return p.pid

def main():
    global _stop
    signal.signal(signal.SIGTERM, lambda *_: globals().update(_stop=True))
    signal.signal(signal.SIGINT, lambda *_: globals().update(_stop=True))
    # load .env
    env = dict(os.environ)
    for line in (ROOT / ".env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env.setdefault(k.strip(), v.strip())
    env["NSTBROWSER_ACTIVE_SLOT"] = "2"
    env["BROWSER_VENDOR"] = "nstbrowser"
    env["KEEP_BROWSER"] = "1"
    env["NSTBROWSER_KEEP_ALIVE"] = "1"
    env["NSTBROWSER_FORBID_CREATE"] = "1"
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    key2 = (env.get("NSTBROWSER_API_KEY_2") or "").strip()
    env["NSTBROWSER_API_KEY"] = key2  # dual-slot may still override via resolve
    log("monitor start local-agent mode slot=2 (no docker)")
    while not _stop:
        ok = agent_ok(key2)
        wpid = ensure_worker(env) if ok else None
        counts = {}
        try:
            from core.job_queue import JobQueue
            q = JobQueue()
            try:
                q.release_expired()
            except Exception:
                pass
            counts = q.counts()
        except Exception as e:
            counts = {"error": str(e)}
        status = {
            "tick": datetime.now(timezone.utc).isoformat(),
            "agent_ok": ok,
            "worker_pid": wpid,
            "plan_limit": plan_limit_in_log(),
            "counts": counts,
            "mode": "local_nst_agent_slot2",
        }
        STATUS.write_text(json.dumps(status, indent=2, default=str))
        log(f"HEALTH agent={ok} worker={wpid} plan_limit={status['plan_limit']} counts={counts}")
        if not ok:
            log("Nstbrowser agent not on :8848 — open the app")
        for _ in range(60):
            if _stop:
                break
            time.sleep(1)
    log("monitor exit")

if __name__ == "__main__":
    raise SystemExit(main())
