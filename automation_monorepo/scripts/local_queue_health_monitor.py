#!/usr/bin/env python3
"""Keep local Mac apply farm healthy while draining the Mongo queue.

Watches:
  * NSTbrowser Docker container (jobbots-nstbrowser) + Local API
  * application workers (linkedin / ats / indeed / workopolis)
  * Mongo job status counts

Restarts dead pieces. Prints one compact status line per tick.
Exit codes are only for fatal setup errors; normal operation loops forever
until SIGTERM/SIGINT.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs" / "monitor"
WORKER_DIR = ROOT / "logs" / "workers"
STATUS_PATH = LOG_DIR / "health_status.json"
TICK_SECONDS = int(os.environ.get("HEALTH_MONITOR_TICK", "45"))
NST_CONTAINER = os.environ.get("NST_CONTAINER_NAME", "jobbots-nstbrowser")
NST_IMAGE = os.environ.get("NST_IMAGE", "nstbrowser/browserless:latest")
NST_DATA = Path(os.environ.get("NST_DATADIR", str(ROOT.parent / "data" / "nstbrowser")))
NST_HOST = os.environ.get("NSTBROWSER_API_HOST", "127.0.0.1")
NST_PORT = os.environ.get("NSTBROWSER_API_PORT", "8848")

# name -> start argv (relative to ROOT) + env overlays
WORKER_SPECS = {
    "ats": {
        "argv": [
            sys.executable, "-u", "scripts/application_worker.py",
            "--portal", "greenhouse", "--portal", "lever",
            "--poll-seconds", "20",
        ],
        "env": {},
        "log": "ats_worker.log",
        "pid": "ats_worker.pid",
    },
    "linkedin": {
        "argv": [
            sys.executable, "-u", "scripts/application_worker.py",
            "--portal", "linkedin", "--keep-browser",
            "--poll-seconds", "20",
        ],
        "env": {
            "NSTBROWSER_ACTIVE_SLOT": "1",
            "BROWSER_VENDOR": "nstbrowser",
            "KEEP_BROWSER": "1",
        },
        "log": "linkedin_worker.log",
        "pid": "linkedin_worker.pid",
    },
    "indeed": {
        "argv": [
            sys.executable, "-u", "scripts/application_worker.py",
            "--portal", "indeed", "--profile", "it",
            "--poll-seconds", "30",
        ],
        "env": {
            "NSTBROWSER_ACTIVE_SLOT": "1",
            "BROWSER_VENDOR": "nstbrowser",
        },
        "log": "indeed_worker.log",
        "pid": "indeed_worker.pid",
    },
    "workopolis": {
        "argv": [
            sys.executable, "-u", "scripts/application_worker.py",
            "--portal", "workopolis", "--profile", "it",
            "--poll-seconds", "30",
        ],
        "env": {
            "NSTBROWSER_ACTIVE_SLOT": "1",
            "BROWSER_VENDOR": "nstbrowser",
        },
        "log": "workopolis_worker.log",
        "pid": "workopolis_worker.pid",
    },
}

_stop = False


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "monitor.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _load_dotenv() -> dict[str, str]:
    env = dict(os.environ)
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return env
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        # Prefer process env for already-set; fill missing from .env
        env.setdefault(k, v)
    # Force slot 1 for this local farm
    env["NSTBROWSER_ACTIVE_SLOT"] = "1"
    env["BROWSER_VENDOR"] = env.get("BROWSER_VENDOR") or "nstbrowser"
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def nst_api_ok(api_key: str) -> tuple[bool, str]:
    if not api_key:
        return False, "missing_api_key"
    url = f"http://{NST_HOST}:{NST_PORT}/api/v2/browsers"
    req = urllib.request.Request(url, headers={"x-api-key": api_key}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            code = body.get("code")
            if code in (0, 200):
                return True, f"code={code}"
            return False, f"code={code} msg={body.get('msg')}"
    except Exception as exc:
        return False, str(exc)[:160]


def docker_container_running(name: str) -> bool:
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
        return out.lower() == "true"
    except Exception:
        return False


def ensure_nst(api_key: str) -> dict:
    info = {"container": False, "api": False, "action": "ok", "detail": ""}
    info["container"] = docker_container_running(NST_CONTAINER)
    ok, detail = nst_api_ok(api_key)
    info["api"] = ok
    info["detail"] = detail
    if info["container"] and ok:
        return info

    # Restart / recreate container with slot-1 token
    info["action"] = "restart"
    _log(f"NST unhealthy (container={info['container']} api={ok} detail={detail}); restarting…")
    NST_DATA.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "rm", "-f", NST_CONTAINER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = [
        "docker", "run", "-d",
        "--name", NST_CONTAINER,
        "-p", f"127.0.0.1:{NST_PORT}:8848",
        "--restart", "unless-stopped",
        "--mount", f"type=bind,src={NST_DATA},dst=/data",
        "-e", f"TOKEN={api_key}",
        "-e", "DATADIR=/data",
        NST_IMAGE,
    ]
    try:
        subprocess.check_call(cmd, timeout=120)
    except Exception as exc:
        info["action"] = "restart_failed"
        info["detail"] = str(exc)[:200]
        _log(f"NST docker start failed: {exc}")
        return info

    # Wait for API
    for i in range(30):
        time.sleep(2)
        ok, detail = nst_api_ok(api_key)
        if ok:
            info["container"] = True
            info["api"] = True
            info["detail"] = detail
            info["action"] = "restarted"
            _log(f"NST API healthy after restart ({(i + 1) * 2}s)")
            return info
    info["container"] = docker_container_running(NST_CONTAINER)
    info["api"] = False
    info["detail"] = "api_not_ready_after_restart"
    _log("NST container up but API not ready yet")
    return info


def linkedin_log_shows_plan_limit() -> bool:
    """True if recent LinkedIn worker log indicates NST plan/quota block."""
    path = WORKER_DIR / "linkedin_worker.log"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[-8000:].lower()
    except Exception:
        return False
    return any(
        m in text
        for m in (
            "exceeded plan limits",
            "plan limit",
            "code=6001",
            '"code":6001',
            "code\": 6001",
        )
    )


def ensure_worker(name: str, base_env: dict) -> dict:
    spec = WORKER_SPECS[name]
    pid_path = WORKER_DIR / spec["pid"]
    log_path = WORKER_DIR / spec["log"]
    WORKER_DIR.mkdir(parents=True, exist_ok=True)
    pid = _read_pid(pid_path)
    if pid and _pid_alive(pid):
        return {"name": name, "pid": pid, "alive": True, "action": "ok"}

    # Start
    env = dict(base_env)
    env.update(spec["env"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            spec["argv"],
            cwd=str(ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as exc:
        log_fh.close()
        _log(f"worker {name} start failed: {exc}")
        return {"name": name, "pid": None, "alive": False, "action": "start_failed", "error": str(exc)}
    _write_pid(pid_path, proc.pid)
    _log(f"worker {name} started pid={proc.pid}")
    return {"name": name, "pid": proc.pid, "alive": True, "action": "started"}


def queue_snapshot() -> dict:
    try:
        from core.job_queue import JobQueue

        q = JobQueue()
        # reclaim expired leases each tick
        try:
            released = q.release_expired()
        except Exception:
            released = 0
        counts = q.counts()
        by_portal = {}
        for row in q.jobs.aggregate(
            [
                {"$match": {"status": {"$in": ["queued", "retry", "leased"]}}},
                {"$group": {"_id": {"s": "$status", "p": "$portal"}, "c": {"$sum": 1}}},
            ]
        ):
            s = row["_id"]["s"]
            p = row["_id"]["p"] or "?"
            by_portal.setdefault(p, {})[s] = row["c"]
        # recent terminals (last 15 min)
        cutoff = time.time() - 900
        recent = list(
            q.jobs.aggregate(
                [
                    {
                        "$match": {
                            "status": {
                                "$in": [
                                    "applied",
                                    "already_applied",
                                    "skipped",
                                    "bookmarked",
                                    "dead",
                                    "manual_review",
                                ]
                            },
                            "updated_at": {"$gte": datetime.fromtimestamp(cutoff, tz=timezone.utc)},
                        }
                    },
                    {"$group": {"_id": "$status", "c": {"$sum": 1}}},
                ]
            )
        )
        recent_map = {r["_id"]: r["c"] for r in recent}
        return {
            "counts": counts,
            "by_portal": by_portal,
            "released_expired": released,
            "recent_15m": recent_map,
            "ok": True,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def write_status(payload: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def handle_signal(signum, _frame):
    global _stop
    _stop = True
    _log(f"received signal {signum}; shutting down monitor")


def main() -> int:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_DIR.mkdir(parents=True, exist_ok=True)
    base_env = _load_dotenv()
    api_key = (base_env.get("NSTBROWSER_API_KEY") or "").strip()
    if not api_key:
        _log("FATAL: NSTBROWSER_API_KEY missing")
        return 2

    _log(
        f"monitor start tick={TICK_SECONDS}s slot=1 key={api_key[:8]}… "
        f"container={NST_CONTAINER} port={NST_PORT}"
    )
    prev_active = None
    # After plan-limit, back off LinkedIn restarts for this many seconds.
    linkedin_backoff_until = 0.0
    linkedin_backoff_sec = int(os.environ.get("LINKEDIN_PLAN_BACKOFF_SECONDS", "1800") or "1800")

    while not _stop:
        tick_at = datetime.now(timezone.utc).isoformat()
        nst = ensure_nst(api_key)
        plan_blocked = linkedin_log_shows_plan_limit() and time.time() < linkedin_backoff_until
        if linkedin_log_shows_plan_limit() and linkedin_backoff_until <= time.time():
            # Fresh observation of plan limit → start backoff window.
            linkedin_backoff_until = time.time() + linkedin_backoff_sec
            plan_blocked = True
            _log(f"LinkedIn NST plan-limit seen; backoff {linkedin_backoff_sec}s")
        nst["plan_blocked"] = plan_blocked
        nst["linkedin_backoff_until"] = linkedin_backoff_until

        workers = {}
        enabled = {
            x.strip()
            for x in (os.environ.get("MONITOR_WORKERS") or "ats,linkedin").split(",")
            if x.strip()
        }
        # Never run multiple NST portal workers at once on Mac Docker.
        if "linkedin" in enabled and plan_blocked:
            enabled.discard("linkedin")
        enabled.discard("indeed")
        enabled.discard("workopolis")
        for name in WORKER_SPECS:
            if name not in enabled:
                workers[name] = {
                    "name": name,
                    "pid": None,
                    "alive": False,
                    "action": "disabled",
                    "reason": "plan_backoff" if name == "linkedin" and plan_blocked else "disabled",
                }
                continue
            workers[name] = ensure_worker(name, base_env)
        qsnap = queue_snapshot()
        counts = qsnap.get("counts") or {}
        active = int(counts.get("queued") or 0) + int(counts.get("retry") or 0) + int(counts.get("leased") or 0)
        terminal_recent = qsnap.get("recent_15m") or {}

        status = {
            "tick_at": tick_at,
            "nst": nst,
            "workers": workers,
            "queue": qsnap,
            "active_work": active,
        }
        write_status(status)

        worker_bits = " ".join(
            f"{n}={'up' if w.get('alive') else 'DOWN'}"
            + (f"({w.get('action')})" if w.get("action") not in (None, "ok") else "")
            for n, w in workers.items()
        )
        portal_bits = " ".join(
            f"{p}:" + ",".join(f"{s}={c}" for s, c in sorted(st.items()))
            for p, st in sorted((qsnap.get("by_portal") or {}).items())
        )
        recent_bits = ",".join(f"{k}={v}" for k, v in sorted(terminal_recent.items())) or "none"
        delta = "" if prev_active is None else f" d_active={active - prev_active:+d}"
        prev_active = active
        _log(
            f"HEALTH nst_api={nst.get('api')} plan_blocked={plan_blocked} nst_action={nst.get('action')} "
            f"active={active} counts={counts}{delta} recent15m=[{recent_bits}] "
            f"workers=[{worker_bits}] portals=[{portal_bits}]"
        )

        # Stall hint: active work not dropping and no recent terminals
        if active > 0 and not terminal_recent and prev_active == active:
            # only soft-log; restarts already handled above
            pass

        for _ in range(TICK_SECONDS):
            if _stop:
                break
            time.sleep(1)

    _log("monitor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
