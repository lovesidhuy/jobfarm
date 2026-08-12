"""SRE Error Recovery Playbook / Self-Healing logic.

Resolves common failure modes autonomously: port conflicts, profile corruption/locks,
rate limits, and MongoDB down fallbacks.
"""

from __future__ import annotations

import os
import sys
import shutil
import time
import zipfile
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT

# Add project root to path
base_dir = _MONOREPO_ROOT
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from jobbots.core.secret_manager import get_secret
from jobbots.core.alerts import send_telegram_alert
from jobbots.core.session_registry import record_bot_session_not_ready
from jobbots.core.supervised_bots import supervised_bot_config_by_name
from jobbots.core.supervisor_chrome import kill_bot_chromes


def clean_port_conflict(bot_name: str) -> bool:
    """Kill any orphan Chrome/browser processes using the port or profile of the bot."""
    print(f"[ErrorRecovery] Cleaning port conflicts for {bot_name}...")
    try:
        cfg = supervised_bot_config_by_name(bot_name)
        profile_dir = cfg.get("profile_dir", "")
        port = cfg.get("cdp_port")
        
        # Call supervisor_chrome helper to clean up processes
        kill_bot_chromes(profile_dir, port)
        print(f"[ErrorRecovery] Cleaned up processes on port {port} and profile {profile_dir}")
        return True
    except Exception as e:
        print(f"[ErrorRecovery] Port cleanup failed for {bot_name}: {e}")
        return False


def backup_profile(bot_name: str) -> bool:
    """Create a backup ZIP of the browser profile directory when it is confirmed healthy."""
    try:
        cfg = supervised_bot_config_by_name(bot_name)
        profile_dir = Path(cfg.get("profile_dir", ""))
        if not profile_dir.is_dir():
            print(f"[ErrorRecovery] Profile dir {profile_dir} does not exist. Skipping backup.")
            return False

        backup_dir = base_dir / "data" / "profile_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_zip = backup_dir / f"{bot_name}_profile.zip"

        # Create zip file, skipping Cache and lock files
        print(f"[ErrorRecovery] Creating profile backup zip for {bot_name} at {backup_zip}...")
        with zipfile.ZipFile(backup_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(profile_dir):
                # Ignore Cache / Temp files to keep backup small
                if any(x in root.lower() for x in ("cache", "temp", "gpu", "logs")):
                    continue
                for file in files:
                    # Ignore lock files
                    if file in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
                        continue
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(profile_dir.parent)
                    zipf.write(file_path, arcname)
                    
        print(f"[ErrorRecovery] Profile backup completed successfully for {bot_name}")
        return True
    except Exception as e:
        print(f"[ErrorRecovery] Profile backup failed for {bot_name}: {e}")
        return False


def restore_profile(bot_name: str) -> bool:
    """Restore the browser profile directory from the latest backup zip."""
    try:
        cfg = supervised_bot_config_by_name(bot_name)
        profile_dir = Path(cfg.get("profile_dir", ""))
        backup_zip = base_dir / "data" / "profile_backups" / f"{bot_name}_profile.zip"

        if not backup_zip.is_file():
            print(f"[ErrorRecovery] Backup file {backup_zip} not found for restoration.")
            return False

        # Make sure no processes are locking the directory
        clean_port_conflict(bot_name)
        time.sleep(2)

        # Remove corrupted directory
        if profile_dir.exists():
            print(f"[ErrorRecovery] Removing corrupted profile directory {profile_dir}...")
            shutil.rmtree(profile_dir, ignore_errors=True)

        # Re-create and extract
        profile_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"[ErrorRecovery] Extracting profile backup for {bot_name}...")
        with zipfile.ZipFile(backup_zip, 'r') as zipf:
            zipf.extractall(profile_dir.parent)
            
        print(f"[ErrorRecovery] Successfully restored profile {bot_name} from backup.")
        send_telegram_alert(
            f"🔄 Bot profile '{bot_name}' was corrupted and has been automatically restored from backup.",
            bot_name=bot_name,
            alert_type="profile_restored",
            force=True
        )
        return True
    except Exception as e:
        print(f"[ErrorRecovery] Profile restoration failed for {bot_name}: {e}")
        return False


def handle_rate_limit(bot_name: str, cooldown_seconds: int = 1800) -> None:
    """Apply an exponential backoff / cooldown block to a bot that has been rate-limited or blocked."""
    print(f"[ErrorRecovery] Setting rate-limit backoff of {cooldown_seconds}s for {bot_name}...")
    try:
        from jobbots.core.health_controller import load_all_health_statuses, save_health_status
        statuses = load_all_health_statuses()
        status_doc = statuses.get(bot_name, {
            "bot_name": bot_name,
            "state": "HEALTHY",
            "pid": 0,
            "last_seen": time.time(),
            "restart_count": 0,
            "crash_window_start": 0.0,
            "last_crash_reason": "",
            "next_retry_after": 0.0
        })
        
        status_doc["state"] = "UNHEALTHY"
        status_doc["next_retry_after"] = time.time() + cooldown_seconds
        status_doc["last_crash_reason"] = "Rate limit / CAPTCHA block detected"
        save_health_status(status_doc)
        
        record_bot_session_not_ready(bot_name, reason="rate_limited")
        
        send_telegram_alert(
            f"⏳ Rate limit or CAPTCHA block detected for {bot_name}. "
            f"Applying backoff cooldown: bot is blocked from restarting for {cooldown_seconds // 60} minutes.",
            bot_name=bot_name,
            alert_type="rate_limited_blocked",
            force=True
        )
    except Exception as e:
        print(f"[ErrorRecovery] Failed to save backoff status: {e}")


def execute_recovery_actions(bot_name: str, error_msg: str) -> bool:
    """Analyze the error message and run the appropriate recovery actions."""
    reason = error_msg.lower()
    
    # 1. Check for CDP Port or Browser launch failures (conflict)
    if any(x in reason for x in ("cdp", "port", "address already in use", "target closed", "connection refused")):
        print(f"[ErrorRecovery] Diagnosed port conflict / browser lock for {bot_name}.")
        return clean_port_conflict(bot_name)
        
    # 2. Check for Profile locks or corruption
    if any(x in reason for x in ("profile corrupted", "profile lock", "singletonlock", "failed to read profile")):
        print(f"[ErrorRecovery] Diagnosed profile corruption for {bot_name}.")
        # Try cleaning locks first
        clean_port_conflict(bot_name)
        # Attempt restore
        return restore_profile(bot_name)
        
    # 3. Check for rate limit or captcha block
    if any(x in reason for x in ("rate limit", "too many requests", "429", "captcha block", "cloudflare challenge")):
        print(f"[ErrorRecovery] Diagnosed blockpage or rate limit for {bot_name}.")
        handle_rate_limit(bot_name, cooldown_seconds=1800)
        return True
        
    # 4. Check for session expiry
    if "session" in reason or "login" in reason or "unauthorized" in reason:
        print(f"[ErrorRecovery] Diagnosed session expiry for {bot_name}.")
        record_bot_session_not_ready(bot_name, reason="session_expired")
        return True

    return False


if __name__ == "__main__":
    if len(sys.argv) > 2:
        execute_recovery_actions(sys.argv[1], sys.argv[2])
