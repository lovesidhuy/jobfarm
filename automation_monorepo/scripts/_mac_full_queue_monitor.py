#!/usr/bin/env python3
"""Full queue drain on local NST agent. ATS parallel; NST portals serialized."""
from __future__ import annotations
import json, os, signal, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "logs" / "monitor"
WDIR = ROOT / "logs" / "workers"
STATUS = LOG_DIR / "full_queue_status.json"
LOG = LOG_DIR / "full_queue_monitor.log"
_stop = False

# NST order: finish higher-priority first; never run two NST workers at once
NST_ORDER = ["linkedin", "workopolis", "indeed", "glassdoor"]
# Playwright ATS (no NST) — apply in parallel with one NST portal
ATS_PORTALS = ["greenhouse", "lever", "ashby", "bamboohr", "google"]

def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")

def load_env() -> dict:
    env = dict(os.environ)
    for line in (ROOT / ".env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env.setdefault(k.strip(), v.strip())
    # The launcher selects the slot; keep the monitor aligned with it so the
    # API key and profile IDs are never silently taken from the other account.
    slot = str(env.get("NSTBROWSER_ACTIVE_SLOT") or "1").strip()
    env["NSTBROWSER_ACTIVE_SLOT"] = slot
    env["BROWSER_VENDOR"] = "nstbrowser"
    env["NSTBROWSER_FORBID_CREATE"] = "1"
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    if slot == "2":
        key2 = (env.get("NSTBROWSER_API_KEY_2") or "").strip()
        if key2:
            env["NSTBROWSER_API_KEY"] = key2
    return env

def agent_ok(key: str) -> bool:
    try:
        req = urllib.request.Request("http://127.0.0.1:8848/api/v2/browsers", headers={"x-api-key": key})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode()).get("code") in (0, 200)
    except Exception:
        return False

def pid_alive(pid: int) -> bool:
    try:
        # ``kill(pid, 0)`` can report a zombie as alive on macOS.  Treat
        # zombies as dead so the monitor can replace a worker whose process
        # exited without cleaning its pid file.
        probe = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        state = (probe.stdout or "").strip()
        if not state or state.startswith("Z"):
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False

def read_pid(name: str) -> int | None:
    p = WDIR / f"{name}_worker.pid"
    try:
        return int(p.read_text().strip())
    except Exception:
        return None

def write_pid(name: str, pid: int) -> None:
    WDIR.mkdir(parents=True, exist_ok=True)
    (WDIR / f"{name}_worker.pid").write_text(str(pid))

def stop_worker(name: str) -> None:
    pid = read_pid(name)
    if pid and pid_alive(pid):
        try:
            os.kill(pid, 15)
            time.sleep(1)
            if pid_alive(pid):
                os.kill(pid, 9)
        except Exception:
            pass
    pf = WDIR / f"{name}_worker.pid"
    if pf.exists():
        pf.unlink(missing_ok=True)

def worker_alive(name: str) -> bool:
    pid = read_pid(name)
    return bool(pid and pid_alive(pid))

def plan_limit() -> bool:
    """Only treat as plan-limit if seen recently in an *active* worker log (last 4k)."""
    for name in NST_ORDER:
        path = WDIR / f"{name}_worker.log"
        if not path.is_file():
            continue
        try:
            # only if worker is currently running or just died
            if not worker_alive(name):
                # still check if last lines of existing log show plan limit from this session
                pass
            tail = path.read_text(errors="replace")[-4000:].lower()
            if "exceeded plan limits" in tail or "code=6001" in tail or '"code": 6001' in tail:
                # ignore archived-era if log is tiny/new and only has seed — require "nstbrowser api failed" or "starting nst"
                if "starting nstbrowser" in tail or "nstbrowser api failed" in tail or "opening existing" in tail:
                    return True
        except Exception:
            continue
    return False

