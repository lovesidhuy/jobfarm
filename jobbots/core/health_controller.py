from __future__ import annotations

"""Bot Health Controller.

Tracks bot heartbeats, computes health states, manages restart budgets,
and triggers alerts on failures or state changes.
"""

import os
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jobbots.core.secret_manager import get_secret
from jobbots.core.alerts import send_telegram_alert

# Configuration constants
CRASH_WINDOW_SECONDS = 600      # 10 minutes
MAX_CRASH_COUNT = 3             # Max crashes in window
BACKOFF_DURATION_SECONDS = 1800 # 30 minutes block
STALE_THRESHOLD_SECONDS = 300   # 5 minutes without heartbeat

def _get_monorepo_root() -> Path:
    from jobbots.core.supervised_bots import monorepo_root
    return monorepo_root()

def _get_status_file_path() -> Path:
    return _get_monorepo_root() / "data" / "bot_health_status.json"

_mongo_available_cache: bool | None = None
_mongo_cache_time = 0.0

def is_mongodb_available() -> bool:
    """Check if MongoDB is reachable with a short timeout and cache the result for 10 seconds."""
    global _mongo_available_cache, _mongo_cache_time
    now = time.time()
    if _mongo_available_cache is not None and (now - _mongo_cache_time) < 10.0:
        return _mongo_available_cache
        
    try:
        from pymongo import MongoClient
        uri = (
            os.environ.get("MONGODB_URI")
            or os.environ.get("MONGO_URI")
            or get_secret("MONGODB_URI", "")
            or "mongodb://localhost:27017"
        )
        client = MongoClient(uri, serverSelectionTimeoutMS=1000)
        client.admin.command("ping")
        client.close()
        _mongo_available_cache = True
    except Exception:
        _mongo_available_cache = False
        
    _mongo_cache_time = now
    return _mongo_available_cache

def _get_mongo_client() -> tuple[Optional[Any], Optional[Any]]:
    """Get MongoDB client and collection for health status if enabled and connected."""
    if not is_mongodb_available():
        return None, None
    try:
        from pymongo import MongoClient
        uri = (
            os.environ.get("MONGODB_URI")
            or os.environ.get("MONGO_URI")
            or get_secret("MONGODB_URI", "")
            or "mongodb://localhost:27017"
        )
        db_name = os.environ.get("MONGODB_HISTORY_DB") or get_secret("MONGODB_HISTORY_DB", "") or "auto_job_applier_history"
        client = MongoClient(uri, serverSelectionTimeoutMS=1000)
        db = client[db_name]
        coll = db["bot_health_status"]
        return client, coll
    except Exception:
        return None, None

def load_all_health_statuses() -> dict[str, dict[str, Any]]:
    """Load all bot health statuses from MongoDB or local JSON fallback."""
    client, coll = _get_mongo_client()
    if coll is not None:
        try:
            docs = list(coll.find({}, {"_id": 0}))
            client.close()
            return {doc["bot_name"]: doc for doc in docs}
        except Exception:
            pass
            
    # Local fallback
    path = _get_status_file_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_health_status(status_doc: dict[str, Any]) -> None:
    """Save bot health status to MongoDB and local JSON fallback."""
    bot_name = status_doc["bot_name"]
    
    # Write to local file first
    path = _get_status_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        local_statuses = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    local_statuses = json.load(f)
            except Exception:
                pass
        local_statuses[bot_name] = status_doc
        with open(path, "w", encoding="utf-8") as f:
            json.dump(local_statuses, f, indent=2)
    except Exception:
        pass
        
    # Write to MongoDB
    client, coll = _get_mongo_client()
    if coll is not None:
        try:
            coll.update_one(
                {"bot_name": bot_name},
                {"$set": status_doc},
                upsert=True
            )
            client.close()
        except Exception:
            # MongoDB Down, trigger alert if not already triggered recently
            send_telegram_alert(
                "MongoDB connection failed while saving health status; local fallback active.",
                bot_name="system",
                alert_type="mongo_down_fallback"
            )

