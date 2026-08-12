"""Footprint Discovery — surface unknown small-company GH/Lever boards.

Standard company databases miss stealth startups and small local businesses.
Their Greenhouse/Lever footprint leaks structurally though: embedded board
subdomains, inline ``jobs.lever.co`` config objects, and indexed board pages.
This module generates geo-anchored *structural* dorks (no job-title terms)
and mines the returned URLs/snippets for slugs, verifying candidates with a
live public-API hit before they join the registry as
``discovery_source="footprint_sensor"``.

Query variants (per the search-engineering spec)
------------------------------------------------
A. Custom-domain embedded Greenhouse boards
   ``"{city}" site:*.greenhouse.io -site:boards.greenhouse.io -site:www.greenhouse.io``
B. Lever inline configuration objects on small-business sites
   ``"{city}" intext:"jobs.lever.co" -site:lever.co``
C. Direct local board footprints with About/Team signals
   ``"{city}" site:boards.greenhouse.io OR site:jobs.lever.co "About Us" "Team"``

Slug surfaces handled
---------------------
- path slugs (``boards.greenhouse.io/{slug}/...``)
- subdomain slugs (``{slug}.greenhouse.io``) — variant A's whole point
- snippet-mined slugs when a custom corporate domain merely *mentions*
  ``jobs.lever.co/{slug}`` — regex from page text via
  ``ats_slugs.extract_slugs_from_text``; optionally one quick page fetch
  (``FOOTPRINT_PAGE_FETCH=1``) to pull the pattern from raw HTML source.

Feedback loop (immutable)
-------------------------
Every candidate slug is verified against the public JSON API. Only verified
slugs are upserted, tagged ``footprint_sensor``. Once registered, the
``ats_board_api`` poller owns them forever.

Env
---
``FOOTPRINT_SENSOR_ENABLED``   default ``1``
``FOOTPRINT_MAX_RESULTS``      per-query cap (default ``10``, hard cap 15)
``FOOTPRINT_VERIFY_CONCURRENCY`` slug verification workers (default ``8``)
``FOOTPRINT_PAGE_FETCH``       ``1`` to fetch variant-B custom domains for
                               source-regex mining (default ``0`` — snippet only)
"""
from __future__ import annotations

import logging
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from jobbots.core.discovery.ats_slugs import (
    PLATFORM_ASHBY,
    PLATFORM_BAMBOOHR,
    PLATFORM_GREENHOUSE,
    PLATFORM_LEVER,
    extract_slugs_from_text,
    extract_slugs_from_url,
)
from jobbots.core.discovery.slug_registry import register_slugs

_log = logging.getLogger("discovery.footprint_sensor")

_GH_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_API = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_BAMBOOHR_API = "https://{slug}.bamboohr.com/careers/list"

# Metro-Van geo anchors for the {city} variable.
DEFAULT_CITY_PACK: tuple[str, ...] = (
    "Vancouver", "Burnaby", "Surrey", "Richmond", "Coquitlam",
    "North Vancouver", "New Westminster", "Langley",
)


