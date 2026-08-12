from __future__ import annotations

"""
Multi-bot supervisor: launches each bot subprocess with env for profile, CDP port,
and unattended flags (SKIP_USER_START, AUTONOMOUS_SUPERVISOR). Indeed and Glassdoor
use **manual login** (or saved Chrome profile cookies); see ``PORTAL_MANUAL_LOGIN_TIMEOUT_MINUTES``.
LinkedIn may still use LINKEDIN_EMAIL / LINKEDIN_PASSWORD for an automated first step.

After each bot’s login check, ``data/supervisor/session_registry.json`` is updated; the
supervisor prints this summary so you can see which bots last reported ``session_ok``.

Bot list, CDP ports, and profile directories: ``core/supervised_bots.py``.

Optional environment (inherit when starting supervisor):
  PORTAL_MANUAL_LOGIN_TIMEOUT_MINUTES  minutes to wait for manual Indeed/Glassdoor/LinkedIn login (default 15)
  INDEED_BASE_URL          e.g. https://ca.indeed.com  (default https://www.indeed.com)
  GLASSDOOR_BASE_URL       e.g. https://www.glassdoor.ca
  IMAP_OTP_MAX_WAIT_SECONDS  legacy / unused for Indeed-Glassdoor login (still passed if set)
  AUTOMATION_PYTHON        explicit python path for bot subprocesses (otherwise uses
                           VIRTUAL_ENV or <repo>/.venv when present, so Indeed/Glassdoor
                           get playwright/bs4 even if you start the supervisor with system python)
"""

import argparse
import os
import sys
import time
import signal
import subprocess
from pathlib import Path
from typing import Optional

# Reconfigure stdout/stderr to UTF-8 so Unicode chars (✓ ✗ etc.) don't crash
# on Windows cp1252 console (especially when piped through Tee-Object/PowerShell).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Per-bot cycle runtime cap in seconds. 0 / unset = unlimited (no kill).
try:
    _max_rt = int(os.environ.get("BOT_MAX_RUNTIME_SECONDS", "0") or 0)
except ValueError:
    _max_rt = 0
MAX_BOT_RUNTIME: Optional[int] = _max_rt if _max_rt > 0 else None

# Add project root to sys.path so we can import from core
base_dir = Path(__file__).resolve().parent
sys.path.append(str(base_dir))

def consolidate_training_data() -> None:
    """Automatically run prepare_training_data scripts to consolidate Q/A pairs on VM."""
    try:
        print("\n[Supervisor] Automatically consolidating training data...")
        root_dir = base_dir.parent
        
        indeed_script = root_dir / "prepare_training_data.py"
        if indeed_script.exists():
            print(f"[Supervisor] Running {indeed_script.name}...")
            subprocess.run([sys.executable, str(indeed_script)], cwd=str(root_dir))
            
    except Exception as e:
        print(f"[Supervisor] Failed to auto-consolidate training data: {e}")

from core.supervisor_chrome import (
    daily_limit_flag_present,
    kill_bot_chromes,
    kill_subprocess_tree,
)
from core.supervisor_runtime import (
    build_subprocess_env as _build_subprocess_env,
    merge_dotenv_into_env,
    resolve_bot_python,
)

merge_dotenv_into_env(os.environ, base_dir / ".env", override=False)

from core.llm_backend.db import MongoStore
from core.session_registry import format_registry_summary, load_session_registry
from core.supervised_bots import supervised_bot_configs
from core.sentry_init import init_sentry
init_sentry("supervisor")

# Default MongoDB URI (adjust if your MongoDB runs elsewhere)
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")

# Central system database for supervisor and orchestration
SYSTEM_DB_NAME = os.environ.get("JOBBOTS_MONGO_DATABASE", "jobbots")

# Ports, profile paths, and scripts: single definition in core/supervised_bots.py
BOT_CONFIGS = supervised_bot_configs(base_dir)


