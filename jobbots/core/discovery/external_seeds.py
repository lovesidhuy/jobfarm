"""Controlled importers for external ATS board seed directories.

External directories are *lead sources*, not trusted job feeds.  This module
imports only company board identifiers into the slug registry; the existing
``ats_board_api`` provider subsequently verifies a board against the live
public ATS API before any job can reach the application queue.

The Feashliaa job-board-aggregator company lists are intentionally imported in
bounded batches.  Activating its full catalogue at once would turn a daily
discovery run into an unsafe burst of requests to each ATS.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.request import Request, urlopen

from jobbots.core.discovery.ats_slugs import SUPPORTED_PLATFORMS, clean_slug


FEASHLIAA_SOURCE = "feashliaa_job_board_aggregator"
FEASHLIAA_REPOSITORY = "https://github.com/Feashliaa/job-board-aggregator"
FEASHLIAA_RAW_URLS: dict[str, str] = {
    platform: (
        "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/"
        f"main/data/{platform}_companies.json"
    )
    for platform in SUPPORTED_PLATFORMS
}


def parse_slug_list(payload: str | bytes) -> list[str]:
    """Return valid, de-duplicated board slugs from one upstream JSON list."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of board slugs")

    out: list[str] = []
    seen: set[str] = set()
    for raw in data:
        # The upstream contract is a string list.  Reject objects rather than
        # guessing at a schema and silently registering a wrong board.
        if not isinstance(raw, str):
            continue
        slug = clean_slug(raw)
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def fetch_feashliaa_lists(*, timeout: float = 30.0) -> dict[str, list[str]]:
    """Download the four public company lists with no dependency on requests."""
    fetched: dict[str, list[str]] = {}
    for platform, url in FEASHLIAA_RAW_URLS.items():
        request = Request(url, headers={"User-Agent": "jobbots-ats-seed-importer/1.0"})
        with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed HTTPS URLs
            fetched[platform] = parse_slug_list(response.read())
    return fetched


def seed_feashliaa_lists(
    registry: Any,
    lists: Mapping[str, Iterable[str]],
    *,
    per_platform: int = 250,
    offset: int = 0,
    source: str = FEASHLIAA_SOURCE,
) -> dict[str, Any]:
    """Upsert one bounded, deterministic batch from each allowed ATS list.

    ``offset`` is shared across platforms, making it safe for a scheduled
    rotation (for example ``0``, ``250``, ``500``).  Existing slugs are only
    touched, preserving the first discovery provenance in the registry.
    """
    if per_platform < 1:
        raise ValueError("per_platform must be at least 1")
    if offset < 0:
        raise ValueError("offset cannot be negative")

    totals = {"inserted": 0, "updated": 0, "invalid": 0, "error": 0}
    platform_reports: dict[str, dict[str, int]] = {}
    for platform in SUPPORTED_PLATFORMS:
        values = lists.get(platform, ())
        # Apply the same parsing/validation to supplied fixtures and network
        # data.  This lets the importer tolerate duplicate or malformed rows.
        normalised: list[str] = []
        seen: set[str] = set()
        for value in values:
            slug = clean_slug(value if isinstance(value, str) else "")
            if slug and slug not in seen:
                seen.add(slug)
                normalised.append(slug)

        batch = normalised[offset : offset + per_platform]
        report = {"available": len(normalised), "selected": len(batch), **totals}
        # The totals copy above is zero at this point, but avoid sharing it.
        for key in totals:
            report[key] = 0
        for slug in batch:
            try:
                outcome = registry.upsert_slug(slug, platform, source=source)
            except Exception:
                report["error"] += 1
                totals["error"] += 1
                continue
            if outcome in totals:
                report[outcome] += 1
                totals[outcome] += 1
            else:
                report["invalid"] += 1
                totals["invalid"] += 1
        platform_reports[platform] = report

    return {
        "source": source,
        "repository": FEASHLIAA_REPOSITORY,
        "offset": offset,
        "per_platform": per_platform,
        "platforms": platform_reports,
        "totals": totals,
    }
