"""Cross-portal, append-only training telemetry.

This is deliberately separate from operational logs.  Every portal can emit
the same small event schema for discovery decisions, form questions, apply
outcomes, and later status evidence.  JSONL is always written; MongoDB is a
best-effort index for dashboards and analysis.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

_LOCK = threading.Lock()
_MONGO_LOCK = threading.Lock()
_MONGO_COLLECTION: Any = None
_MONGO_RESOLVED = False
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*['\"]?[^'\",}\s]+"
)


def _events_path() -> Path:
    explicit = (os.getenv("JOBBOTS_TRAINING_EVENTS_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    data_dir = (os.getenv("JOBBOTS_DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir) / "training" / "events.jsonl"
    return _MONOREPO_ROOT / "logs" / "training" / "events.jsonl"


def _clean(value: Any, limit: int = 1200) -> Any:
    if isinstance(value, str):
        value = value.replace("\x00", "")
        value = _SECRET_RE.sub(r"\1=[REDACTED]", value)
        value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
        return value if len(value) <= limit else value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): _clean(v, limit) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v, limit) for v in list(value)[:80]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _clean(str(value), limit)


def _mongo_collection():
    global _MONGO_COLLECTION, _MONGO_RESOLVED
    if _MONGO_RESOLVED:
        return _MONGO_COLLECTION
    with _MONGO_LOCK:
        if _MONGO_RESOLVED:
            return _MONGO_COLLECTION
        _MONGO_RESOLVED = True
        if (os.getenv("TRAINING_EVENTS_MONGO", "1") or "1").lower() in {"0", "false", "no", "off"}:
            return None
        try:
            from pymongo import ASCENDING, DESCENDING, MongoClient

            uri = (os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "").strip()
            if not uri:
                return None
            db_name = (os.getenv("JOBBOTS_MONGO_DATABASE") or os.getenv("MONGODB_DB_NAME") or "jobbots").strip()
            client = MongoClient(uri, serverSelectionTimeoutMS=1500)
            client.admin.command("ping")
            coll = client[db_name]["training_events"]
            coll.create_index([("event", ASCENDING), ("ts", DESCENDING)], name="training_event_ts")
            coll.create_index([("job_id", ASCENDING), ("ts", DESCENDING)], name="training_job_ts")
            _MONGO_COLLECTION = coll
        except Exception:
            _MONGO_COLLECTION = None
        return _MONGO_COLLECTION


def record_training_event(event: str, *, portal: str = "", profile: str = "",
                          job_id: str | int = "", source_job_id: str = "",
                          job_url: str = "", result_url: str = "",
                          **payload: Any) -> None:
    """Write one redacted event without ever affecting bot execution."""
    try:
        doc = {
            "schema_version": 1,
            "ts": datetime.now(timezone.utc),
            "event": str(event or "unknown"),
            "portal": str(portal or "").lower(),
            "profile": str(profile or "").lower(),
            "job_id": str(job_id or ""),
            "source_job_id": str(source_job_id or ""),
            "job_url": job_url or "",
            "result_url": result_url or "",
            "payload": payload,
        }
        safe = _clean(doc)
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        serial = {**safe, "ts": safe["ts"] if isinstance(safe["ts"], str) else str(safe["ts"])}
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(serial, ensure_ascii=False, default=str) + "\n")
        coll = _mongo_collection()
        if coll is not None:
            coll.insert_one(safe)
    except Exception:
        pass