def build_subprocess_env(cfg: dict, run_id: str) -> dict:
    """Environment for bot subprocesses (re-export with monorepo base_dir)."""
    return _build_subprocess_env(cfg, run_id, base_dir)


def initialize_system_database():
    """Initialize the central system database for supervisor and orchestration."""
    try:
        system_db = MongoStore(
            bot_id="system_supervisor",
            uri=MONGO_URI,
            database=SYSTEM_DB_NAME,
            fallback_dir=base_dir / "data" / "system_db_fallback"
        )
        
        if system_db.connected:
            print(f"[Supervisor] ✓ System database connected: {SYSTEM_DB_NAME}")
        else:
            print(f"[Supervisor] ⚠ System database not available, using fallback")
            
        return system_db
    except Exception as e:
        print(f"[Supervisor] ✗ Failed to initialize system database: {e}")
        return None


def run_bot(cfg) -> int:
    """
    Launch a single bot as a subprocess and log its lifecycle to MongoDB.
    """
    bot_name = cfg["bot_name"]
    print(f"\n[Supervisor] Preparing to launch: {bot_name}")
    # Kill any orphan Chrome processes from a prior crashed run before spawning
    kill_bot_chromes(cfg.get("profile_dir", ""), cfg.get("cdp_port"))
    prev = load_session_registry().get(bot_name)
    if prev:
        print(f"[Supervisor] session_registry last state: {prev}")
    else:
        print("[Supervisor] session_registry: no prior entry (first run or cleared).")

    # Initialize per-bot MongoStore
    db = MongoStore(
        bot_id=bot_name,
        uri=MONGO_URI,
        database=SYSTEM_DB_NAME,
        fallback_dir=base_dir / "data" / "db_fallback"
    )
    
    if not db.connected:
        print(f"[Supervisor] Warning: MongoDB not connected for {bot_name}. Falling back to local JSONL logs.")
        
    # Start DB Run
    run_id = db.start_run(mode="autonomous", label=f"supervisor_auto_run")
    print(f"[Supervisor] DB Run started for {bot_name} (Run ID: {run_id})")

    env = build_subprocess_env(cfg, run_id)

    script_path = base_dir / "bots" / cfg["script"]

    print(f"[Supervisor] Executing {bot_name} subprocess (streaming output)...")
    print(f"[Supervisor] -------- {bot_name} OUTPUT BEGIN --------")

    start_time = time.time()
    python_exe = resolve_bot_python(base_dir)

    popen_kwargs = dict(env=env, cwd=str(base_dir))
    # stdout/stderr inherit from supervisor — output streams live and is also
    # captured by Tee-Object → supervisor.log when launched via trial_run.ps1.
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    p = subprocess.Popen([str(python_exe), "-u", str(script_path)], **popen_kwargs)

    from core.health_controller import record_bot_start
    try:
        record_bot_start(bot_name, p.pid)
    except Exception:
        pass

    if MAX_BOT_RUNTIME is None:
        p.wait()
    else:
        try:
            p.wait(timeout=MAX_BOT_RUNTIME)
        except subprocess.TimeoutExpired:
            print(f"\n[Supervisor] ⚠ {bot_name} exceeded max runtime of {MAX_BOT_RUNTIME}s! Terminating.")
            try:
                if os.name == "nt":
                    p.terminate()
                else:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                p.wait(timeout=10)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    elapsed = time.time() - start_time
    exit_code = p.returncode

    status = "completed" if exit_code == 0 else "failed"
    error_msg = f"Exited with code {exit_code}" if exit_code != 0 else ""

    try:
        from core.datadog_metrics import gauge as _dd_gauge
        _dd_gauge("supervisor.run_duration_seconds", elapsed,
                  tags=[f"bot:{bot_name}", f"status:{status}"])
    except Exception:
        pass

    from core.health_controller import record_bot_exit
    try:
        record_bot_exit(bot_name, exit_code, error_msg)
    except Exception:
        pass

    db.end_run(run_id=run_id, status=status, error=error_msg)

    print(f"[Supervisor] -------- {bot_name} OUTPUT END --------")

    after = load_session_registry().get(bot_name)
    if after:
        print(f"[Supervisor] session_registry after run: {after}")

    print(f"[Supervisor] {bot_name} finished in {elapsed:.1f}s with status: {status}")
    return exit_code

