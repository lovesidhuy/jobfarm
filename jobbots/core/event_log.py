from __future__ import annotations

"""
Unified, append-only event log for every job-related decision a bot makes.

Each call inserts ONE document into MongoDB collection ``bot_events`` and also
appends a JSON line to ``logs/<bot_name>/events.jsonl`` as a fallback so the
local file is always the source of truth even if Mongo is unreachable.

Event types
-----------
- ``applied``        : Easy Apply / SmartApply / external-form submission completed.
- ``saved``          : Job saved (Easy Apply not available; user-side bookmark).
- ``skipped``        : Bot decided NOT to apply for a non-error reason
                       (location too broad, blacklisted, AI rejected, bad words,
                       not Easy Apply, suggested card, already applied, etc.).
- ``failed``         : Bot tried to apply and the application errored out
                       (form validation, submit click failed, captcha unsolved,
                       page crash, daily limit etc.).

Querying examples (mongosh)
---------------------------
    use auto_indeed_it_db
    db.bot_events.countDocuments({bot_name: "indeed_it",  event: "applied"})
    db.bot_events.aggregate([
      { $match: { bot_name: "linkedin_it", event: "skipped" } },
      { $group: { _id: "$reason", n: { $sum: 1 } } },
      { $sort: { n: -1 } }
    ])
"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pymongo import ASCENDING, DESCENDING, MongoClient
    from pymongo.errors import PyMongoError
except Exception:  # pymongo not installed → JSONL only
    ASCENDING = DESCENDING = None
    MongoClient = None
    PyMongoError = Exception  # type: ignore[misc,assignment]


_LOCK = threading.Lock()
_COLLECTION_NAME = "bot_events"
_DEFAULT_DB = "auto_job_applier_events"
_JSONL_DIR_NAME = "logs"

_VALID_EVENTS = {"applied", "saved", "skipped", "failed", "filter_rejected", "manual_review"}

_collection_cache: Any = None
_collection_resolved = False


def _bot_name() -> str:
    return (os.environ.get("BOT_NAME") or "unknown_bot").strip()


def _mongo_uri() -> str:
    return (os.environ.get("MONGODB_URI")
            or os.environ.get("MONGO_URI")
            or "mongodb://localhost:27017").strip()


def _mongo_db_name() -> str:
    # Per-bot DB if provided, else a shared events DB so reports across bots are cheap.
    return (os.environ.get("MONGODB_EVENTS_DB")
            or os.environ.get("MONGODB_DB_NAME")
            or _DEFAULT_DB).strip()


def _get_collection():
    global _collection_cache, _collection_resolved
    if _collection_resolved:
        return _collection_cache
    _collection_resolved = True
    if MongoClient is None:
        return None
    try:
        client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        coll = client[_mongo_db_name()][_COLLECTION_NAME]
        try:
            coll.create_index(
                [("bot_name", ASCENDING), ("event", ASCENDING), ("ts", DESCENDING)],
                name="bot_event_ts",
            )
            coll.create_index(
                [("bot_name", ASCENDING), ("job_id", ASCENDING), ("ts", DESCENDING)],
                name="bot_job_ts",
            )
        except Exception:
            pass
        _collection_cache = coll
        return coll
    except Exception:
        return None


def _truncate(value: Any, limit: int = 800) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): _truncate(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_truncate(v, limit) for v in list(value)[:50]]
    return value


def _jsonl_path(bot_name: str) -> Path:
    return Path(_JSONL_DIR_NAME) / bot_name / "events.jsonl"


def record_event(
    event: str,
    job_id: str | int | None = None,
    *,
    bot_name: str | None = None,
    portal: str | None = None,
    profile: str | None = None,
    reason: str | None = None,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    job_link: str | None = None,
    application_link: str | None = None,
    **extra: Any,
) -> None:
    """
    Append one event document. Never raises — logging must not break the bot.

    Parameters
    ----------
    event : one of ``_VALID_EVENTS``
    job_id : platform job id (string)
    reason : short machine-readable reason for ``skipped`` / ``failed``
             e.g. "country_only_location", "blacklisted_company",
             "ai_rejected", "captcha_unsolved", "submit_failed".
    extra : free-form context (search_term, score, etc.)
    """
    try:
        if event not in _VALID_EVENTS:
            event = "filter_rejected"  # safe bucket
        bot = (bot_name or _bot_name()).strip()
        doc = {
            "ts": datetime.now(timezone.utc),
            "bot_name": bot,
            "portal": (portal or bot.split("_")[0]),
            "profile": (profile or (bot.split("_", 1)[1] if "_" in bot else "")),
            "event": event,
            "job_id": str(job_id) if job_id is not None else "",
            "reason": _truncate(reason or ""),
            "title": _truncate(title or ""),
            "company": _truncate(company or ""),
            "location": _truncate(location or ""),
            "job_link": _truncate(job_link or ""),
            "application_link": _truncate(application_link or ""),
        }
        if extra:
            doc["extra"] = _truncate(extra)

        # Datadog metric (best-effort, no-op without agent)
        try:
            from jobbots.core.datadog_metrics import increment as _dd_increment
            _dd_increment("bot.applications", tags=[
                f"bot:{doc['bot_name']}",
                f"portal:{doc['portal']}",
                f"event:{doc['event']}",
            ])
        except Exception:
            pass

        # JSONL fallback (always written)
        try:
            jpath = _jsonl_path(bot)
            jpath.parent.mkdir(parents=True, exist_ok=True)
            with _LOCK:
                with jpath.open("a", encoding="utf-8") as f:
                    serial_doc = {**doc, "ts": doc["ts"].isoformat()}
                    f.write(json.dumps(serial_doc, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

        # Mongo (best-effort)
        coll = _get_collection()
        if coll is not None:
            try:
                coll.insert_one(doc)
            except PyMongoError:
                pass
    except Exception:
        # never let logging crash the bot
        pass


def quick_stats(bot_name: str | None = None, since_hours: int | None = None) -> dict:
    """
    Aggregate counts per event-type for a bot. Falls back to JSONL if Mongo
    unreachable. Returns ``{event: count}`` with a special key ``"_total"``.
    """
    bot = (bot_name or _bot_name()).strip()
    counts: dict[str, int] = {"_total": 0}
    coll = _get_collection()
    cutoff = None
    if since_hours and since_hours > 0:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    if coll is not None:
        try:
            match: dict[str, Any] = {"bot_name": bot}
            if cutoff:
                match["ts"] = {"$gte": cutoff}
            for row in coll.aggregate([
                {"$match": match},
                {"$group": {"_id": "$event", "n": {"$sum": 1}}},
            ]):
                counts[row["_id"]] = int(row["n"])
                counts["_total"] += int(row["n"])
            return counts
        except Exception:
            pass

    # JSONL fallback
    try:
        path = _jsonl_path(bot)
        if not path.exists():
            return counts
        cutoff_iso = cutoff.isoformat() if cutoff else None
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if cutoff_iso and obj.get("ts", "") < cutoff_iso:
                    continue
                ev = obj.get("event", "filter_rejected")
                counts[ev] = counts.get(ev, 0) + 1
                counts["_total"] += 1
    except Exception:
        pass
    return counts
