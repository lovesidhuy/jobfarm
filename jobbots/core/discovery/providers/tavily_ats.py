"""Tavily API fail-safe for Greenhouse/Lever discovery dorks.

Used when Google CDP web/fallback paths hit captcha / empty SERPs.
Passes the same site: dorks as ``google_cdp_provider.build_google_web_ats_query``
and returns clean GH/Lever apply URLs (no browser, no CapMonster).

Env
---
``TAVILY_API_KEY``
    Required (Infisical or env).
``TAVILY_ATS_ENABLED``
    Default ``1``. Set ``0`` to disable.
``TAVILY_ATS_MAX_RESULTS``
    Per-query max results (default ``15``, capped 20).
``TAVILY_ATS_SEARCH_DEPTH``
    ``basic`` or ``advanced`` (default ``advanced`` — Premium).
``TAVILY_ATS_TIMEOUT_SECONDS``
    HTTP timeout (default ``45``).
``TAVILY_ATS_TIME_RANGE``
    Optional: ``day`` / ``week`` / ``month`` / ``year``. Default ``month``.
``TAVILY_ATS_PARALLEL``
    Concurrent term workers (default ``4``, cap 8).
``TAVILY_ATS_VARIANTS``
    Query variants per term (default ``2``: metro dork + Canada remote; max 3).
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

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

_log = logging.getLogger("discovery.providers.tavily_ats")

_TAVILY_URL = "https://api.tavily.com/search"
_ATS_DOMAINS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "lever.co",
    "grnh.se",
    "jobs.ashbyhq.com",
    "ashbyhq.com",
    "bamboohr.com",
)


def _truthy(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on", "y"}


def tavily_enabled() -> bool:
    if not _truthy(os.getenv("TAVILY_ATS_ENABLED"), default=True):
        return False
    return bool(_tavily_api_key())


def _tavily_api_key() -> str:
    try:
        from jobbots.core.secret_manager import get_secret

        key = (get_secret("TAVILY_API_KEY") or "").strip()
    except Exception:
        key = ""
    if not key:
        key = (os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY_KEY") or "").strip()
    return key


def _max_results(default: int = 15) -> int:
    try:
        n = int(os.getenv("TAVILY_ATS_MAX_RESULTS", str(default)) or default)
    except ValueError:
        n = default
    return max(1, min(n, 20))


def _timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("TAVILY_ATS_TIMEOUT_SECONDS", "45") or "45"))
    except ValueError:
        return 45.0


def _search_depth() -> str:
    # Premium plan: advanced is higher recall for niche site: dorks.
    raw = (os.getenv("TAVILY_ATS_SEARCH_DEPTH") or "advanced").strip().lower()
    return raw if raw in {"basic", "advanced"} else "advanced"


def _time_range() -> str | None:
    """Map env / freshness into Tavily ``time_range`` (day|week|month|year).

    Default is unrestricted (higher recall for sparse Metro-Van GH/Lever).
    Set ``TAVILY_ATS_TIME_RANGE=month`` to prefer fresher SERP pages.
    """
    raw = (os.getenv("TAVILY_ATS_TIME_RANGE") or "none").strip().lower()
    if raw in {"", "0", "none", "off", "all"}:
        return None
    aliases = {
        "d": "day",
        "day": "day",
        "w": "week",
        "week": "week",
        "m": "month",
        "month": "month",
        "y": "year",
        "year": "year",
    }
    return aliases.get(raw)


def _parallel_workers() -> int:
    try:
        n = int(os.getenv("TAVILY_ATS_PARALLEL", "4") or "4")
    except ValueError:
        n = 4
    return max(1, min(n, 8))


def _variant_count() -> int:
    try:
        n = int(os.getenv("TAVILY_ATS_VARIANTS", "2") or "2")
    except ValueError:
        n = 2
    return max(1, min(n, 3))


def _ats_anchor_location(locations: list[str] | None) -> str:
    """One region anchor — query builder expands metro pack (avoid city×term waste)."""
    locs = [((loc or "").strip()) for loc in (locations or []) if (loc or "").strip()]
    if not locs:
        return "Vancouver, BC"
    for loc in locs:
        if "vancouver" in loc.lower() and "wa" not in loc.lower().split(","):
            return loc
    return locs[0]


def _company_from_url(url: str) -> str:
    try:
        path = urlparse(url).path.strip("/").split("/")
        if not path:
            return ""
        # boards.greenhouse.io/{board}/jobs/{id}
        # jobs.lever.co/{company}/{uuid}
        return path[0].replace("-", " ").strip()
    except Exception:
        return ""


def _title_from_tavily(row: dict[str, Any]) -> str:
    title = (row.get("title") or "").strip()
    # "Job Application for X at Y" → X
    m = re.match(r"(?i)job application for\s+(.+?)\s+at\s+(.+)$", title)
    if m:
        return m.group(1).strip()
    # "Company - Title" / "Title @ Company"
    m = re.match(r"^(.+?)\s+[-–|]\s+(.+)$", title)
    if m and len(m.group(1)) < 40 and len(m.group(2)) > 8:
        # often "ARC'TERYX - Data Analyst" or "Magna - IT Support"
        return m.group(2).strip() if " at " not in m.group(2).lower() else m.group(1).strip()
    # "Jobs at Company" / bare board pages → empty title (filtered later if bare)
    if re.match(r"(?i)^jobs?\s+at\s+", title):
        return ""
    return title[:200]


def _location_from_tavily(
    row: dict[str, Any],
    *,
    title: str,
    search_location: str,
) -> str:
    """Prefer location tokens in title/content over the search-centre stamp."""
    content = (row.get("content") or row.get("snippet") or "")[:600]
    blob = f"{title}\n{content}"
    # Explicit "City, ST/BC" patterns
    m = re.search(
        r"\b("
        r"(?:North\s+)?Vancouver|Burnaby|Surrey|Richmond|Coquitlam|"
        r"New\s+Westminster|Langley|Delta|Port\s+Moody|Port\s+Coquitlam|"
        r"Maple\s+Ridge|White\s+Rock|West\s+Vancouver"
        r")\s*,\s*(?:BC|British\s+Columbia|Canada)\b",
        blob,
        re.I,
    )
    if m:
        return m.group(0).strip()
    m = re.search(r"\b([\w\s]+?,\s*(?:BC|British Columbia|ON|AB|QC|Canada))\b", blob, re.I)
    if m:
        return m.group(1).strip()[:80]
    # Foreign city in title → use that so geo policy rejects
    m = re.search(
        r"\b(Sydney|Melbourne|Dublin|London|Singapore|Tokyo|New York|San Francisco|Seattle)\b",
        title,
        re.I,
    )
    if m:
        return m.group(1)
    # Remote signals
    if re.search(r"\b(remote|work from home|wfh)\b", blob, re.I):
        if re.search(r"\b(canada|canadian)\b", blob, re.I):
            return "Remote, Canada"
        return "Remote"
    return (search_location or "Vancouver, BC").strip()


def tavily_search(
    query: str,
    *,
    max_results: int | None = None,
    include_domains: list[str] | None = None,
    time_range: str | None = None,
) -> list[dict[str, str]]:
    """Run one Tavily search; return ``[{title, url, content}, ...]`` (no secrets logged)."""
    key = _tavily_api_key()
    if not key:
        _log.warning("Tavily ATS search skipped — no TAVILY_API_KEY")
        return []
    q = (query or "").strip()
    if not q:
        return []
    payload: dict[str, Any] = {
        "api_key": key,
        "query": q,
        "search_depth": _search_depth(),
        "max_results": max_results or _max_results(),
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    domains = include_domains if include_domains is not None else list(_ATS_DOMAINS[:4])
    if domains:
        payload["include_domains"] = domains
    tr = time_range if time_range is not None else _time_range()
    if tr:
        payload["time_range"] = tr

    req = urllib.request.Request(
        _TAVILY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
    except urllib.error.HTTPError as exc:
        _log.warning("Tavily HTTP %s for query=%r", exc.code, q[:80])
        return []
    except Exception as exc:
        _log.warning("Tavily search failed: %s", exc)
        return []

    out: list[dict[str, str]] = []
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = (row.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "title": (row.get("title") or "").strip(),
                "url": url,
                "content": (row.get("content") or row.get("snippet") or "").strip(),
            }
        )
    _log.info("Tavily query=%r → %d raw results", q[:100], len(out))
    return out


def _is_board_noise_title(title: str) -> bool:
    """True for career-board shells Tavily often returns instead of role titles."""
    t = (title or "").strip()
    if not t or t.lower() in {"unknown", "search page", "careers", "jobs"}:
        return True
    if re.match(
        r"(?i)^("
        r"jobs?|careers?|current (job )?openings?|open positions|"
        r"explore our open|job listings?|search page|"
        r".+\s+careers?|.*job listings?"
        r")\s*(at|with|and)?\b",
        t,
    ):
        return True
    if re.match(r"(?i)^apply for a career\b", t):
        return True
    if re.match(r"(?i)^page_title\b", t):
        return True
    return False


_WEAK_TERM_TOKENS = frozenset(
    {
        "support",
        "analyst",
        "engineer",
        "specialist",
        "technician",
        "admin",
        "administrator",
        "junior",
        "level",
        "entry",
        "intern",
        "student",
        "co",
        "op",
    }
)


def _term_tokens_in_blob(term: str, blob: str) -> bool:
    """Require meaningful search-term signal in title/snippet.

    Empty-title career pages often contain generic words like \"support\";
    demand a strong token (qa, desk, desktop, …) or ≥2 term tokens.
    """
    tokens = [tok for tok in re.split(r"\W+", (term or "").lower()) if len(tok) > 2]
    if not tokens:
        return True
    text = (blob or "").lower()
    hits = [tok for tok in tokens if tok in text]
    if not hits:
        return False
    if any(tok not in _WEAK_TERM_TOKENS for tok in hits):
        return True
    return len(hits) >= 2


def tavily_hits_to_raw_jobs(
    hits: list[dict[str, str]],
    *,
    term: str,
    location: str,
    mode: str = "tavily_web",
) -> list[RawJob]:
    """Canonicalize + geo/intent filter Tavily rows into ``RawJob``s."""
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for hit in hits:
        raw_url = hit.get("url") or ""
        apply_url = canonicalize_ats_url(raw_url)
        if not apply_url or apply_url in seen:
            continue
        if not is_greenhouse_or_lever(apply_url) and "grnh.se" not in apply_url:
            # canonicalize may keep grnh.se short links; allow those
            if not re.search(r"(greenhouse\.io|lever\.co|grnh\.se|gh\.io)", apply_url, re.I):
                continue
        seen.add(apply_url)
        title = _title_from_tavily(hit) or ""
        snippet = (hit.get("content") or "")[:400]
        # Drop career-board shells / empty titles that do not mention the role.
        if _is_board_noise_title(title) and not _term_tokens_in_blob(term, f"{title} {snippet}"):
            _log.debug("Tavily skip board noise: %s | %s", (title or "")[:60], apply_url)
            continue
        loc = _location_from_tavily(hit, title=title, search_location=location)
        # Geo check uses only title+snippet evidence — never the search-centre
        # stamp (that was laundering US remote as Vancouver).
        if title or snippet:
            if not serp_passes_metro_van_canada(title=title, snippet=snippet):
                _log.debug("Tavily skip non-metro: %s | %s", title[:60], apply_url)
                continue
            if title and not _is_board_noise_title(title) and not serp_title_matches_search_intent(
                title=title, search_term=term
            ):
                _log.debug("Tavily skip intent: %s | term=%r", title[:60], term)
                continue
        # Company-name-as-title (Cloudflare @ cloudflare) is not a role.
        company_guess = _company_from_url(apply_url)
        if (
            title
            and company_guess
            and title.lower().replace(" ", "") == company_guess.lower().replace(" ", "")
            and not _term_tokens_in_blob(term, snippet)
        ):
            _log.debug("Tavily skip company-as-title: %s | %s", title[:60], apply_url)
            continue
        company = company_guess
        # Parse company from "Job Application for X at Y"
        raw_title = (hit.get("title") or "").strip()
        m = re.match(r"(?i)job application for\s+.+?\s+at\s+(.+)$", raw_title)
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
                is_remote=bool(re.search(r"\bremote\b", loc, re.I)) or None,
                raw_extras={
                    "search_term": term,
                    "site": "tavily",
                    "google_mode": mode,
                    "ats_filter": "greenhouse_or_lever",
                    "discovered_by": "tavily_ats",
                    "tavily_query": build_google_web_ats_query(term, location),
                    "search_location": location,
                },
            )
        )
    return jobs


def _fetch_ats_page_title(url: str, *, timeout: float = 8.0) -> str:
    """Lightweight HTML ``<title>`` fetch for GH/Lever job pages (no browser)."""
    u = (url or "").strip()
    if not u:
        return ""
    req = urllib.request.Request(
        u,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(80000)
        html = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    # Reuse Tavily title normalizer shape
    return _title_from_tavily({"title": title}) or title[:200]


def _enrich_job_titles(jobs: list[RawJob], *, term: str) -> list[RawJob]:
    """Optionally resolve empty/board-noise titles via page ``<title>``."""
    if not _truthy(os.getenv("TAVILY_ATS_ENRICH_TITLES"), default=True):
        return jobs
    try:
        max_fetch = max(0, min(int(os.getenv("TAVILY_ATS_ENRICH_MAX", "12") or "12"), 30))
    except ValueError:
        max_fetch = 12
    if max_fetch <= 0 or not jobs:
        return jobs

    def _needs_title(j: RawJob) -> bool:
        t = (j.title or "").strip()
        if _is_board_noise_title(t) or t.lower() == "unknown":
            return True
        company = (j.company or "").strip()
        if company and t.lower().replace(" ", "") == company.lower().replace(" ", ""):
            return True
        return False

    need_idx = [i for i, j in enumerate(jobs) if _needs_title(j)][:max_fetch]
    if not need_idx:
        return jobs

    def _one(i: int) -> tuple[int, str]:
        url = jobs[i].destination_url or jobs[i].listing_url or ""
        return i, _fetch_ats_page_title(url)

    drop: set[int] = set()
    enriched = 0
    with ThreadPoolExecutor(max_workers=min(4, len(need_idx))) as pool:
        futs = [pool.submit(_one, i) for i in need_idx]
        for fut in as_completed(futs):
            try:
                i, title = fut.result()
            except Exception:
                continue
            if not title or _is_board_noise_title(title):
                # Fetch failed / still board shell — keep URL for apply path
                # (already passed snippet term-token filter).
                continue
            if not serp_title_matches_search_intent(title=title, search_term=term):
                drop.add(i)
                continue
            job = jobs[i]
            jobs[i] = replace(
                job,
                title=title,
                raw_extras={**(job.raw_extras or {}), "title_enriched": True},
            )
            enriched += 1

    out = [j for i, j in enumerate(jobs) if i not in drop]
    if enriched or drop:
        _log.info(
            "Tavily title enrich: resolved=%d dropped=%d kept=%d",
            enriched,
            len(drop),
            len(out),
        )
    return out


def _discover_term_via_tavily(
    term: str,
    *,
    location: str,
    max_results: int,
    n_variants: int,
) -> list[RawJob]:
    """Run query variants for one term; return filtered RawJobs."""
    variants = build_ats_query_variants(term, location)[:n_variants]
    hits: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for q in variants:
        batch = tavily_search(q, max_results=max_results)
        for hit in batch:
            url = (hit.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            hits.append(hit)
        # If first variant already rich, skip remaining to save credits
        if len(hits) >= max_results and q == variants[0]:
            break
    jobs = tavily_hits_to_raw_jobs(
        hits, term=term, location=location, mode="tavily_web"
    )
    jobs = _enrich_job_titles(jobs, term=term)
    _log.info(
        "Tavily term=%r loc=%r variants=%d hits=%d jobs=%d",
        term,
        location,
        len(variants),
        len(hits),
        len(jobs),
    )
    return jobs


def discover_ats_via_tavily(request: DiscoveryRequest) -> list[RawJob]:
    """Run Tavily dorks for each term (one geo anchor + variants); unique GH/Lever jobs."""
    if not tavily_enabled():
        _log.info("Tavily ATS discovery disabled or missing key")
        return []

    terms = [t for t in (request.search_terms or []) if (t or "").strip()]
    if not terms:
        return []

    # Collapse 8 metro cities → one anchor; query expands metro OR pack.
    location = _ats_anchor_location(list(request.locations or []))
    per = min(int(request.max_results_per_term or 15), _max_results())
    n_variants = _variant_count()
    workers = min(_parallel_workers(), len(terms))

    all_jobs: list[RawJob] = []
    if workers <= 1 or len(terms) == 1:
        for term in terms:
            all_jobs.extend(
                _discover_term_via_tavily(
                    term, location=location, max_results=per, n_variants=n_variants
                )
            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(
                    _discover_term_via_tavily,
                    term,
                    location=location,
                    max_results=per,
                    n_variants=n_variants,
                ): term
                for term in terms
            }
            for fut in as_completed(futs):
                term = futs[fut]
                try:
                    all_jobs.extend(fut.result())
                except Exception as exc:
                    _log.warning("Tavily term=%r failed: %s", term, exc)

    # Dedup by destination
    seen: set[str] = set()
    unique: list[RawJob] = []
    for job in all_jobs:
        key = canonicalize_ats_url(job.destination_url or job.listing_url)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(job)
    _log.info(
        "Tavily ATS total unique jobs: %d (terms=%d anchor=%r depth=%s time_range=%s)",
        len(unique),
        len(terms),
        location,
        _search_depth(),
        _time_range(),
    )
    # Flywheel: register board slugs for the ats_board_api direct poller.
    try:
        from jobbots.core.discovery.slug_registry import register_slugs_from_url

        for job in unique:
            register_slugs_from_url(
                job.destination_url or job.listing_url, source="tavily"
            )
    except Exception:
        pass
    # Footprint sensor: structural dorks to discover unknown small-company
    # boards (verified slugs are registered as footprint_sensor).
    try:
        from jobbots.core.discovery.footprint_sensor import run_footprint_sensor

        run_footprint_sensor(tavily_search)
    except Exception as exc:
        _log.debug("footprint sensor skipped: %s", exc)
    return unique


class TavilyATSProvider:
    """Standalone discovery provider (web dorks only — no Playwright)."""

    name = "tavily_ats"
    supported_platforms = ["google", "tavily"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        return discover_ats_via_tavily(request)
