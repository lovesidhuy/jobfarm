"""Unified Daily Orchestrator for Job Automation Bots.

Coordinates the daily lifecycle phases: pre-flight health checks, discovery,
application worker execution, spreadsheet reporting, S3 backup, and cost-saving VM shutdown.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import subprocess
from pathlib import Path

# Project root setup
base_dir = Path(__file__).resolve().parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from core.secret_manager import get_secret
from core.alerts import send_telegram_alert
from core.supervised_bots import supervised_bot_configs
from core.supervisor_runtime import merge_dotenv_into_env, resolve_bot_python

# Load environment
merge_dotenv_into_env(os.environ, base_dir / ".env", override=False)


class DailyOrchestrator:
    def __init__(self, workers: int = 1, once: bool = False):
        self.workers = workers
        self.once = once
        self.python_exe = resolve_bot_python(base_dir)
        self.supervisor_script = base_dir / "supervisor.py"

    def run_preflight_checks(self) -> dict:
        """Phase 1: Pre-flight checks on DB, API, and portal sessions."""
        print("\n=== Phase 1: Pre-flight Health Checks ===")
        from core.session_check import run_preflight_checks
        
        # Determine enabled bots
        bot_configs = supervised_bot_configs(base_dir)
        enabled_bots = [cfg["bot_name"] for cfg in bot_configs]
        
        results = run_preflight_checks(only_bots=enabled_bots)
        
        # Compile summary
        print(f"[Orchestrator] Preflight results: {results}")
        return results

    def run_discovery(self, ready_portals: list[str]) -> bool:
        """Phase 2: Scraping and job screening phase (enqueues jobs)."""
        print("\n=== Phase 2: Starting Job Discovery Phase ===")
        if not ready_portals:
            print("[Orchestrator] No portals are ready. Skipping discovery.")
            return False
            
        portals_str = ",".join(ready_portals)
        print(f"[Orchestrator] Running discovery for portals: {portals_str}")
        send_telegram_alert(f"🔍 Starting discovery phase for portals: {portals_str}", bot_name="orchestrator", alert_type="status")
        
        # Call supervisor.py with discover stage
        cmd = [
            str(self.python_exe),
            str(self.supervisor_script),
            "--stage", "discover",
            # Keep the preflight-approved portal set intact. Passing an empty
            # value previously widened a multi-portal run to the planner's
            # defaults and silently dropped the explicit production plan.
            "--portal", portals_str,
        ]
        # Filter empty strings
        cmd = [x for x in cmd if x]
        if self.once:
            cmd.append("--once")
            
        try:
            res = subprocess.run(cmd, cwd=str(base_dir), env=os.environ.copy())
            success = res.returncode == 0
            print(f"[Orchestrator] Discovery stage exited with code: {res.returncode}")
            return success
        except Exception as e:
            print(f"[Orchestrator] Discovery execution failed: {e}")
            return False

    def run_applications(self, ready_portals: list[str]) -> bool:
        """Phase 3: Queue consumption and application submitting phase."""
        print("\n=== Phase 3: Starting Job Application Phase ===")
        if not ready_portals:
            print("[Orchestrator] No portals are ready. Skipping applications.")
            return False
            
        print(f"[Orchestrator] Spawning {self.workers} workers for application processing.")
        send_telegram_alert(f"🚀 Starting application workers (parallel workers: {self.workers})", bot_name="orchestrator", alert_type="status")
        
        # Call supervisor.py with apply stage
        cmd = [
            str(self.python_exe),
            str(self.supervisor_script),
            "--stage", "apply",
            "--workers", str(self.workers),
            # Only consume queues for sessions that passed this cycle's
            # preflight; do not let one expired portal block another.
            "--portal", ",".join(ready_portals),
        ]
        if self.once:
            cmd.append("--once")
            
        try:
            res = subprocess.run(cmd, cwd=str(base_dir), env=os.environ.copy())
            success = res.returncode == 0
            print(f"[Orchestrator] Application stage completed with code: {res.returncode}")
            return success
        except Exception as e:
            print(f"[Orchestrator] Application execution failed: {e}")
            return False

    def compile_reports(self) -> None:
        """Phase 4: Run Google Sheets logging and file upload reports."""
        print("\n=== Phase 4: Compiling Reports and Syncing to Google Sheets ===")
        try:
            from core.google_sheets_reporter import run_daily_reporting
            run_daily_reporting()
        except Exception as e:
            print(f"[Orchestrator] Reporting failed: {e}")

    def run_backup(self) -> None:
        """Phase 5: Run S3 artifact backup."""
        print("\n=== Phase 5: Running S3 Artifact Backup ===")
        cmd = [str(self.python_exe), str(self.supervisor_script), "--backup"]
        try:
            subprocess.run(cmd, cwd=str(base_dir), env=os.environ.copy())
        except Exception as e:
            print(f"[Orchestrator] S3 backup execution failed: {e}")

    def shutdown_vm(self) -> None:
        """Phase 6: Cost-saving VM shutdown."""
        print("\n=== Phase 6: Shutting down VM to save costs ===")
        try:
            from core.vm_lifecycle import stop_vm
            stop_vm()
        except Exception as e:
            print(f"[Orchestrator] VM shutdown execution failed: {e}")

    def execute_auto_cycle(self) -> None:
        """Run the full unattended daily automation cycle sequentially."""
        print("\n=============================================")
        print("Starting Autonomous Daily Orchestrator Cycle")
        print("=============================================")
        
        # 1. Preflight
        preflight = self.run_preflight_checks()
        
        if not preflight.get("mongodb"):
            send_telegram_alert("🚨 Orchestrator HALTING: MongoDB is unreachable. Manual SRE review required.", bot_name="system", alert_type="db_fatal", force=True)
            return

        # Determine ready portals
        ready_portals = []
        bot_configs = supervised_bot_configs(base_dir)
        for cfg in bot_configs:
            name = cfg["bot_name"]
            portal = cfg["portal"]
            if preflight["bots"].get(name):
                if portal not in ready_portals:
                    ready_portals.append(portal)
                    
        if not ready_portals:
            send_telegram_alert("⚠️ Orchestrator HALTING: All portal sessions are expired / invalid. User login sync required.", bot_name="system", alert_type="login_fatal", force=True)
            if get_secret("SHUTDOWN_ON_FATAL_SESSION", "false").lower() in ("true", "1", "yes"):
                self.shutdown_vm()
            return
            
        print(f"[Orchestrator] Portals ready for processing: {ready_portals}")
        
        # 2. Discovery
        self.run_discovery(ready_portals)
        
        # 3. Apply
        self.run_applications(ready_portals)
        
        # 4. Reports
        self.compile_reports()
        
        # 5. Backup
        self.run_backup()
        
        # 6. Shutdown
        self.shutdown_vm()
        print("\n[Orchestrator] Autonomous cycle completed successfully.")


def main():
    ap = argparse.ArgumentParser(description="Unified Job Automation Daily Orchestrator")
    ap.add_argument(
        "--stage",
        choices=["discover", "apply", "check-health", "report", "backup", "shutdown", "all"],
        default="all",
        help="Lifecycle phase to run. 'all' runs the full automated sequence."
    )
    ap.add_argument(
        "--auto",
        action="store_true",
        help="Run in headless autonomous mode (sequential phases)."
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run only one loop cycle of discovery/apply (no continuous supervisor retry)."
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel queue consumer workers for application stage."
    )
    args = ap.parse_args()

    orchestrator = DailyOrchestrator(workers=args.workers, once=args.once)

    if args.stage == "all" or args.auto:
        orchestrator.execute_auto_cycle()
    elif args.stage == "check-health":
        orchestrator.run_preflight_checks()
    elif args.stage == "discover":
        # Resolve active portals from a quick health check
        preflight = orchestrator.run_preflight_checks()
        ready_portals = list({cfg["portal"] for cfg in supervised_bot_configs(base_dir) if preflight["bots"].get(cfg["bot_name"])})
        orchestrator.run_discovery(ready_portals)
    elif args.stage == "apply":
        preflight = orchestrator.run_preflight_checks()
        ready_portals = list({cfg["portal"] for cfg in supervised_bot_configs(base_dir) if preflight["bots"].get(cfg["bot_name"])})
        orchestrator.run_applications(ready_portals)
    elif args.stage == "report":
        orchestrator.compile_reports()
    elif args.stage == "backup":
        orchestrator.run_backup()
    elif args.stage == "shutdown":
        orchestrator.shutdown_vm()


if __name__ == "__main__":
    main()
