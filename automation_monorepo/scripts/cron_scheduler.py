"""Background Python scheduler for Job Automation Bots.

Zero-dependency scheduler that polls the system clock and triggers daily orchestrator phases
at configured times. Can be run as a daemon or managed by systemd.
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

# Add project root to path
base_dir = Path(__file__).resolve().parent.parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from core.secret_manager import get_secret
from core.alerts import send_telegram_alert


def run_orchestrator_stage(stage: str, extra_args: list[str] | None = None) -> bool:
    """Invoke the orchestrator for a specific stage using subprocess."""
    python_exe = sys.executable
    orchestrator_script = base_dir / "orchestrator.py"
    
    cmd = [str(python_exe), str(orchestrator_script), "--stage", stage]
    if extra_args:
        cmd.extend(extra_args)
        
    print(f"\n[Scheduler] [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Executing: {' '.join(cmd)}")
    
    try:
        # Run with current environment
        res = subprocess.run(cmd, cwd=str(base_dir), env=os.environ.copy())
        print(f"[Scheduler] Stage '{stage}' exited with code {res.returncode}")
        return res.returncode == 0
    except Exception as e:
        print(f"[Scheduler] Failed to execute orchestrator stage '{stage}': {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Zero-dependency Background Python Scheduler")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scheduled times and trace execution without running subprocesses."
    )
    args = ap.parse_args()

    # Retrieve scheduling times from environment or defaults
    start_time = get_secret("SCHED_START_TIME", "06:00")
    workers = int(get_secret("SCHED_APPLY_WORKERS", "2"))

    print("=======================================")
    print(" Jobbots Background Scheduler Daemon")
    print("=======================================")
    print(f"Daily Start Time (Local): {start_time}")
    print(f"Apply Workers:            {workers}")
    print(f"Dry-run mode:             {args.dry_run}")
    print("=======================================")

    send_telegram_alert(
        f"📅 Jobbots background scheduler daemon started (Start Time: {start_time}, Dry-run: {args.dry_run}).",
        bot_name="system",
        alert_type="scheduler_started",
        force=True
    )

    # Tracks if the daily cycle ran today
    last_run_date = ""
    run_today = False

    try:
        while True:
            now = datetime.now()
            current_date = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            # Reset daily track on date change
            if current_date != last_run_date:
                last_run_date = current_date
                run_today = False
                print(f"\n[Scheduler] Date changed to {current_date}. Resetting run logs.")

            # Trigger the complete cycle at the scheduled start time
            if current_time == start_time and not run_today:
                run_today = True
                if args.dry_run:
                    print(f"[Scheduler] [DRY RUN] Would execute complete orchestrator --auto cycle at {current_time}")
                else:
                    print(f"[Scheduler] Daily start time {current_time} reached. Launching auto cycle...")
                    run_orchestrator_stage("all", ["--auto", "--workers", str(workers)])

            # Check every 30 seconds
            time.sleep(30)

    except KeyboardInterrupt:
        print("\n[Scheduler] Daemon terminated by user.")
    except Exception as e:
        print(f"[Scheduler] Daemon crashed with exception: {e}")
        send_telegram_alert(f"🚨 Background scheduler daemon crashed: {e}", bot_name="system", alert_type="scheduler_crashed", force=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
