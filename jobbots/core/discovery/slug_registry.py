"""Permanent ATS slug registry — the flywheel's memory.

Two backends, one interface:

  ``MongoSlugRegistry``  — canonical store (``ats_slug_registry`` collection).
  ``JsonSlugRegistry``   — atomic-file fallback for dev / Mongo-down runs.

``get_registry()`` picks Mongo when reachable, else JSON, so harvesters and
the poller never crash on infra hiccups (mirrors ``history_store``'s
``connect_collection(strict=False)`` pattern).

Document schema
---------------
::

  {
    "slug_id": "acme",                    # cleaned token (unique per platform)
    "platform": "greenhouse",             # greenhouse | lever
    "status": "active",                   # active | inactive
    "discovery_source": "manual_seed",    # manual_seed | jobspy | google_cdp |
                                          #   firecrawl | tavily | footprint_sensor
    "first_discovered_at": <datetime>,
    "last_seen_at": <datetime>,           # last upsert touch by any harvester
    "last_successful_poll_at": <datetime|null>,
    "consecutive_failures": 0,
    "deactivated_reason": null,           # e.g. "http_404_x3"
    "company_name": null                  # best-effort, filled by API poll
  }

Dead-slug policy
----------------
404/410 from the board API increments ``consecutive_failures``; at
``ATS_SLUG_MAX_CONSEC_FAILURES`` (default 3) the slug flips to ``inactive``
and is excluded from future polls. Any success resets the counter and
reactivates the slug. Failures are consecutive per (platform, slug), so one
bad board never poisons the run.

Env
---
``ATS_SLUG_REGISTRY_BACKEND``   ``auto`` (default) | ``mongo`` | ``json``
``ATS_SLUG_REGISTRY_JSON``      JSON file path (default
                                ``<monorepo>/artifacts/ats_slug_registry.json``)
``ATS_SLUG_MAX_CONSEC_FAILURES`` dead-slug threshold (default ``3``)
``MONGODB_URI`` / ``MONGO_URI`` / ``MONGODB_DB_NAME``
                                inherited from history_store.mongo_config()
``MONGODB_SLUG_COLLECTION``     collection name (default ``ats_slug_registry``)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any, Iterable, Protocol

from jobbots.core.discovery.ats_slugs import SUPPORTED_PLATFORMS, clean_slug

_log = logging.getLogger("discovery.slug_registry")

DEFAULT_COLLECTION = "ats_slug_registry"
_STATUS_ACTIVE = "active"
_STATUS_INACTIVE = "inactive"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _max_consec_failures() -> int:
    try:
        return max(1, int(os.getenv("ATS_SLUG_MAX_CONSEC_FAILURES", "3") or "3"))
    except ValueError:
        return 3


def _json_path() -> Path:
    raw = (os.getenv("ATS_SLUG_REGISTRY_JSON") or "").strip()
    if raw:
        return Path(raw)
    root = _MONOREPO_ROOT  # automation_monorepo/
    return root / "artifacts" / "ats_slug_registry.json"


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class SlugRegistry(Protocol):
    """Storage contract used by harvesters, the seeder, and the poller."""

    def upsert_slug(
        self,
        slug: str,
        platform: str,
        *,
        source: str,
        company_name: str | None = None,
    ) -> str:
        """Insert or touch a slug. Returns ``inserted`` | ``updated`` | ``invalid``."""
        ...

    def mark_poll_success(self, slug: str, platform: str, *, company_name: str | None = None) -> None:
        ...

    def mark_poll_failure(self, slug: str, platform: str, *, reason: str) -> None:
        ...

    def iter_active_slugs(self, platform: str | None = None) -> list[dict[str, Any]]:
        ...

    def stats(self) -> dict[str, int]:
        ...


def _validate(slug: str, platform: str) -> tuple[str, str] | None:
    s = clean_slug(slug)
    p = (platform or "").strip().lower()
    if not s or p not in SUPPORTED_PLATFORMS:
        return None
    return s, p


# ---------------------------------------------------------------------------
# Mongo backend
# ---------------------------------------------------------------------------

def _jobbots_mongo_db_name(fallback: str) -> str:
    """Prefer the application queue DB (jobbots) over history-only default.

    Production sets ``JOBBOTS_MONGO_DATABASE=jobbots``.  The history helper
    defaults to ``auto_job_applier_history``, which made the slug flywheel
    write to an empty orphan DB while discovery counted ``jobbots`` → 0 slugs.
    """
    return (
        (os.getenv("JOBBOTS_MONGO_DATABASE") or "").strip()
        or (os.getenv("MONGODB_SLUG_DB") or "").strip()
        or (os.getenv("MONGODB_DB_NAME") or "").strip()
        or (os.getenv("MONGODB_HISTORY_DB") or "").strip()
        or (fallback or "").strip()
        or "jobbots"
    )


class MongoSlugRegistry:
    """MongoDB-backed registry (production canonical store)."""

    def __init__(self) -> None:
        from jobbots.core.history_store import load_dotenv, mongo_config

        load_dotenv()
        uri, history_db, _ = mongo_config()
        db_name = _jobbots_mongo_db_name(history_db)
        coll_name = (os.getenv("MONGODB_SLUG_COLLECTION") or DEFAULT_COLLECTION).strip()

        from pymongo import ASCENDING, MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        self._coll = client[db_name][coll_name]
        _log.info("Mongo slug registry db=%s collection=%s", db_name, coll_name)
        self._coll.create_index(
            [("platform", ASCENDING), ("slug_id", ASCENDING)],
            unique=True,
            name="platform_slug_unique",
        )
        self._coll.create_index([("status", ASCENDING)], name="status")
        self._coll.create_index(
            [("discovery_source", ASCENDING)], name="discovery_source"
        )

    # -- writes ------------------------------------------------------------
    def upsert_slug(
        self,
        slug: str,
        platform: str,
        *,
        source: str,
        company_name: str | None = None,
    ) -> str:
        vp = _validate(slug, platform)
        if not vp:
            return "invalid"
        s, p = vp
        now = _now()
        update: dict[str, Any] = {
            "$set": {"last_seen_at": now},
            "$setOnInsert": {
                "slug_id": s,
                "platform": p,
                "status": _STATUS_ACTIVE,
                "discovery_source": source or "unknown",
                "first_discovered_at": now,
                "last_successful_poll_at": None,
                "consecutive_failures": 0,
                "deactivated_reason": None,
                "company_name": company_name,
            },
        }
        # Never let a later harvester overwrite the original source on re-seed.
        res = self._coll.update_one(
            {"platform": p, "slug_id": s}, update, upsert=True
        )
        return "inserted" if res.upserted_id is not None else "updated"

    def mark_poll_success(self, slug: str, platform: str, *, company_name: str | None = None) -> None:
        vp = _validate(slug, platform)
        if not vp:
            return
        s, p = vp
        set_fields: dict[str, Any] = {
            "last_successful_poll_at": _now(),
            "consecutive_failures": 0,
            "status": _STATUS_ACTIVE,
            "deactivated_reason": None,
        }
        if company_name:
            set_fields["company_name"] = company_name
        self._coll.update_one(
            {"platform": p, "slug_id": s},
            {"$set": set_fields},
        )

    def mark_poll_failure(self, slug: str, platform: str, *, reason: str) -> None:
        vp = _validate(slug, platform)
        if not vp:
            return
        s, p = vp
        threshold = _max_consec_failures()
        doc = self._coll.find_one_and_update(
            {"platform": p, "slug_id": s},
            {"$inc": {"consecutive_failures": 1}},
            return_document=True,
        )
        failures = int((doc or {}).get("consecutive_failures") or 1)
        if failures >= threshold:
            self._coll.update_one(
                {"platform": p, "slug_id": s},
                {
                    "$set": {
                        "status": _STATUS_INACTIVE,
                        "deactivated_reason": f"{reason}_x{failures}",
                    }
                },
            )
            _log.info("slug deactivated: %s/%s (%s x%d)", p, s, reason, failures)

    # -- reads -------------------------------------------------------------
    def iter_active_slugs(self, platform: str | None = None) -> list[dict[str, Any]]:
        q: dict[str, Any] = {"status": _STATUS_ACTIVE}
        if platform:
            q["platform"] = platform.strip().lower()
        return list(self._coll.find(q, {"_id": 0}))

    def stats(self) -> dict[str, int]:
        pipe = [{"$group": {"_id": {"p": "$platform", "s": "$status"}, "n": {"$sum": 1}}}]
        out: dict[str, int] = {}
        for row in self._coll.aggregate(pipe):
            key = f"{row['_id']['p']}_{row['_id']['s']}"
            out[key] = int(row["n"])
        out["total"] = sum(out.values())
        return out


# ---------------------------------------------------------------------------
# JSON fallback backend
# ---------------------------------------------------------------------------

class JsonSlugRegistry:
    """Atomic JSON-file registry — dev / Mongo-down fallback.

    Thread-safe within the process; atomic via tmp-file + os.replace.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _json_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self._path.exists():
            self._write({"slugs": {}})

    # -- persistence --------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"slugs": {}}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self._path)

    @staticmethod
    def _key(platform: str, slug: str) -> str:
        return f"{platform}:{slug}"

    # -- writes ------------------------------------------------------------
    def upsert_slug(
        self,
        slug: str,
        platform: str,
        *,
        source: str,
        company_name: str | None = None,
    ) -> str:
        vp = _validate(slug, platform)
        if not vp:
            return "invalid"
        s, p = vp
        now = _now().isoformat()
        with self._lock:
            data = self._read()
            slugs = data.setdefault("slugs", {})
            key = self._key(p, s)
            if key in slugs:
                slugs[key]["last_seen_at"] = now
                self._write(data)
                return "updated"
            slugs[key] = {
                "slug_id": s,
                "platform": p,
                "status": _STATUS_ACTIVE,
                "discovery_source": source or "unknown",
                "first_discovered_at": now,
                "last_seen_at": now,
                "last_successful_poll_at": None,
                "consecutive_failures": 0,
                "deactivated_reason": None,
                "company_name": company_name,
            }
            self._write(data)
            return "inserted"

    def mark_poll_success(self, slug: str, platform: str, *, company_name: str | None = None) -> None:
        vp = _validate(slug, platform)
        if not vp:
            return
        s, p = vp
        with self._lock:
            data = self._read()
            rec = data.get("slugs", {}).get(self._key(p, s))
            if not rec:
                return
            rec["last_successful_poll_at"] = _now().isoformat()
            rec["consecutive_failures"] = 0
            rec["status"] = _STATUS_ACTIVE
            rec["deactivated_reason"] = None
            if company_name:
                rec["company_name"] = company_name
            self._write(data)

    def mark_poll_failure(self, slug: str, platform: str, *, reason: str) -> None:
        vp = _validate(slug, platform)
        if not vp:
            return
        s, p = vp
        threshold = _max_consec_failures()
        with self._lock:
            data = self._read()
            rec = data.get("slugs", {}).get(self._key(p, s))
            if not rec:
                return
            rec["consecutive_failures"] = int(rec.get("consecutive_failures") or 0) + 1
            if rec["consecutive_failures"] >= threshold:
                rec["status"] = _STATUS_INACTIVE
                rec["deactivated_reason"] = f"{reason}_x{rec['consecutive_failures']}"
                _log.info(
                    "slug deactivated: %s/%s (%s x%d)",
                    p, s, reason, rec["consecutive_failures"],
                )
            self._write(data)

    # -- reads -------------------------------------------------------------
    def iter_active_slugs(self, platform: str | None = None) -> list[dict[str, Any]]:
        p = (platform or "").strip().lower() or None
        out: list[dict[str, Any]] = []
        for rec in self._read().get("slugs", {}).values():
            if rec.get("status") != _STATUS_ACTIVE:
                continue
            if p and rec.get("platform") != p:
                continue
            out.append(dict(rec))
        return out

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rec in self._read().get("slugs", {}).values():
            key = f"{rec.get('platform')}_{rec.get('status')}"
            out[key] = out.get(key, 0) + 1
        out["total"] = sum(out.values())
        return out


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_registry_cache: SlugRegistry | None = None
_registry_lock = threading.Lock()


