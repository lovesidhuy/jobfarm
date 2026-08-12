"""Filesystem SERP cache for discovery cells (portal|term|loc|day).

Env:
  DISCOVERY_SERP_CACHE=1 (default on) / 0 to disable
  DISCOVERY_SERP_CACHE_HOURS=18
  DISCOVERY_SERP_CACHE_DIR  (default: <monorepo>/artifacts/serp_cache)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

_log = logging.getLogger("discovery.serp_cache")


def cache_enabled() -> bool:
    return os.getenv("DISCOVERY_SERP_CACHE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _ttl_seconds() -> float:
    try:
        hours = float(os.getenv("DISCOVERY_SERP_CACHE_HOURS", "18") or "18")
    except ValueError:
        hours = 18.0
    return max(1.0, hours) * 3600.0


def _cache_dir() -> Path:
    raw = (os.getenv("DISCOVERY_SERP_CACHE_DIR") or "").strip()
    if raw:
        p = Path(raw)
    else:
        p = _MONOREPO_ROOT / "artifacts" / "serp_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cell_key(portal: str, term: str, location: str, *, day: str | None = None) -> str:
    if not day:
        day = time.strftime("%Y-%m-%d", time.gmtime())
    blob = f"{(portal or '').lower()}|{(term or '').strip().lower()}|{(location or '').strip().lower()}|{day}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def get_raw_jobs(portal: str, term: str, location: str) -> list[dict[str, Any]] | None:
    if not cache_enabled():
        return None
    path = _cache_dir() / f"{cell_key(portal, term, location)}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = float(data.get("ts") or 0)
        if time.time() - ts > _ttl_seconds():
            return None
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            _log.info("SERP cache hit %s / %r @ %r (%d jobs)", portal, term, location, len(jobs))
            return jobs
    except Exception as exc:
        _log.debug("SERP cache read failed: %s", exc)
    return None


def put_raw_jobs(portal: str, term: str, location: str, jobs: list[Any]) -> None:
    if not cache_enabled():
        return
    path = _cache_dir() / f"{cell_key(portal, term, location)}.json"
    try:
        serializable = []
        for j in jobs:
            if hasattr(j, "__dict__"):
                # dataclasses / simple objects
                try:
                    from dataclasses import asdict, is_dataclass
                    if is_dataclass(j):
                        serializable.append(asdict(j))
                        continue
                except Exception:
                    pass
            if isinstance(j, dict):
                serializable.append(j)
        path.write_text(
            json.dumps({"ts": time.time(), "portal": portal, "term": term, "location": location, "jobs": serializable}, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        _log.debug("SERP cache write failed: %s", exc)


def jobs_from_dicts(dicts: list[dict[str, Any]]):
    """Rebuild RawJob list from cache dicts."""
    from jobbots.core.discovery.contracts import RawJob
    out = []
    for d in dicts:
        try:
            out.append(RawJob(
                source_platform=d.get("source_platform") or "",
                source_job_id=d.get("source_job_id") or "",
                title=d.get("title") or "",
                company=d.get("company") or "",
                location=d.get("location") or "",
                description=d.get("description") or "",
                listing_url=d.get("listing_url") or "",
                destination_url=d.get("destination_url"),
                date_posted=d.get("date_posted"),
                easy_apply_evidence=d.get("easy_apply_evidence") or "",
                is_remote=d.get("is_remote"),
                raw_extras=d.get("raw_extras") or {},
            ))
        except Exception:
            continue
    return out