def get_latest_heartbeat(bot_name: str) -> Optional[dict[str, Any]]:
    """Fetch the latest heartbeat record from MongoDB or local fallback file."""
    # Try MongoDB first
    if is_mongodb_available():
        try:
            from pymongo import MongoClient
            uri = (
                os.environ.get("MONGODB_URI")
                or os.environ.get("MONGO_URI")
                or get_secret("MONGODB_URI", "")
                or "mongodb://localhost:27017"
            )
            db_name = os.environ.get("MONGODB_HISTORY_DB") or get_secret("MONGODB_HISTORY_DB", "") or "auto_job_applier_history"
            client = MongoClient(uri, serverSelectionTimeoutMS=1000)
            db = client[db_name]
            coll = db["health_heartbeats"]
            doc = coll.find_one({"bot_name": bot_name}, {"_id": 0})
            client.close()
            if doc:
                # Parse ISO or datetime to timestamp
                last_time = doc.get("last_activity_time")
                if isinstance(last_time, datetime):
                    doc["last_activity_time_ts"] = last_time.replace(tzinfo=timezone.utc).timestamp()
                elif isinstance(last_time, str):
                    try:
                        dt = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
                        doc["last_activity_time_ts"] = dt.timestamp()
                    except Exception:
                        doc["last_activity_time_ts"] = time.time()
                else:
                    doc["last_activity_time_ts"] = time.time()
                return doc
        except Exception:
            pass
        
    # Try local fallback file
    path = _get_monorepo_root() / "data" / "heartbeats" / f"{bot_name}.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
                last_time = doc.get("last_activity_time")
                if isinstance(last_time, str):
                    try:
                        dt = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
                        doc["last_activity_time_ts"] = dt.timestamp()
                    except Exception:
                        doc["last_activity_time_ts"] = time.time()
                else:
                    doc["last_activity_time_ts"] = time.time()
                return doc
        except Exception:
            pass
            
    return None

