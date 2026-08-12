"""Firecrawl fail-safe for Greenhouse/Lever discovery dorks.

Same role as ``tavily_ats``: CAPTCHA-free web search for ATS URLs when Google
CDP is empty or blocked. Prefer Firecrawl when ``FIRECRAWL_API_KEY`` is set.

Env
---
``FIRECRAWL_API_KEY``
    Required (Infisical or env).
``FIRECRAWL_ATS_ENABLED``
    Default ``1``.
``FIRECRAWL_ATS_MAX_RESULTS``
    Per-query max (default ``12``, cap 20).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.providers.google_cdp_provider import (
    build_ats_query_variants,
    build_google_web_ats_query,
    canonicalize_ats_url,
    is_greenhouse_or_lever,
    serp_passes_metro_van_canada,
    serp_title_matches_search_intent,
)
from jobbots.core.firecrawl_client import firecrawl_enabled, firecrawl_search, firecrawl_api_base

_log = logging.getLogger("discovery.providers.firecrawl_ats")

_ATS_DOMAINS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "lever.co",
    "grnh.se",
)


def _max_results(default: int = 8) -> int:
    """Per-query cap. Cloud student credits: keep default low (env override)."""
    try:
        n = int(os.getenv("FIRECRAWL_ATS_MAX_RESULTS", str(default)) or default)
    except ValueError:
        n = default
    # Hard cap 10 on cloud-style keys to avoid burning student credits.
    hard = 10
    try:
        from jobbots.core.firecrawl_client import firecrawl_api_key

        key = firecrawl_api_key() or ""
        if key.startswith("fc-"):
            hard = 10
        else:
            hard = 20
    except Exception:
        hard = 10
    return max(1, min(n, hard))


def _company_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse

        path = (urlparse(url).path or "").strip("/")
        parts = [p for p in path.split("/") if p]
        if parts:
            return parts[0].replace("-", " ").title()
    except Exception:
        pass
    return "Unknown"


def firecrawl_hits_to_raw_jobs(
    hits: list[dict[str, str]],
    *,
    term: str,
    location: str,
    mode: str = "firecrawl_web",
) -> list[RawJob]:
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for hit in hits:
        raw_url = hit.get("url") or ""
        apply_url = canonicalize_ats_url(raw_url)
        if not apply_url or apply_url in seen:
            continue
        if not is_greenhouse_or_lever(apply_url) and "grnh.se" not in (apply_url or ""):
            if not re.search(r"(greenhouse\.io|lever\.co|grnh\.se|gh\.io)", apply_url or "", re.I):
                continue
        seen.add(apply_url)
        title = (hit.get("title") or "").strip()
        # Strip common SERP noise prefixes
        title = re.sub(r"(?i)^job application for\s+", "", title).strip()
        snippet = (hit.get("content") or "")[:400]
        loc = (location or "Vancouver, BC").strip()
        blob = f"{title} {snippet}"
        # Drop career-board shells that never mention the search role.
        if (
            not title
            or re.match(r"(?i)^(jobs?|careers?|current (job )?openings?)\s*(at|with)?\b", title)
            or re.match(r"(?i)^apply for a career\b", title)
        ):
            from jobbots.core.discovery.providers.tavily_ats import _term_tokens_in_blob

            if not _term_tokens_in_blob(term, blob):
                continue
        if title or snippet:
            if not serp_passes_metro_van_canada(title=title, snippet=blob):
                continue
            if title and not serp_title_matches_search_intent(title=title, search_term=term):
                # Allow "Jobs at X" only when snippet already matched term tokens above.
                if not re.match(r"(?i)^(jobs?|careers?)\b", title):
                    continue
        company = _company_from_url(apply_url)
        m = re.match(r"(?i).+?\s+at\s+(.+)$", title)
        if m:
            company = m.group(1).strip() or company
        jobs.append(
            RawJob(
                source_platform="google",
                source_job_id=apply_url,
                title=title or "Unknown",
                company=company or "Unknown",
                location=loc,
                description=snippet,
                listing_url=apply_url,
                destination_url=apply_url,
                date_posted=None,
                easy_apply_evidence="",
                is_remote=bool(re.search(r"\bremote\b", blob, re.I)) or None,
                raw_extras={
                    "search_term": term,
                    "site": "firecrawl",
                    "google_mode": mode,
                    "ats_filter": "greenhouse_or_lever",
                    "discovered_by": "firecrawl_ats",
                    "firecrawl_query": build_google_web_ats_query(term, location),
                    "search_location": location,
                },
            )
        )
    return jobs


def _ats_anchor_location(locations: list[str] | None) -> str:
    locs = [((loc or "").strip()) for loc in (locations or []) if (loc or "").strip()]
    if not locs:
        return "Vancouver, BC"
    for loc in locs:
        if "vancouver" in loc.lower() and "wa" not in loc.lower().split(","):
            return loc
    return locs[0]


def discover_ats_via_firecrawl(request: DiscoveryRequest) -> list[RawJob]:
    if not firecrawl_enabled():
        _log.info("Firecrawl ATS discovery disabled or missing FIRECRAWL_API_KEY")
        return []

    all_jobs: list[RawJob] = []
    per = min(int(request.max_results_per_term or 12), _max_results())
    location = _ats_anchor_location(list(request.locations or []))
    # Default 1 variant on cloud to save credits; set FIRECRAWL_ATS_VARIANTS=2 for more.
    try:
        default_variants = "1"
        from jobbots.core.firecrawl_client import firecrawl_api_key

        if not (firecrawl_api_key() or "").startswith("fc-"):
            default_variants = "2"
        n_variants = max(1, min(int(os.getenv("FIRECRAWL_ATS_VARIANTS", default_variants) or default_variants), 3))
    except ValueError:
        n_variants = 1

    for term in request.search_terms or []:
        variants = build_ats_query_variants(term, location)[:n_variants]
        hits: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for q in variants:
            batch = firecrawl_search(q, limit=per, include_domains=list(_ATS_DOMAINS[:4]))
            for hit in batch:
                url = (hit.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                hits.append(hit)
            if len(hits) >= per and q == variants[0]:
                break
        jobs = firecrawl_hits_to_raw_jobs(
            hits, term=term, location=location, mode="firecrawl_web"
        )
        _log.info(
            "Firecrawl term=%r loc=%r variants=%d hits=%d jobs=%d",
            term,
            location,
            len(variants),
            len(hits),
            len(jobs),
        )
        all_jobs.extend(jobs)

    seen: set[str] = set()
    unique: list[RawJob] = []
    for job in all_jobs:
        key = canonicalize_ats_url(job.destination_url or job.listing_url)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(job)
    _log.info(
        "Firecrawl ATS total unique jobs: %d (base=%s)",
        len(unique),
        firecrawl_api_base(),
    )
    # Flywheel: register board slugs for the ats_board_api direct poller.
    try:
        from jobbots.core.discovery.slug_registry import register_slugs_from_url

        for job in unique:
            register_slugs_from_url(
                job.destination_url or job.listing_url, source="firecrawl"
            )
    except Exception:
        pass
    return unique


class FirecrawlATSProvider:
    name = "firecrawl_ats"
    supported_platforms = ["google", "firecrawl"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        return discover_ats_via_firecrawl(request)