def _close_ixbrowser_for_bot(cfg: dict) -> None:
    """Release IX Browser profile before a fresh spawn (avoids 'already open' races)."""
    try:
        from core.supervised_bots import _ixbrowser_profile_id_for
        from core.ixbrowser_util import close_ixbrowser_profile

        profile_id = _ixbrowser_profile_id_for(cfg["bot_name"])
        if profile_id and close_ixbrowser_profile(profile_id):
            print(f"[Supervisor] Closed IX Browser profile {profile_id} for {cfg['bot_name']}")
    except Exception as e:
        print(f"[Supervisor] IX Browser profile close (best-effort): {e}")


def _spawn_bot(cfg: dict, log_dir: Path, python_exe: str) -> tuple:
    """Spawn one bot subprocess. Returns (Popen, bot_name, log_file_handle, start_time)."""
    bot_name = cfg["bot_name"]
    # Kill orphan Chromes from prior crashed run for this bot
    kill_bot_chromes(cfg.get("profile_dir", ""), cfg.get("cdp_port"))
    _close_ixbrowser_for_bot(cfg)
    run_id = f"parallel_{int(time.time())}"
    env = build_subprocess_env(cfg, run_id)
    script_path = base_dir / "bots" / cfg["script"]
    # Append-mode so restarts don't truncate prior output
    log_file = open(log_dir / f"{bot_name}.log", "a")
    log_file.write(f"\n\n========== Spawn @ {time.strftime('%Y-%m-%d %H:%M:%S')} ==========\n")
    log_file.flush()
    popen_kwargs = dict(env=env, stdout=log_file, stderr=subprocess.STDOUT, cwd=str(base_dir))
    if os.name == "nt":
        # On Windows, start_new_session is not supported; CREATE_NEW_PROCESS_GROUP
        # lets us send Ctrl-Break later if needed.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    p = subprocess.Popen([str(python_exe), "-u", str(script_path)], **popen_kwargs)
    from core.health_controller import record_bot_start
    try:
        record_bot_start(bot_name, p.pid)
    except Exception:
        pass
    return (p, bot_name, log_file, time.time())


