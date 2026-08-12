from __future__ import annotations

"""Bot health heartbeat system for MongoDB.

Keeps a live state/heartbeat document per bot in the health_heartbeats collection.
"""

import os
import socket
import sys
from datetime import datetime, timezone

def send_heartbeat(
    bot_name: str,
    status: str,
    last_activity: str = "",
    run_id: str | None = None,
) -> bool:
    """Upsert the bot runtime status and heartbeat metrics into MongoDB."""
    # Write local fallback heartbeat file first (always best-effort)
    try:
        from pathlib import Path
        import json
        current = Path(__file__).resolve()
        root = None
        for parent in [current] + list(current.parents):
            if (parent / "automation_monorepo").is_dir():
                root = parent / "automation_monorepo"
                break
            if parent.name == "automation_monorepo":
                root = parent
                break
            if (parent / ".git").exists():
                if (parent / "automation_monorepo").is_dir():
                    root = parent / "automation_monorepo"
                else:
                    root = parent
                break
        if root is None:
            root = current.parent
            
        fallback_dir = root / "data" / "heartbeats"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_path = fallback_dir / f"{bot_name}.json"
        
        now_str = datetime.now(timezone.utc).isoformat()
        fallback_doc = {
            "bot_name": bot_name,
            "status": status,
            "last_activity": last_activity,
            "last_activity_time": now_str,
            "pid": os.getpid(),
            "run_id": run_id or os.environ.get("BOT_RUN_ID") or "",
            "updated_at": now_str,
        }
        with open(fallback_path, "w", encoding="utf-8") as f:
            json.dump(fallback_doc, f, indent=2)
    except Exception:
        pass

    # Datadog gauge (best-effort, no-op without agent)
    try:
        from jobbots.core.datadog_metrics import gauge as _dd_gauge
        _dd_gauge("bot.heartbeat", 1, tags=[f"bot:{bot_name}", f"status:{status}"])
    except Exception:
        pass

    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
    except ImportError:
        return False

    uri = (
        os.environ.get("MONGODB_URI")
        or os.environ.get("MONGO_URI")
        or "mongodb://localhost:27017"
    )
    db_name = os.environ.get("JOBBOTS_MONGO_DATABASE") or os.environ.get("MONGODB_DB_NAME") or "jobbots"
        
    coll_name = "health_heartbeats"

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        db = client[db_name]
        coll = db[coll_name]
        
        # Ensure indexes on first use
        coll.create_index([("bot_name", 1)], unique=True)
        coll.create_index([("last_activity_time", -1)])
        
        pid = os.getpid()
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = "unknown"
            
        now = datetime.now(timezone.utc)
        
        document = {
            "bot_name": bot_name,
            "status": status,
            "last_activity": last_activity,
            "last_activity_time": now,
            "pid": pid,
            "hostname": hostname,
            "run_id": run_id or os.environ.get("BOT_RUN_ID") or "",
            "python_version": sys.version.split()[0],
            "updated_at": now,
        }
        
        coll.update_one(
            {"bot_name": bot_name},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return True
    except PyMongoError:
        # Silently fail so that offline runs are unaffected
        return False
    except Exception:
        return False
