"""Mongo-backed job history helpers with CSV compatibility.

The bots still write CSV files for local compatibility, but this module gives
the platform a single MongoDB history collection that can be backfilled from
those CSVs and exported back to CSV when needed.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any, Iterable


DEFAULT_DB = "auto_job_applier_history"
DEFAULT_COLLECTION = "job_history"

_STATUS_WORDS = {
    "applied": "applied",
    "failed": "failed",
    "skipped": "skipped",
    "saved": "saved",
}


def monorepo_root() -> Path:
    return _MONOREPO_ROOT


def repo_root() -> Path:
    return monorepo_root().parent


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or monorepo_root() / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def mongo_config() -> tuple[str, str, str]:
    uri = (
        os.environ.get("MONGODB_URI")
        or os.environ.get("MONGO_URI")
        or "mongodb://localhost:27017"
    )
    db_name = os.environ.get("MONGODB_HISTORY_DB") or os.environ.get("MONGODB_DB_NAME") or DEFAULT_DB
    collection = os.environ.get("MONGODB_HISTORY_COLLECTION") or DEFAULT_COLLECTION
    return uri.strip(), db_name.strip(), collection.strip()


def connect_collection(strict: bool = False):
    try:
        from pymongo import ASCENDING, DESCENDING, MongoClient
        from pymongo.errors import PyMongoError
    except Exception as exc:
        if strict:
            raise RuntimeError("pymongo is not installed") from exc
        return None

    uri, db_name, collection_name = mongo_config()
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        coll = client[db_name][collection_name]
        coll.create_index(
            [("platform", ASCENDING), ("status", ASCENDING), ("job_id", ASCENDING)],
            unique=True,
            name="platform_status_job_id_unique",
        )
        coll.create_index(
            [("platform", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="platform_status_updated_at",
        )
        coll.create_index([("source_file", ASCENDING)], name="source_file")
        return coll
    except PyMongoError as exc:
        if strict:
            raise
        print(f"[history_store] Mongo unavailable: {exc}")
        return None


def discover_history_csvs(root: Path | None = None) -> list[Path]:
    base = root or repo_root()
    candidates: list[Path] = []
    for path in base.rglob("*.csv"):
        parts = {p.lower() for p in path.parts}
        name = path.name.lower()
        if "all excels" not in parts:
            continue
        if not any(word in name for word in _STATUS_WORDS):
            continue
        candidates.append(path)
    return sorted(candidates)


def infer_platform_status(path: Path) -> tuple[str, str]:
    stem = path.stem.lower()
    status = "unknown"
    for word, value in _STATUS_WORDS.items():
        if re.search(rf"(^|_){word}(_|$)", stem):
            status = value
            break

    platform = stem
    for token in (
        "_applications_history",
        "_jobs_history",
        "_history",
        "_applied",
        "_failed",
        "_skipped",
        "_saved",
    ):
        platform = platform.replace(token, "")
    platform = platform.strip("_") or "unknown"
    if platform == "all":
        platform = "linkedin_default"
    return platform, status


def normalize_record(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k).strip(): "" if v is None else str(v).strip() for k, v in row.items() if str(k).strip()}


def job_id_for(row: dict[str, Any]) -> str:
    for key in ("Job ID", "job_id", "Job Id", "id", "ID"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def upsert_history_record(
    coll,
    *,
    platform: str,
    status: str,
    record: dict[str, Any],
    source_file: Path | str = "",
) -> bool:
    job_id = job_id_for(record)
    if not job_id:
        return False
    now = datetime.now(timezone.utc)
    document = {
        "platform": platform,
        "status": status,
        "job_id": job_id,
        "record": normalize_record(record),
        "source_file": str(source_file),
        "updated_at": now,
    }
    coll.update_one(
        {"platform": platform, "status": status, "job_id": job_id},
        {"$set": document, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return True


def import_csv_file(coll, path: Path, *, platform: str | None = None, status: str | None = None) -> int:
    inferred_platform, inferred_status = infer_platform_status(path)
    platform = platform or inferred_platform
    status = status or inferred_status
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="", errors="ignore") as handle:
        for row in csv.DictReader(handle):
            if upsert_history_record(
                coll,
                platform=platform,
                status=status,
                record=row,
                source_file=path,
            ):
                count += 1
    return count


def fieldnames_for(records: Iterable[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for record in records:
        for key in record:
            if key not in seen:
                seen.append(key)
    preferred = ["Job ID", "Title", "Company", "Work Location", "Job Link", "Reason"]
    ordered = [k for k in preferred if k in seen]
    ordered.extend(k for k in seen if k not in ordered)
    return ordered