def _run_parallel(bots: list[dict], auto_restart: bool = True,
                  restart_cooldown: int = 15,
                  failed_bots: list[str] | None = None) -> None:
    """
    Launch bots as parallel subprocesses.
    If auto_restart=True (default), respawn any bot that exits — successful or
    crashed — after `restart_cooldown` seconds. Loops until Ctrl+C.
    If auto_restart=False, runs bots once and waits for all to finish.
    """
    if failed_bots is None:
        failed_bots = []

    log_dir = base_dir / "logs" / "supervisor"
    log_dir.mkdir(parents=True, exist_ok=True)
    python_exe = resolve_bot_python(base_dir)

    print(f"[Supervisor] Parallel mode: launching {len(bots)} bots simultaneously.")
    if auto_restart:
        print(f"[Supervisor] Auto-restart ON (cooldown {restart_cooldown}s after each exit).")

    # bot_name -> dict(cfg, proc tuple, restart_count, last_exit_at)
    state: dict[str, dict] = {}
    from core.health_controller import is_bot_allowed_to_start
    for cfg in bots:
        allowed, reason = is_bot_allowed_to_start(cfg["bot_name"])
        if not allowed:
            print(f"[Supervisor] Bot {cfg['bot_name']} is blocked from starting: {reason}")
            failed_bots.append(cfg["bot_name"])
            continue
        proc = _spawn_bot(cfg, log_dir, python_exe)
        state[cfg["bot_name"]] = {
            "cfg": cfg, "proc": proc, "restarts": 0, "last_exit": None,
        }
        print(f"[Supervisor] Started {cfg['bot_name']} (pid {proc[0].pid})")
        time.sleep(2)

    print(f"[Supervisor] All {len(state)} bots running. Logs: logs/supervisor/<bot>.log")
    try:
        while state:
            for bot_name, info in list(state.items()):
                p, name, lf, start = info["proc"]
                ret = p.poll()
                # Hard timeout: terminate runaways (skipped when unlimited)
                if (
                    MAX_BOT_RUNTIME is not None
                    and ret is None
                    and time.time() - start > MAX_BOT_RUNTIME
                ):
                    print(f"[Supervisor] ⚠ {name} exceeded {MAX_BOT_RUNTIME}s! Terminating.")
                    kill_subprocess_tree(p)
                    ret = p.poll()
                if ret is not None:
                    try:
                        p.wait(timeout=20)
                    except Exception:
                        pass
                    _close_ixbrowser_for_bot(info["cfg"])
                    elapsed = time.time() - start
                    status = "completed" if ret == 0 else "failed"
                    print(f"[Supervisor] {name} exited in {elapsed:.1f}s — {status} (code {ret})")
                    if ret != 0:
                        failed_bots.append(name)
                    try:
                        from core.datadog_metrics import gauge as _dd_gauge
                        _dd_gauge("supervisor.run_duration_seconds", elapsed,
                                  tags=[f"bot:{name}", f"status:{status}"])
                    except Exception:
                        pass
                    try:
                        lf.close()
                    except Exception:
                        pass
                    # Per-bot daily-limit flag (LinkedIn writes this when it
                    # detects "You've reached today's Easy Apply limit").
                    # The bot's `logs_folder_path` is relative to its master
                    # cwd, so the flag normally lands in
                    # ``<master_dir>/logs/<bot_name>/daily_limit_reached.flag``.
                    # Look in the supervisor-side dir AND any matching
                    # location under ../master/*/logs/<bot>/, so tooling that
                    # writes either way is honored.
                    if daily_limit_flag_present(name, base_dir):
                        print(f"[Supervisor] {name} hit daily Easy Apply limit. "
                              "Will not respawn until tomorrow.")
                        del state[bot_name]
                        continue
                    if auto_restart:
                        info["restarts"] += 1
                        info["last_exit"] = time.time()
                        from core.health_controller import record_bot_exit, is_bot_allowed_to_start
                        try:
                            record_bot_exit(name, ret, f"Exited with code {ret}")
                        except Exception:
                            pass
                        
                        allowed, reason = is_bot_allowed_to_start(name)
                        if not allowed:
                            print(f"[Supervisor] Bot {name} cannot restart: {reason}")
                            del state[bot_name]
                            continue
                            
                        print(f"[Supervisor] {name} restarting in {restart_cooldown}s "
                              f"(restart #{info['restarts']})...")
                        time.sleep(restart_cooldown)
                        info["proc"] = _spawn_bot(info["cfg"], log_dir, python_exe)
                        print(f"[Supervisor] {name} respawned (pid {info['proc'][0].pid})")
                    else:
                        del state[bot_name]
            if state:
                time.sleep(5)
    except KeyboardInterrupt:
        print("\n[Supervisor] Stopping all bots...")
        for info in state.values():
            p, _, lf, _ = info["proc"]
            kill_subprocess_tree(p)
            try:
                lf.close()
            except Exception:
                pass

    # Print restart summary
    if auto_restart and state:
        print("\n[Supervisor] === Restart summary ===")
        for name, info in state.items():
            print(f"   {name}: {info['restarts']} restart(s)")