def is_pid_alive(pid: int) -> bool:
    """Check if process with pid is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def evaluate_bot_health(bot_name: str) -> dict[str, Any]:
    """Perform health checks on a bot and update its persistent status.
    
    Rules:
    - If last_seen is older than 5 minutes -> STALE
    - If next_retry_after is in the future -> UNHEALTHY
    - If bot crashed 3 times in 10 minutes -> UNHEALTHY
    - If heartbeat status is failed -> FAILED
    - If status needs human attention (login, captcha, etc) -> NEEDS_X
    - If healthy but restarts > 0 -> DEGRADED
    """
    now = time.time()
    
    # Load status or create a new one
    statuses = load_all_health_statuses()
    status_doc = statuses.get(bot_name, {
        "bot_name": bot_name,
        "state": "HEALTHY",
        "pid": 0,
        "last_seen": 0.0,
        "restart_count": 0,
        "crash_window_start": 0.0,
        "last_crash_reason": "",
        "next_retry_after": 0.0
    })
    
    # Fetch latest heartbeat
    hb = get_latest_heartbeat(bot_name)
    if hb:
        status_doc["last_seen"] = hb.get("last_activity_time_ts", status_doc.get("last_seen", 0.0))
        status_doc["last_activity"] = hb.get("last_activity", "")
        # Update PID if heartbeat has a newer one
        hb_pid = hb.get("pid", 0)
        if hb_pid > 0 and status_doc["pid"] != hb_pid:
            # A new process took over, reset PID
            status_doc["pid"] = hb_pid
    
    # Check if process is running
    proc_running = is_pid_alive(status_doc.get("pid", 0))
    if not proc_running:
        status_doc["pid"] = 0 # reset pid if dead
        
    # ── Check Cooldowns and Backoffs ──
    next_retry = status_doc.get("next_retry_after", 0.0)
    if next_retry > now:
        status_doc["state"] = "UNHEALTHY"
        save_health_status(status_doc)
        return status_doc
    elif next_retry > 0.0:
        # Cooldown expired, clear it
        status_doc["next_retry_after"] = 0.0
        status_doc["restart_count"] = 0
        status_doc["crash_window_start"] = 0.0
        
    # ── Decide Health State ──
    hb_status = (hb.get("status") or "").lower() if hb else ""
    
    # Check STALE state
    last_seen = status_doc.get("last_seen", 0.0)
    is_stale = (last_seen > 0.0 and (now - last_seen) > STALE_THRESHOLD_SECONDS)
    
    if is_stale and proc_running:
        status_doc["state"] = "STALE"
        send_telegram_alert(
            f"Bot heartbeat is STALE. Last seen {int(now - last_seen)}s ago.",
            bot_name=bot_name,
            alert_type="stale"
        )
    elif "login" in hb_status or "login" in status_doc.get("last_activity", "").lower():
        status_doc["state"] = "NEEDS_LOGIN"
        send_telegram_alert(
            f"Bot needs login: {status_doc.get('last_activity', 'Login expired')}",
            bot_name=bot_name,
            alert_type="needs_login"
        )
    elif "captcha" in hb_status or "captcha" in status_doc.get("last_activity", "").lower():
        status_doc["state"] = "NEEDS_CAPTCHA"
        send_telegram_alert(
            "Bot is blocked by CAPTCHA page.",
            bot_name=bot_name,
            alert_type="needs_captcha"
        )
    elif "review" in hb_status or "review" in status_doc.get("last_activity", "").lower():
        status_doc["state"] = "NEEDS_REVIEW"
        send_telegram_alert(
            "Bot requires manual review/action.",
            bot_name=bot_name,
            alert_type="needs_review"
        )
    elif "profile" in hb_status or "profile" in status_doc.get("last_activity", "").lower():
        status_doc["state"] = "NEEDS_PROFILE_FIX"
        send_telegram_alert(
            f"Bot has profile issue: {status_doc.get('last_activity')}",
            bot_name=bot_name,
            alert_type="needs_profile_fix"
        )
    elif "ixbrowser" in hb_status or "ixbrowser" in status_doc.get("last_activity", "").lower():
        status_doc["state"] = "NEEDS_IXBROWSER_LOGIN"
        send_telegram_alert(
            "ixBrowser login expired or unreachable.",
            bot_name=bot_name,
            alert_type="needs_ixbrowser_login"
        )
    elif hb_status == "failed":
        status_doc["state"] = "FAILED"
    elif proc_running:
        restarts = status_doc.get("restart_count", 0)
        if restarts > 0:
            status_doc["state"] = "DEGRADED"
        else:
            status_doc["state"] = "HEALTHY"
    else:
        # Not running and no recent crash
        status_doc["state"] = "HEALTHY" if hb_status == "finished" else "FAILED"
        
    save_health_status(status_doc)
    return status_doc

def record_bot_start(bot_name: str, pid: int) -> dict[str, Any]:
    """Record that the supervisor/manager spawned a bot process."""
    statuses = load_all_health_statuses()
    status_doc = statuses.get(bot_name, {
        "bot_name": bot_name,
        "state": "HEALTHY",
        "pid": pid,
        "last_seen": time.time(),
        "restart_count": 0,
        "crash_window_start": 0.0,
        "last_crash_reason": "",
        "next_retry_after": 0.0
    })
    
    status_doc["pid"] = pid
    status_doc["last_seen"] = time.time()
    
    # If the bot was UNHEALTHY but the block expired or is overridden, reset
    if status_doc.get("next_retry_after", 0.0) <= time.time():
        status_doc["next_retry_after"] = 0.0
        
    # Evaluate health state immediately
    status_doc["state"] = "HEALTHY"
    save_health_status(status_doc)
    return status_doc

def record_bot_exit(bot_name: str, exit_code: int, last_error: str = "") -> dict[str, Any]:
    """Record a bot process exit and calculate crash windows/backoff policies."""
    now = time.time()
    statuses = load_all_health_statuses()
    status_doc = statuses.get(bot_name, {
        "bot_name": bot_name,
        "state": "HEALTHY",
        "pid": 0,
        "last_seen": now,
        "restart_count": 0,
        "crash_window_start": 0.0,
        "last_crash_reason": "",
        "next_retry_after": 0.0
    })
    
    status_doc["pid"] = 0
    
    # Check if exit is a crash
    is_crash = (exit_code != 0)

    # Datadog metric (best-effort, no-op without agent)
    try:
        from jobbots.core.datadog_metrics import increment as _dd_increment
        _dd_increment("supervisor.bot_exit", tags=[
            f"bot:{bot_name}",
            f"outcome:{'crash' if is_crash else 'clean'}",
        ])
    except Exception:
        pass
    
    # Extract crash reason (e.g. proxy, login) to alert specifically
    reason = last_error or f"Exited with code {exit_code}"
    
    # Check for specific failure warnings to alert on
    reason_lower = reason.lower()
    alert_type = "crash"
    if "proxy" in reason_lower:
        alert_type = "proxy_failure"
    elif "login" in reason_lower:
        alert_type = "login_expired"
    elif "captcha" in reason_lower:
        alert_type = "captcha_failure"
    
    if is_crash:
        status_doc["last_crash_reason"] = reason
        
        # Crash budget check
        window_start = status_doc.get("crash_window_start", 0.0)
        restarts = status_doc.get("restart_count", 0)
        
        if window_start == 0.0 or (now - window_start) > CRASH_WINDOW_SECONDS:
            # Start new crash tracking window
            status_doc["crash_window_start"] = now
            status_doc["restart_count"] = 1
        else:
            status_doc["restart_count"] = restarts + 1
            
        restarts_now = status_doc["restart_count"]
        
        if restarts_now >= MAX_CRASH_COUNT:
            # Too many crashes within 10 minutes: transition to UNHEALTHY & block
            status_doc["state"] = "UNHEALTHY"
            status_doc["next_retry_after"] = now + BACKOFF_DURATION_SECONDS
            try:
                from jobbots.core.datadog_metrics import increment as _dd_increment
                _dd_increment("supervisor.unhealthy", tags=[f"bot:{bot_name}"])
            except Exception:
                pass
            send_telegram_alert(
                f"Bot crashed {restarts_now} times in 10 minutes. Marked as UNHEALTHY. "
                f"Restarts blocked for {BACKOFF_DURATION_SECONDS // 60} minutes. Last error: {reason}",
                bot_name=bot_name,
                alert_type="unhealthy_blocked",
                force=True
            )
        else:
            status_doc["state"] = "DEGRADED"
            send_telegram_alert(
                f"Bot crashed (restart #{restarts_now}). Error: {reason}",
                bot_name=bot_name,
                alert_type=alert_type
            )
    else:
        # Successful completion (exit 0)
        status_doc["state"] = "HEALTHY"
        # Reset crash window and restart count on a clean exit
        status_doc["restart_count"] = 0
        status_doc["crash_window_start"] = 0.0
        
    save_health_status(status_doc)
    return status_doc

def is_bot_allowed_to_start(bot_name: str) -> tuple[bool, str]:
    """Check if a bot is currently allowed to start based on backoff status."""
    if os.environ.get("FORCE_RUN") == "1":
        return True, ""

    statuses = load_all_health_statuses()
    status_doc = statuses.get(bot_name)
    if not status_doc:
        return True, ""
        
    now = time.time()
    next_retry = status_doc.get("next_retry_after", 0.0)
    if next_retry > now:
        remaining_sec = int(next_retry - now)
        return False, f"Blocked due to crash budget. Retry allowed in {remaining_sec // 60}m {remaining_sec % 60}s."
        
    return True, ""
