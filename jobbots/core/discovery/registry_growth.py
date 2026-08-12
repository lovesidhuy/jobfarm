"""Grow the ATS slug registry from seeds, queue history, and live URLs.

The board API only knows companies we have already registered.  This module
keeps the flywheel fed:

1. Seed from monorepo ``artifacts/ats_slug_registry.json`` when Mongo is thin.
2. Harvest GH/Lever/Ashby/Bamboo URLs already in ``application_queue``.
3. Register any single URL (used by harvesters + enqueue path).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

_log = logging.getLogger("discovery.registry_growth")

_ATS_PORTALS = frozenset({"greenhouse", "lever", "ashby", "bamboohr", "bamboo", "google"})


def _artifact_path() -> Path:
    raw = (os.getenv("ATS_SLUG_REGISTRY_JSON") or "").strip()
    if raw:
        return Path(raw)
    # automation_monorepo/artifacts/...
    return _MONOREPO_ROOT / "artifacts" / "ats_slug_registry.json"


def active_slug_count(registry: Any | None = None) -> int:
    try:
        from jobbots.core.discovery.slug_registry import get_registry

        reg = registry or get_registry()
        return len(reg.iter_active_slugs())
    except Exception as exc:
        _log.debug("active_slug_count failed: %s", exc)
        return 0


def seed_from_artifact(
    *,
    path: Path | None = None,
    min_active: int = 1,
    force: bool = False,
) -> dict[str, int]:
    """Upsert artifact slugs into the live registry if under-filled."""
    from jobbots.core.discovery.slug_registry import get_registry

    counts = {"inserted": 0, "updated": 0, "invalid": 0, "error": 0, "skipped": 0}
    reg = get_registry()
    active = active_slug_count(reg)
    if active >= min_active and not force:
        counts["skipped"] = active
        _log.info("Registry seed skipped (active_slugs=%d >= %d)", active, min_active)
        return counts

    art = path or _artifact_path()
    if not art.is_file():
        _log.warning("Registry seed artifact missing: %s", art)
        counts["error"] = 1
        return counts

    try:
        data = json.loads(art.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warning("Registry seed artifact unreadable: %s", exc)
        counts["error"] = 1
        return counts

    slugs = data.get("slugs") if isinstance(data, dict) else None
    if not isinstance(slugs, dict):
        _log.warning("Registry seed artifact has no slugs map")
        counts["error"] = 1
        return counts

    for _key, row in slugs.items():
        if not isinstance(row, dict):
            continue
        sid = (row.get("slug_id") or row.get("slug") or "").strip()
        plat = (row.get("platform") or "").strip().lower()
        if not sid or not plat:
            counts["invalid"] += 1
            continue
        try:
            outcome = reg.upsert_slug(
                sid,
                plat,
                source=str(row.get("discovery_source") or "artifact_seed"),
                company_name=row.get("company_name"),
            )
            if outcome in counts:
                counts[outcome] += 1
            else:
                counts["invalid"] += 1
        except Exception as exc:
            _log.debug("seed upsert failed %s/%s: %s", plat, sid, exc)
            counts["error"] += 1

    _log.info(
        "Registry seeded from %s: inserted=%d updated=%d invalid=%d error=%d (was_active=%d)",
        art,
        counts["inserted"],
        counts["updated"],
        counts["invalid"],
        counts["error"],
        active,
    )
    return counts


def grow_from_application_queue(*, limit: int = 500) -> dict[str, int]:
    """Register slugs from ATS URLs already stored in application_queue."""
    from jobbots.core.discovery.slug_registry import get_registry, register_slugs_from_url

    counts = {"urls": 0, "inserted": 0, "updated": 0, "invalid": 0, "error": 0}
    try:
        from pymongo import MongoClient
    except Exception:
        counts["error"] = 1
        return counts

    uri = (os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "mongodb://127.0.0.1:27017").strip()
    db_name = (
        (os.getenv("JOBBOTS_MONGO_DATABASE") or "").strip()
        or (os.getenv("MONGODB_DB_NAME") or "").strip()
        or "jobbots"
    )
    try:
        coll = MongoClient(uri, serverSelectionTimeoutMS=4000)[db_name]["application_queue"]
        cursor = coll.find(
            {"portal": {"$in": list(_ATS_PORTALS)}},
            {"url": 1, "destination_url": 1, "result_url": 1, "metadata": 1},
        ).limit(max(50, int(limit)))
    except Exception as exc:
        _log.warning("grow_from_application_queue mongo failed: %s", exc)
        counts["error"] = 1
        return counts

    reg = get_registry()
    for doc in cursor:
        meta = doc.get("metadata") or {}
        for key in ("destination_url", "url", "result_url"):
            url = (doc.get(key) or meta.get(key) or meta.get("destination_url") or "").strip()
            if not url or "http" not in url:
                continue
            counts["urls"] += 1
            try:
                out = register_slugs_from_url(url, source="queue_harvest")
                for k in ("inserted", "updated", "invalid", "error"):
                    counts[k] += int(out.get(k) or 0)
            except Exception as exc:
                _log.debug("queue harvest url failed: %s", exc)
                counts["error"] += 1
            break

    _log.info(
        "Registry grown from queue: urls=%d inserted=%d updated=%d",
        counts["urls"],
        counts["inserted"],
        counts["updated"],
    )
    return counts


# Curated Canada / Metro-Van-leaning boards that are under-represented vs GH/Lever.
# Dead boards fail verify on poll and go inactive — safe to over-seed.
_DEFAULT_ASHBY_SLUGS: tuple[str, ...] = (
    "ashby",
    "certa",
    "clipboard",
    "h2analytics",
    "hims-and-hers",
    "lightspeedhq",
    "maintainx",
    "moego",
    "nestmed",
    "partyhat",
    "pergolux",
    "telus-digital",
    "thinkific",
    "waabi",
    "wealthsimple",
    "applyboard",
    "clearco",
    "koho",
    "borrowell",
    "bench",
    "ritual",
    "hopper",
    "league",
    "dialogue",
    "inkblot",
    "mindbeacon",
    "frontrow",
    "figure",
    "retool",
    "linear",
    "ramp",
    "vercel",
    "notion",
    "clay",
    "posthog",
    "replicate",
    "cursor",
    "elevenlabs",
    "anthropic",
    "openai",
)

_DEFAULT_BAMBOOHR_SLUGS: tuple[str, ...] = (
    "bakertillywm",
    "benjipays3",
    "connexa",
    "creator",
    "crowemackay",
    "d3security",
    "encepta",
    "fastepp",
    "fintelconnect",
    "fraseracademy",
    "hyperhippoproductions",
    "investorcom",
    "isacybersecurity",
    "laurenservices",
    "lawsonlundell",
    "lexful",
    "macdonaldshhc",
    "orag",
    "owi",
    "panagopizza",
    "paybyphone",
    "pictonmahoney",
    "pivothr",
    "portableelectric",
    "prismengineering",
    "qehome",
    "responsebio",
    "safecare",
    "successbc",
    "svante",
    "tantalussystems",
    "tractionrec",
    "vibrantmarketing",
    "bcit",
    "vancity",
    "coastcapital",
    "icbc",
    "translink",
    "metrovancouver",
    "cityofvancouver",
    "cityofburnaby",
    "ubc",
    "sfu",
    "langara",
    "douglascollege",
    "kpu",
    "capilanou",
    "workbc",
    "lululemon",
    "mec",
    "aritzia",
    "lush",
    "well.ca",
    "shoppers",
    "london-drugs",
    "saveonfoods",
)


def seed_ashby_bamboohr_defaults(*, source: str = "platform_seed") -> dict[str, int]:
    """Upsert curated Ashby + BambooHR slugs so board_api is not GH/Lever-only."""
    from jobbots.core.discovery.slug_registry import get_registry

    reg = get_registry()
    counts = {"inserted": 0, "updated": 0, "invalid": 0, "error": 0}
    for plat, slugs in (
        ("ashby", _DEFAULT_ASHBY_SLUGS),
        ("bamboohr", _DEFAULT_BAMBOOHR_SLUGS),
    ):
        for sid in slugs:
            try:
                outcome = reg.upsert_slug(sid, plat, source=source)
                if outcome in counts:
                    counts[outcome] += 1
                else:
                    counts["invalid"] += 1
            except Exception as exc:
                _log.debug("platform seed upsert failed %s/%s: %s", plat, sid, exc)
                counts["error"] += 1
    _log.info(
        "seed_ashby_bamboohr_defaults: inserted=%d updated=%d invalid=%d error=%d",
        counts["inserted"],
        counts["updated"],
        counts["invalid"],
        counts["error"],
    )
    return counts


def ensure_registry_ready(*, min_active: int = 20) -> dict[str, Any]:
    """Seed + harvest until the registry can feed board_api productively."""
    report: dict[str, Any] = {
        "active_before": active_slug_count(),
        "seed": {},
        "queue_harvest": {},
        "ashby_bamboo_seed": {},
        "active_after": 0,
    }
    report["seed"] = seed_from_artifact(min_active=min_active, force=False)
    # Always harvest queue (cheap, grows from who we already applied to).
    report["queue_harvest"] = grow_from_application_queue(limit=800)
    # Ashby/Bamboo are chronically thin vs GH/Lever — always top them up.
    report["ashby_bamboo_seed"] = seed_ashby_bamboohr_defaults()
    # If still thin, force re-seed from artifact even if some rows exist.
    if active_slug_count() < min_active:
        report["seed_force"] = seed_from_artifact(min_active=min_active, force=True)
    report["active_after"] = active_slug_count()
    _log.info(
        "ensure_registry_ready: active %s → %s",
        report["active_before"],
        report["active_after"],
    )
    return report