def _run_group_parallel_once(bots: list[dict], failed_bots: list[str]) -> None:
    if not bots:
        return
    log_dir = base_dir / "logs" / "supervisor"
    log_dir.mkdir(parents=True, exist_ok=True)
    python_exe = resolve_bot_python(base_dir)

    print(f"\n[Supervisor] Starting parallel group ({len(bots)} bots): {', '.join(b['bot_name'] for b in bots)}")
    active = []
    for cfg in bots:
        try:
            proc_info = _spawn_bot(cfg, log_dir, python_exe)
            active.append({"cfg": cfg, "proc": proc_info[0], "name": proc_info[1], "log_file": proc_info[2], "start_time": proc_info[3]})
            print(f"[Supervisor]   Started {cfg['bot_name']} (pid {proc_info[0].pid})")
        except Exception as e:
            print(f"[Supervisor]   Failed to spawn {cfg['bot_name']}: {e}")
            failed_bots.append(cfg["bot_name"])
        time.sleep(2)

    print(f"[Supervisor] Waiting for group completion...")
    while active:
        for info in list(active):
            p = info["proc"]
            name = info["name"]
            lf = info["log_file"]
            start = info["start_time"]
            ret = p.poll()
            
            # Hard timeout check
            if MAX_BOT_RUNTIME is not None and ret is None and time.time() - start > MAX_BOT_RUNTIME:
                print(f"[Supervisor] ⚠ {name} exceeded {MAX_BOT_RUNTIME}s! Terminating.")
                kill_subprocess_tree(p)
                ret = p.poll()
                
            if ret is not None:
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
                _close_ixbrowser_for_bot(info["cfg"])
                elapsed = time.time() - start
                status = "completed" if ret == 0 else "failed"
                print(f"[Supervisor]   {name} exited in {elapsed:.1f}s — {status} (code {ret})")
                if ret != 0:
                    failed_bots.append(name)
                try:
                    lf.close()
                except Exception:
                    pass
                active.remove(info)
        time.sleep(2)


def _run_backup() -> None:
    print("\n[Supervisor] Running S3 backup (MongoDB, logs, cookies)...")
    try:
        if os.name == "nt":
            script_path = Path(__file__).resolve().parent / "scripts" / "backup_s3.ps1"
            if script_path.exists():
                print(f"[Supervisor] Executing backup script: {script_path}")
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)], check=True)
                print("[Supervisor] Backup completed successfully.")
            else:
                print(f"[Supervisor] Error: Backup script not found at {script_path}")
        else:
            print("[Supervisor] Backup to S3 not implemented for non-Windows guest OS")
    except Exception as e:
        print(f"[Supervisor] S3 backup failed: {e}")

def _trigger_shutdown() -> None:
    _run_backup()
    print("\n[Supervisor] Triggering guest OS shutdown in 60 seconds to stop billing...")
    if os.name == "nt":
        os.system('shutdown /s /t 60 /c "Supervisor complete. Shutting down VM to stop billing."')
    else:
        os.system('sudo shutdown -h +1 "Supervisor complete. Shutting down VM to stop billing."')

