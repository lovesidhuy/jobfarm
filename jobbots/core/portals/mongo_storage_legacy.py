from __future__ import annotations
"""Optional MongoDB persistence for application history."""

import os
from datetime import datetime
from functools import lru_cache
from typing import Any

from jobbots.core.utils import print_lg, truncate_for_csv

try:
    from config.settings import (
        mongodb_collection,
        mongodb_database,
        mongodb_uri,
        use_mongodb,
    )
except ImportError:
    use_mongodb = False
    mongodb_uri = "mongodb://localhost:27017"
    mongodb_database = os.getenv("JOBBOTS_MONGO_DATABASE", "jobbots")
    mongodb_collection = "job_history"

try:
    from pymongo import ASCENDING, DESCENDING, MongoClient
    from pymongo.errors import PyMongoError
except ImportError:
    ASCENDING = DESCENDING = None
    MongoClient = None
    PyMongoError = Exception


def is_enabled() -> bool:
    return bool(use_mongodb)


@lru_cache(maxsize=1)
def _collection():
    if not use_mongodb:
        return None
    if MongoClient is None:
        print_lg("[MongoDB] pymongo is not installed; CSV history will still be used.")
        return None

    uri = os.getenv("MONGODB_URI") or mongodb_uri
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2500)
        client.admin.command("ping")
        database_name = os.getenv("JOBBOTS_MONGO_DATABASE", mongodb_database or "jobbots")
        collection = client[database_name][mongodb_collection]
        collection.create_index(
            [
                ("platform", ASCENDING),
                ("status", ASCENDING),
                ("job_id", ASCENDING),
            ],
            unique=True,
            name="platform_status_job_id_unique",
        )
        collection.create_index(
            [("platform", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="platform_status_updated_at",
        )
        return collection
    except PyMongoError as exc:
        print_lg(f"[MongoDB] Connection disabled for this run: {exc}")
        return None


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in record.items():
        cleaned[key] = truncate_for_csv(value)
    return cleaned


def save_job_record(platform: str, status: str, record: dict[str, Any]) -> None:
    collection = _collection()
    if collection is None:
        return

    job_id = truncate_for_csv(record.get("Job ID") or record.get("job_id"))
    if not job_id:
        return

    now = datetime.now()
    document = {
        "platform": platform,
        "status": status,
        "job_id": job_id,
        "record": _clean_record(record),
        "updated_at": now,
    }

    try:
        collection.update_one(
            {"platform": platform, "status": status, "job_id": job_id},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    except PyMongoError as exc:
        print_lg(f"[MongoDB] Failed saving {platform} {status} job {job_id}: {exc}")


def get_job_ids(platform: str, status: str = "applied") -> set[str]:
    collection = _collection()
    if collection is None:
        return set()

    try:
        return {
            doc["job_id"]
            for doc in collection.find(
                {"platform": platform, "status": status},
                {"job_id": 1, "_id": 0},
            )
            if doc.get("job_id")
        }
    except PyMongoError as exc:
        print_lg(f"[MongoDB] Failed reading {platform} job ids: {exc}")
        return set()


def list_jobs(platform: str, status: str) -> list[dict[str, Any]]:
    collection = _collection()
    if collection is None:
        return []

    try:
        docs = collection.find(
            {"platform": platform, "status": status},
            {"record": 1, "_id": 0},
        ).sort("updated_at", DESCENDING)
        return [doc.get("record", {}) for doc in docs]
    except PyMongoError as exc:
        print_lg(f"[MongoDB] Failed listing {platform} {status} jobs: {exc}")
        return []


def update_date_applied(platform: str, job_id: str, date_applied: str) -> bool:
    collection = _collection()
    if collection is None:
        return False

    try:
        result = collection.update_one(
            {"platform": platform, "status": "applied", "job_id": str(job_id)},
            {
                "$set": {
                    "record.Date Applied": date_applied,
                    "updated_at": datetime.now(),
                }
            },
        )
        return result.matched_count > 0
    except PyMongoError as exc:
        print_lg(f"[MongoDB] Failed updating applied date for {platform} {job_id}: {exc}")
        return False
