"""Unified MongoDB writer.

Every bot writes to the same database and is isolated by the mandatory ``bot_id``
field. If MongoDB is unavailable, append-only JSONL fallback records are retained.

Collections (created lazily, indexed on first use):
    runs              run lifecycle and exit status
    jobs              jobs we observed (raw + normalized)
    gate_decisions    AI gate verdicts per (job, run)
    applications      application attempts (easy/external, saved, applied)
    questions         every form question + answer + accepted flag
    errors            structured error events (with screenshot path)

All writes are best-effort with a Mongo-disabled fallback to a JSONL file under
`data/db_fallback/`. This guarantees we never lose training data on a Mongo
outage.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


try:
    from pymongo import MongoClient, ASCENDING
    from pymongo.errors import PyMongoError
    _HAVE_PYMONGO = True
except ImportError:
    _HAVE_PYMONGO = False
    PyMongoError = Exception  # type: ignore[assignment,misc]


COLLECTIONS = ("runs", "jobs", "gate_decisions", "applications", "questions", "errors")

INDEXES = {
    "runs": [("bot_id", 1), ("started_at", -1)],
    "jobs": [("bot_id", 1), ("source", 1), ("job_id", 1)],
    "gate_decisions": [("bot_id", 1), ("job_id", 1), ("run_id", 1)],
    "applications": [("bot_id", 1), ("job_id", 1), ("run_id", 1), ("applied_at", -1)],
    "questions": [("bot_id", 1), ("job_id", 1), ("run_id", 1)],
    "errors": [("bot_id", 1), ("run_id", 1), ("ts", -1)],
}


@dataclass
class MongoStore:
    bot_id: str
    uri: str
    database: str
    fallback_dir: pathlib.Path
    _client: Optional[Any] = field(default=None, init=False, repr=False)
    _db: Optional[Any] = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.fallback_dir = pathlib.Path(self.fallback_dir)
        self.fallback_dir.mkdir(parents=True, exist_ok=True)
        if _HAVE_PYMONGO:
            try:
                self._client = MongoClient(self.uri, serverSelectionTimeoutMS=2500)
                # Force a round-trip so we know connection works.
                self._client.admin.command("ping")
                self._db = self._client[self.database]
                self._ensure_indexes()
                self._connected = True
            except PyMongoError as exc:
                self._record_fallback("connect_error", {"error": str(exc)})

    def _ensure_indexes(self) -> None:
        if self._db is None:
            return
        for name in COLLECTIONS:
            coll = self._db[name]
            try:
                coll.create_index([(field_name, ASCENDING if direction == 1 else -1) for field_name,direction in INDEXES[name]])
            except PyMongoError:
                pass

    @property
    def connected(self) -> bool:
        return self._connected

    # ── core write API ────────────────────────────────────────────────────────
    def insert(self, collection: str, doc: dict) -> str:
        if collection not in COLLECTIONS:
            raise ValueError(f"unknown collection: {collection}")
        doc = {"_id": doc.get("_id") or str(uuid.uuid4()),
               "bot_id": self.bot_id,
               "ts": doc.get("ts") or time.time(),
               **doc}
        if self._db is not None:
            try:
                self._db[collection].insert_one(doc)
                return doc["_id"]
            except PyMongoError as exc:
                self._record_fallback(f"insert_error:{collection}", {"error": str(exc), "doc": doc})
                return doc["_id"]
        self._record_fallback(f"insert:{collection}", doc)
        return doc["_id"]

    def update(self, collection: str, doc_id: str, patch: dict) -> bool:
        if collection not in COLLECTIONS:
            raise ValueError(f"unknown collection: {collection}")
        if self._db is not None:
            try:
                res = self._db[collection].update_one({"_id": doc_id}, {"$set": patch})
                return res.matched_count > 0
            except PyMongoError as exc:
                self._record_fallback(f"update_error:{collection}",
                                      {"error": str(exc), "_id": doc_id, "patch": patch})
                return False
        self._record_fallback(f"update:{collection}", {"_id": doc_id, "patch": patch})
        return False

    def find_one(self, collection: str, query: dict) -> Optional[dict]:
        if self._db is None:
            return None
        try:
            return self._db[collection].find_one(query)
        except PyMongoError:
            return None

    # ── high-level helpers ────────────────────────────────────────────────────
    def get_applied_job_ids(self) -> set[str]:
        """Fetch all successfully applied job IDs for this bot from MongoDB."""
        applied_ids = set()
        if self._db is not None:
            try:
                # applied: True means we successfully applied
                cursor = self._db["applications"].find({"bot_id": self.bot_id, "applied": True}, {"job_id": 1})
                for doc in cursor:
                    if "job_id" in doc:
                        applied_ids.add(str(doc["job_id"]))
            except PyMongoError:
                pass
        return applied_ids

    def start_run(self, mode: str, label: str = "") -> str:
        return self.insert("runs", {
            "mode": mode, "label": label,
            "started_at": time.time(), "status": "running",
        })

    def end_run(self, run_id: str, status: str, error: str = "") -> None:
        self.update("runs", run_id, {
            "ended_at": time.time(), "status": status, "error": error,
        })

    def record_job(self, run_id: str, job: dict) -> str:
        return self.insert("jobs", {"run_id": run_id, **job})

    def record_gate(self, run_id: str, job_id: str, verdict: str,
                    score: Optional[float], reasoning: str,
                    provider: str, latency_ms: float) -> str:
        return self.insert("gate_decisions", {
            "run_id": run_id, "job_id": job_id, "verdict": verdict,
            "score": score, "reasoning": reasoning,
            "provider": provider, "latency_ms": latency_ms,
        })

    def record_application(self, run_id: str, job_id: str, mode: str,
                           saved: bool, applied: bool, outcome: str) -> str:
        return self.insert("applications", {
            "run_id": run_id, "job_id": job_id, "mode": mode,
            "saved": saved, "applied": applied,
            "applied_at": time.time() if applied else None,
            "outcome": outcome,
        })

    def record_question(self, run_id: str, job_id: str, question: str,
                        kind: str, answer: str, source: str,
                        provider: str, accepted: Optional[bool]) -> str:
        return self.insert("questions", {
            "run_id": run_id, "job_id": job_id, "question": question,
            "kind": kind, "answer": answer, "source": source,
            "provider": provider, "accepted": accepted,
        })

    def record_error(self, run_id: str, where: str, error: str,
                     traceback_str: str = "", screenshot_path: str = "") -> str:
        return self.insert("errors", {
            "run_id": run_id, "where": where, "error": error,
            "traceback": traceback_str, "screenshot_path": screenshot_path,
        })

    # ── fallback writer ───────────────────────────────────────────────────────
    def _record_fallback(self, label: str, payload: dict) -> None:
        path = self.fallback_dir / f"db_fallback_{time.strftime('%Y%m%d')}.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "label": label,
                                    "bot_id": self.bot_id, "payload": payload},
                                   default=str) + os.linesep)
        except OSError:
            pass