def main():
    ap = argparse.ArgumentParser(description="Multi-Bot Autonomous Supervisor")
    ap.add_argument(
        "--only", type=str, default="",
        help="Comma-separated bot names (e.g. indeed_it,linkedin_general). Default: all ready bots.",
    )
    ap.add_argument(
        "--shutdown", action="store_true",
        help="Shutdown the Windows/Linux VM after completing the run.",
    )
    ap.add_argument(
        "--portal", type=str, default=None,
        help="Portal filter: single name or comma list (e.g. indeed,glassdoor).",
    )
    ap.add_argument(
        "--profile", type=str, default=None,
        choices=["it", "general"],
        help="Run only IT or General bots.",
    )
    ap.add_argument(
        "--keyword", type=str, default=None,
        help="Test override for search keyword/term.",
    )
    ap.add_argument(
        "--parallel", action="store_true",
        help="Run selected bots in parallel (all at once) instead of sequential.",
    )
    ap.add_argument(
        "--stage", choices=["discover", "apply"], default=os.getenv("JOBBOT_MODE", "apply"),
        help="Pipeline stage. discover searches/screens/enqueues without submitting; apply runs application flows.",
    )
    ap.add_argument("--workers", type=int, default=1,
                    help="Number of parallel queue consumers for --stage apply.")
    ap.add_argument(
        "--once", action="store_true",
        help="Run one cycle only, then exit (no continuous loop).",
    )
    ap.add_argument(
        "--include-not-ok", action="store_true",
        help="Also run bots whose session_registry shows session_ok=false (retry on each cycle).",
    )
    ap.add_argument(
        "--list", action="store_true",
        help="Print every supervised bot, its CDP port, profile dir and session status, then exit.",
    )
    ap.add_argument(
        "--backup", action="store_true",
        help="Run S3 backup of database, logs, and profiles, then exit.",
    )
    args = ap.parse_args()
    os.environ["JOBBOT_MODE"] = args.stage

    # ── New dual-engine discovery routing ─────────────────────────────────
    # Feature flag: DISCOVERY_ENGINE = legacy (default) | new | shadow
    _discovery_engine = os.environ.get("DISCOVERY_ENGINE", "legacy").strip().lower()

    if args.stage == "discover" and _discovery_engine in ("new", "shadow") and not args.list and not args.backup:
        print(f"[Supervisor] Discovery engine: {_discovery_engine}")

        portals_filter = None
        if args.portal:
            portals_filter = [p.strip().lower() for p in str(args.portal).split(",") if p.strip()]
        profile_filter = args.profile or "it"
        dry_run = (_discovery_engine == "shadow")

        try:
            from core.discovery import run_discovery

            result = run_discovery(
                profile=profile_filter,
                portals=portals_filter,
                dry_run=dry_run,
                search_terms=[t.strip() for t in args.keyword.split(",") if t.strip()] if args.keyword else None,
            )
            print(f"[Supervisor] Discovery complete: {result}")

            if _discovery_engine == "shadow":
                print("[Supervisor] Shadow mode — new engine results logged but NOT enqueued.")
                print("[Supervisor] Falling through to legacy discovery for actual enqueue...")
                # Fall through to legacy discover path below
            else:
                # DISCOVERY_ENGINE=new — done, no legacy path
                if args.shutdown:
                    _trigger_shutdown()
                return
        except Exception as exc:
            print(f"[Supervisor] New discovery engine failed: {exc}")
            if _discovery_engine == "new":
                # No fallback when explicitly set to 'new'
                if args.shutdown:
                    _trigger_shutdown()
                raise SystemExit(1)
            # Shadow mode: continue to legacy on failure
            print("[Supervisor] Continuing with legacy discovery...")

    if args.stage == "apply" and not args.list and not args.backup:
        worker_script = base_dir / "scripts" / "application_worker.py"
        py = str(resolve_bot_python(base_dir))
        portals: list[str] = []
        if args.portal:
            portals = [p.strip().lower() for p in str(args.portal).split(",") if p.strip()]
        count = max(1, int(args.workers or 1))

        def _portal_shards(portal_list: list[str], n: int) -> list[list[str]]:
            """Shard portals so parallel workers rarely fight over the same NST profile.

            Preferred isolation (when present): indeed | linkedin | glassdoor |
            workopolis | google+greenhouse+lever.  With fewer than five workers,
            Glassdoor and Workopolis share one shard; with five they run in
            parallel on their separate NST profiles.
            """
            if n <= 1 or not portal_list:
                return [portal_list or []]
            # Always include ATS (Playwright, no NST) in the preferred list before
            # optional Glassdoor/Workopolis split. With workers=3 the old order
            # dropped ATS entirely (shards[:3] kept only browser portals).
            preferred = (
                [
                    ["indeed"],
                    ["linkedin"],
                    # ATS Playwright + Job Bank email (no NST) — keep early so
                    # low worker counts still apply Greenhouse/Lever.
                    ["google", "greenhouse", "lever", "ashby", "bamboohr", "jobbank"],
                    ["glassdoor"],
                    ["workopolis"],
                ]
                if n >= 5 else [
                    ["indeed"],
                    ["linkedin"],
                    ["google", "greenhouse", "lever", "ashby", "bamboohr", "jobbank"],
                    ["glassdoor", "workopolis"],
                ]
            )
            remaining = list(portal_list)
            shards: list[list[str]] = []
            for group in preferred:
                hit = [p for p in group if p in remaining]
                if hit:
                    shards.append(hit)
                    for p in hit:
                        remaining.remove(p)
            # Leftover portals (unknown names) attach to the last shard or own slots.
            for p in remaining:
                if len(shards) < n:
                    shards.append([p])
                else:
                    shards[-1].append(p)
            # If we have more slots than preferred groups, duplicate largest queues
            # are NOT created — extra workers share the busiest portal group so
            # claim() still serializes per-job; NST lease still protects profiles.
            while len(shards) < n and shards:
                # Extra worker clones the first (indeed) shard for throughput when
                # queue is deep; profile lease will serialize if both hit same bot.
                shards.append(list(shards[0]))
            return shards[:n] if shards else [portal_list]

        shards = _portal_shards(portals, count)
        print(
            f"[Supervisor] Starting {len(shards)} application queue worker(s) "
            f"(requested workers={count})."
        )
        processes = []
        for i, shard in enumerate(shards):
            worker_args = [py, str(worker_script)]
            for _p in shard:
                worker_args += ["--portal", _p]
            if args.profile:
                worker_args += ["--profile", args.profile]
            if args.once:
                worker_args.append("--once")
            label = ",".join(shard) if shard else "(all)"
            print(f"[Supervisor]   worker[{i}] portals={label}")
            processes.append(
                subprocess.Popen(worker_args, cwd=str(base_dir), env=os.environ.copy())
            )
        try:
            return_codes = [p.wait() for p in processes]
        except KeyboardInterrupt:
            for p in processes:
                kill_subprocess_tree(p)
            return
        if any(code != 0 for code in return_codes):
            raise SystemExit(max(return_codes))
        return

    if args.backup:
        _run_backup()
        return

    if args.list:
        reg = load_session_registry()
        print("Supervised bots:")
        for cfg in BOT_CONFIGS:
            name = cfg["bot_name"]
            entry = reg.get(name, {}) or {}
            ok = "ready" if entry.get("session_ok") else "NEEDS LOGIN"
            ts = (entry.get("updated_at") or "never")[:19]
            print(
                f"  {name:<22} portal={cfg['portal']:<10} port={cfg['cdp_port']:<5} "
                f"profile_dir={cfg['profile_dir']}  status={ok} ({ts})"
            )
        print(
            "\nManual single-bot test:\n"
            "  python supervisor.py --only <bot_name> --once\n"
            "Add --include-not-ok to bypass session_registry gating."
        )
        return

    failed_bots: list[str] = []

    print("=======================================")
    print(" Multi-Bot Autonomous Supervisor")
    print("=======================================")
    print("Running in headless autonomous mode.")
    print(f"[Supervisor] Pipeline stage: {args.stage}")
    _py = resolve_bot_python(base_dir)
    print(f"[Supervisor] Bot subprocess Python: {_py}")
    print(format_registry_summary())
    
    # Initialize central system database
    system_db = initialize_system_database()
    
    if system_db:
        # Start system-wide supervisor run
        supervisor_run_id = system_db.start_run(mode="supervisor", label="multi_bot_supervisor")
        print(f"[Supervisor] System run started: {supervisor_run_id}")
    
    # Check which bots have valid sessions before starting
    registry = load_session_registry()
    if args.include_not_ok:
        # Include ALL bots regardless of session_ok — they'll retry on each cycle
        ready_bots = list(BOT_CONFIGS)
        not_ok_names = [c["bot_name"] for c in BOT_CONFIGS if not registry.get(c["bot_name"], {}).get("session_ok")]
        if not_ok_names:
            print(f"\n[Supervisor] --include-not-ok: also running {', '.join(not_ok_names)} (session_ok=false)")
    else:
        ready_bots = [cfg for cfg in BOT_CONFIGS if registry.get(cfg["bot_name"], {}).get("session_ok")]
        not_ready = [cfg["bot_name"] for cfg in BOT_CONFIGS if cfg not in ready_bots]
        if not_ready:
            print(f"\n[Supervisor] ⚠ Skipping bots without login session: {', '.join(not_ready)}")
            print("[Supervisor]   Pass --include-not-ok to retry these bots anyway.")

    if not ready_bots:
        print("\n[Supervisor] ✗ No bots to run.")
        if args.shutdown:
            _trigger_shutdown()
        return

    # Filter by user selection
    selected = ready_bots
    if args.only.strip():
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        selected = [c for c in selected if c["bot_name"] in want]
    if args.portal:
        want_portals = {p.strip().lower() for p in str(args.portal).split(",") if p.strip()}
        selected = [c for c in selected if c["portal"] in want_portals]
    if args.profile:
        selected = [c for c in selected if args.profile.lower() in c["profile"].lower()]

    if not selected:
        print("[Supervisor] ✗ No bots match your filters.")
        if args.shutdown:
            _trigger_shutdown()
        return

    print(f"[Supervisor] Launching {len(selected)}/{len(BOT_CONFIGS)} bots: {', '.join(c['bot_name'] for c in selected)}")

    if args.parallel:
        print(f"[Supervisor] Mode: Parallel Phase Groups (Indeed first, then Glassdoor/Workopolis)")
        
        indeed_bots = [c for c in selected if c["portal"] == "indeed"]
        g_w_bots = [c for c in selected if c["portal"] in ("glassdoor", "workopolis")]
        other_bots = [c for c in selected if c["portal"] not in ("indeed", "glassdoor", "workopolis")]
        
        cycle = 1
        try:
            while True:
                print(f"\n[Supervisor] === Parallel Phase Cycle {cycle} ===")
                
                # Phase 1: Indeed
                if indeed_bots:
                    _run_group_parallel_once(indeed_bots, failed_bots)
                    
                # Phase 2: Glassdoor & Workopolis
                if g_w_bots:
                    _run_group_parallel_once(g_w_bots, failed_bots)
                    
                # Phase 3: Other bots (if any)
                if other_bots:
                    _run_group_parallel_once(other_bots, failed_bots)
                    
                consolidate_training_data()
                
                if args.once:
                    print("[Supervisor] --once: Parallel cycle complete.")
                    break
                    
                print("[Supervisor] Cycle complete. Cooldown 60s before next parallel cycle...")
                time.sleep(60)
                cycle += 1
        except KeyboardInterrupt:
            print("\n[Supervisor] Interrupted parallel cycle — stopping.")
    else:
        print(f"[Supervisor] Mode: sequential")
        # Continuous autonomous loop
        cycle = 1
        while True:
            print(f"\n[Supervisor] === Starting Global Cycle {cycle} ===")
            for cfg in selected:
                from core.health_controller import is_bot_allowed_to_start
                allowed, reason = is_bot_allowed_to_start(cfg["bot_name"])
                if not allowed:
                    print(f"[Supervisor] Skipping bot {cfg['bot_name']}: {reason}")
                    continue
                try:
                    if run_bot(cfg) != 0:
                        failed_bots.append(cfg["bot_name"])
                except Exception as e:
                    failed_bots.append(cfg["bot_name"])
                    print(f"[Supervisor] {cfg['bot_name']} run_bot raised: {e} — continuing to next bot.")
                time.sleep(5) # Cooldown between bot runs
            consolidate_training_data()
            if args.once:
                print("[Supervisor] Single cycle complete (--once). Exiting.")
                break
            print("[Supervisor] Cycle complete. Sleeping 5 minutes before next global cycle...")
            time.sleep(300)
            cycle += 1
        
    if system_db:
        # End system-wide supervisor run
        system_db.end_run(run_id=supervisor_run_id, status="completed", error="")
        print(f"[Supervisor] System run completed: {supervisor_run_id}")

    if args.shutdown:
        _trigger_shutdown()

    if failed_bots:
        print(f"[Supervisor] Failed bots: {', '.join(sorted(set(failed_bots)))}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