def _enabled() -> bool:
    return os.getenv("FOOTPRINT_SENSOR_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _max_results() -> int:
    try:
        return max(1, min(int(os.getenv("FOOTPRINT_MAX_RESULTS", "10") or "10"), 15))
    except ValueError:
        return 10


def _verify_workers() -> int:
    try:
        return max(1, min(int(os.getenv("FOOTPRINT_VERIFY_CONCURRENCY", "8") or "8"), 16))
    except ValueError:
        return 8


def _page_fetch_enabled() -> bool:
    return os.getenv("FOOTPRINT_PAGE_FETCH", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

def build_footprint_queries(cities: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Generate the A/B/C structural dorks for each geo anchor."""
    pack = list(cities or DEFAULT_CITY_PACK)
    queries: list[str] = []
    for city in pack:
        city = (city or "").strip()
        if not city:
            continue
        # Variant A — custom-domain embedded Greenhouse boards.
        queries.append(
            f'"{city}" site:*.greenhouse.io '
            "-site:boards.greenhouse.io -site:www.greenhouse.io"
        )
        # Variant B — Lever inline config objects on small-business sites.
        queries.append(f'"{city}" intext:"jobs.lever.co" -site:lever.co')
        # Variant C — direct local board footprints w/ About/Team signals.
        queries.append(
            f'"{city}" site:boards.greenhouse.io OR site:jobs.lever.co '
            '"About Us" "Team"'
        )
        # Variant D — Ashby public boards (jobs.ashbyhq.com/{slug}).
        queries.append(f'"{city}" site:jobs.ashbyhq.com')
        # Variant E — BambooHR careers subdomains.
        queries.append(f'"{city}" site:bamboohr.com/careers')
    return queries


# ---------------------------------------------------------------------------
# Slug mining from search hits
# ---------------------------------------------------------------------------

def _fetch_page_text(url: str, *, timeout: float = 6.0, max_bytes: int = 120_000) -> str:
    """Quick raw-HTML pull for variant-B custom domains (source-regex mining)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(max_bytes).decode("utf-8", errors="replace")
    except Exception as exc:
        _log.debug("footprint page fetch failed %s: %s", url[:80], exc)
        return ""


def mine_slugs_from_hits(
    hits: list[dict[str, str]],
    *,
    page_fetch: bool | None = None,
) -> list[tuple[str, str]]:
    """Extract ``(platform, slug)`` candidates from SERP rows.

    Order: URL structure first, then snippet text, then (optionally) a quick
    page fetch for custom-domain hits that mentioned lever/greenhouse but
    exposed no slug yet.
    """
    fetch = _page_fetch_enabled() if page_fetch is None else page_fetch
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(pairs: list[tuple[str, str]]) -> None:
        for p, s in pairs:
            if (p, s) not in seen:
                seen.add((p, s))
                found.append((p, s))

    for hit in hits:
        url = (hit.get("url") or "").strip()
        snippet = (hit.get("content") or hit.get("snippet") or "").strip()
        title = (hit.get("title") or "").strip()

        _add(extract_slugs_from_url(url))
        _add(extract_slugs_from_text(f"{title}\n{snippet}"))

        if fetch and url and not any(
            p in {
                PLATFORM_GREENHOUSE,
                PLATFORM_LEVER,
                PLATFORM_ASHBY,
                PLATFORM_BAMBOOHR,
            }
            for p, _ in found
        ):
            # Custom domain: slug only in page source.
            snip_l = snippet.lower()
            if any(
                token in snip_l
                for token in (
                    "lever.co",
                    "greenhouse.io",
                    "ashbyhq.com",
                    "bamboohr.com",
                )
            ):
                html = _fetch_page_text(url)
                if html:
                    _add(extract_slugs_from_text(html))

    return found


# ---------------------------------------------------------------------------
# Verification (the immutable feedback loop gate)
# ---------------------------------------------------------------------------

def verify_slug(platform: str, slug: str, *, timeout: float = 8.0) -> bool:
    """Confirm a board exists via its public JSON API (200 = real)."""
    if platform == PLATFORM_GREENHOUSE:
        url = _GH_API.format(slug=slug)
    elif platform == PLATFORM_LEVER:
        url = f"{_LEVER_API.format(slug=slug)}?mode=json"
    elif platform == PLATFORM_ASHBY:
        url = _ASHBY_API.format(slug=slug)
    elif platform == PLATFORM_BAMBOOHR:
        url = _BAMBOOHR_API.format(slug=slug)
    else:
        return False
    req = urllib.request.Request(url, headers={"User-Agent": "footprint-sensor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def verify_and_register(
    candidates: list[tuple[str, str]],
    *,
    source: str = "footprint_sensor",
) -> dict[str, Any]:
    """Verify candidates concurrently; upsert verified slugs only."""
    unique = list(dict.fromkeys(candidates))
    verified: list[tuple[str, str]] = []
    if not unique:
        return {"candidates": 0, "verified": 0, "registered": {"inserted": 0, "updated": 0, "invalid": 0, "error": 0}}

    with ThreadPoolExecutor(max_workers=_verify_workers()) as pool:
        futs = {pool.submit(verify_slug, p, s): (p, s) for p, s in unique}
        for fut in as_completed(futs):
            pair = futs[fut]
            try:
                if fut.result():
                    verified.append(pair)
            except Exception:
                continue

    counts = register_slugs(verified, source=source)
    _log.info(
        "footprint sensor: candidates=%d verified=%d registered=%s",
        len(unique), len(verified), counts,
    )
    return {"candidates": len(unique), "verified": len(verified), "registered": counts}


# ---------------------------------------------------------------------------
# Driver — plug into a search backend (Tavily or Firecrawl)
# ---------------------------------------------------------------------------

def run_footprint_sensor(
    search_fn: Any,
    *,
    cities: list[str] | tuple[str, ...] | None = None,
    include_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Execute all footprint queries through ``search_fn`` and process hits.

    ``search_fn(query, max_results) -> list[{title, url, content}]`` — both
    ``tavily_ats.tavily_search`` and ``firecrawl_client.firecrawl_search``
    adapt to this shape (Firecrawl via a small lambda).
    """
    if not _enabled():
        return {"enabled": False, "candidates": 0, "verified": 0}

    queries = build_footprint_queries(cities)
    all_hits: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for q in queries:
        try:
            batch = search_fn(q, max_results=_max_results())
        except TypeError:
            batch = search_fn(q, _max_results())
        except Exception as exc:
            _log.debug("footprint query failed %r: %s", q[:80], exc)
            continue
        for hit in batch or []:
            url = (hit.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            all_hits.append(hit)

    candidates = mine_slugs_from_hits(all_hits)
    result = verify_and_register(candidates, source="footprint_sensor")
    result["enabled"] = True
    result["queries"] = len(queries)
    result["hits"] = len(all_hits)
    return result