def get_registry(*, force_backend: str | None = None) -> SlugRegistry:
    """Return the shared registry (Mongo when reachable, else JSON)."""
    global _registry_cache
    backend = (force_backend or os.getenv("ATS_SLUG_REGISTRY_BACKEND") or "auto").strip().lower()
    with _registry_lock:
        if _registry_cache is not None and not force_backend:
            return _registry_cache
        reg: SlugRegistry
        if backend in {"auto", "mongo"}:
            try:
                reg = MongoSlugRegistry()
                _log.debug("slug registry backend: mongo")
            except Exception as exc:
                if backend == "mongo":
                    raise
                _log.warning("Mongo slug registry unavailable (%s); using JSON fallback", exc)
                reg = JsonSlugRegistry()
        else:
            reg = JsonSlugRegistry()
        if not force_backend:
            _registry_cache = reg
        return reg


# ---------------------------------------------------------------------------
# Convenience: harvester hook (the flywheel upsert)
# ---------------------------------------------------------------------------

def register_slugs(
    pairs: Iterable[tuple[str, str]],
    *,
    source: str,
    registry: SlugRegistry | None = None,
) -> dict[str, int]:
    """Upsert ``(platform, slug)`` pairs; returns outcome counts.

    Swallows storage errors — slug capture must never break a harvester.
    """
    counts = {"inserted": 0, "updated": 0, "invalid": 0, "error": 0}
    try:
        reg = registry or get_registry()
    except Exception as exc:
        _log.debug("slug registry unavailable: %s", exc)
        counts["error"] += 1
        return counts
    for platform, slug in pairs:
        try:
            outcome = reg.upsert_slug(slug, platform, source=source)
            counts[outcome if outcome in counts else "invalid"] += 1
        except Exception as exc:
            _log.debug("slug upsert failed %s/%s: %s", platform, slug, exc)
            counts["error"] += 1
    return counts


def register_slugs_from_url(url: str | None, *, source: str) -> dict[str, int]:
    """Extract + upsert slugs from a single URL. Safe no-op on junk input."""
    from jobbots.core.discovery.ats_slugs import extract_slugs_from_url

    pairs = extract_slugs_from_url(url)
    if not pairs:
        return {"inserted": 0, "updated": 0, "invalid": 0, "error": 0}
    return register_slugs(pairs, source=source)