def start_worker(name: str, argv: list[str], env: dict, extra: dict | None = None) -> int:
    e = dict(env)
    if extra:
        e.update(extra)
    WDIR.mkdir(parents=True, exist_ok=True)
    logf = open(WDIR / f"{name}_worker.log", "a")
    p = subprocess.Popen(argv, cwd=str(ROOT), env=e, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    write_pid(name, p.pid)
    log(f"started {name} pid={p.pid}")
    return p.pid

def active_by_portal(q) -> dict[str, int]:
    out = {}
    for row in q.jobs.aggregate([
        {"$match": {"status": {"$in": ["queued", "retry", "leased"]}}},
        {"$group": {"_id": "$portal", "c": {"$sum": 1}}},
    ]):
        out[row["_id"]] = row["c"]
    return out

def main():
    global _stop
    signal.signal(signal.SIGTERM, lambda *_: globals().update(_stop=True))
    signal.signal(signal.SIGINT, lambda *_: globals().update(_stop=True))
    env = load_env()
    key = env["NSTBROWSER_API_KEY"]
    slot = str(env.get("NSTBROWSER_ACTIVE_SLOT") or "1")
    log(f"full-queue monitor start (local agent slot{slot}, NST serialized, ATS parallel)")
    while not _stop:
        ok = agent_ok(key)
        from core.job_queue import JobQueue
        q = JobQueue()
        try:
            q.release_expired()
        except Exception:
            pass
        by = active_by_portal(q) if ok or True else {}
        counts = q.counts()
        pl = plan_limit()

        # ATS always if work remains (all ATS portals claimable)
        ats_work = sum(by.get(p, 0) for p in ATS_PORTALS)
        if ats_work > 0 and not worker_alive("ats"):
            ats_argv = [sys.executable, "-u", "scripts/application_worker.py"]
            for p in ATS_PORTALS:
                ats_argv.extend(["--portal", p])
            ats_argv.extend(["--poll-seconds", "20"])
            start_worker(
                "ats",
                ats_argv,
                env,
                {"ATS_HEADLESS": "1", "BROWSER_VENDOR": "playwright"},
            )
        if ats_work == 0 and worker_alive("ats"):
            # leave running to poll; optional stop
            pass

        # Pick first NST portal with work; stop others
        nst_choice = None
        if ok and not pl:
            for p in NST_ORDER:
                if by.get(p, 0) > 0:
                    nst_choice = p
                    break
        for p in NST_ORDER:
            if p != nst_choice and worker_alive(p):
                log(f"stopping NST worker {p} (active portal={nst_choice})")
                stop_worker(p)
        if nst_choice and not worker_alive(nst_choice):
            extra = {
                "KEEP_BROWSER": "1",
                "NSTBROWSER_KEEP_ALIVE": "1",
                "NSTBROWSER_ACTIVE_SLOT": slot,
                "BROWSER_VENDOR": "nstbrowser",
            }
            argv = [sys.executable, "-u", "scripts/application_worker.py",
                    "--portal", nst_choice, "--keep-browser", "--poll-seconds", "25"]
            # workopolis: IT only. indeed: both it + general (no --profile filter).
            # glassdoor/linkedin: any profile the queue has.
            if nst_choice == "workopolis":
                argv.extend(["--profile", "it"])
            start_worker(nst_choice, argv, env, extra)
        if pl:
            log("plan-limit detected — not starting NST workers")
            for p in NST_ORDER:
                if worker_alive(p):
                    # leave current job finish; don't restart after death
                    pass

        workers = {n: (read_pid(n) if worker_alive(n) else None) for n in ["ats"] + NST_ORDER}
        status = {
            "tick": datetime.now(timezone.utc).isoformat(),
            "agent_ok": ok,
            "plan_limit": pl,
            "active_by_portal": by,
            "counts": counts,
            "workers": workers,
            "nst_active": nst_choice,
            "mode": f"full_queue_local_slot{slot}",
        }
        STATUS.write_text(json.dumps(status, indent=2, default=str))
        log(f"HEALTH agent={ok} nst={nst_choice} plan={pl} by={by} counts={counts} workers={workers}")
        if not ok:
            log("Open Nstbrowser.app — agent not on :8848")
        for _ in range(45):
            if _stop:
                break
            time.sleep(1)
    log("monitor exit")

if __name__ == "__main__":
    raise SystemExit(main())
