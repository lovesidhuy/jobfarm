"""ATS Board API provider — high-speed direct polling of GH/Lever boards.

The flywheel's engine. Reads active slugs from the slug registry (populated
by JobSpy/Google-CDP/Firecrawl/Tavily/footprint harvesters + manual seeds),
then enumerates every open job on each board via the official public JSON
APIs — no scraping, no CAPTCHAs, no proxies:

  Greenhouse: ``GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs``
  Lever:      ``GET https://api.lever.co/v0/postings/{slug}?mode=json``

Design notes
------------
- **Concurrency**: ``asyncio`` + ``httpx.AsyncClient`` bounded by
  ``asyncio.Semaphore(ATS_BOARD_API_CONCURRENCY)`` (default 20) so a 1,000+
  slug registry doesn't melt the local NIC or trip GH/Lever edge firewalls.
- **Dead-slug policy**: 404/410 → ``registry.mark_poll_failure``; at the
  configured threshold the slug goes ``inactive`` and is skipped on future
  runs. 2xx → ``mark_poll_success`` (resets the counter).
- **Geo ingestion**: every job location passes through
  ``geo_normalizer.resolve_ats_location`` so drift (``"Vancouver - Hybrid"``,
  ``"Remote, Canada"``) is canonicalised *before* policy screens.
- **Emission**: geo-qualified jobs become ``RawJob``s with
  ``source_platform="greenhouse"|"lever"`` and
  ``easy_apply_evidence="ats_board_api_direct"``; destination URLs hit
  ``job-boards.greenhouse.io`` / ``jobs.lever.co`` which
  ``apply_type._EXTERNAL_ATS_DOMAINS`` already classifies as
  ``COMPANY_APPLY`` — the exact leads the ATS applier consumes.

Env
---
``ATS_BOARD_API_ENABLED``        default ``1``
``ATS_BOARD_API_CONCURRENCY``    bounded semaphore size (default ``20``, cap 50)
``ATS_BOARD_API_MAX_SLUGS_PER_PLATFORM``
                                 fair per-ATS board budget per discovery run
                                 (default ``250``, cap ``10,000``).  This
                                 rotates by oldest successful poll and keeps
                                 large imported seed directories safe.
``ATS_BOARD_API_TIMEOUT``        per-request seconds (default ``15``)
``ATS_BOARD_API_TITLE_FILTER``   default ``1`` — light keyword pre-filter so
                                 non-tech boards (marketing, ops) don't flood
                                 the AI gate. ``0`` emits all geo-qualified jobs.
``ATS_BOARD_API_INCLUDE_CONTENT`` default ``0`` — ``content=true`` fetches JD
                                 bodies (bigger payloads; only for deep nights).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from jobbots.core.discovery.ats_slugs import PLATFORM_GREENHOUSE, PLATFORM_LEVER, PLATFORM_ASHBY, PLATFORM_BAMBOOHR
from jobbots.core.discovery.classification.geo_normalizer import resolve_ats_location
from jobbots.core.discovery.classification.location_policy import REGION_METRO_VAN
from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.slug_registry import SlugRegistry, get_registry

_log = logging.getLogger("discovery.providers.ats_board_api")

_GH_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_API = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_BAMBOOHR_API = "https://{slug}.bamboohr.com/careers/list"
_DEAD_STATUSES = {404, 410}


def _geo_allowed(geo: Any) -> bool:
    """Allow only confirmed Metro Vancouver jobs from direct ATS board APIs."""
    return geo.region == REGION_METRO_VAN

# Prefer junior / support / QA / admin — bare "Software Engineer" floods senior
# remote US roles that the farm cannot productively fill.
_TITLE_FILTER_RE = re.compile(
    r"\b("
    r"it support|help ?desk|service desk|desktop support|technical support|"
    r"application support|product support|support (analyst|engineer|technician|specialist)|"
    r"sysadmin|systems? administrator|junior systems|network (admin|support|technician)|"
    r"qa |qa\b|quality assurance|sdet|test (analyst|engineer|automation)|manual test|"
    r"data analyst|junior data|business systems analyst|"
    r"it (co-?op|intern|student|analyst|coordinator|operations|assistant|specialist)|"
    r"junior (software|developer|engineer|qa|network|systems|devops)|"
    r"software developer intern|software engineer (intern|co-?op)|"
    r"software (engineer|developer)|full[- ]?stack (engineer|developer)|"
    r"backend (engineer|developer)|frontend (engineer|developer)|"
    r"systems software engineer|infrastructure (engineer|developer)|"
    r"security analyst|soc analyst|cloud support|desktop|endpoint support|"
    r"information technology|information systems"
    r")\b",
    re.IGNORECASE,
)
# Hard drop even if title matches a broad tech token.
_TITLE_REJECT_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|principle|director|manager|managing|management|"
    r"head|vp|vice president|architect|lead|chief|supervisor|supervising|"
    r"founding|distinguished|executive|iii|iv|v|emea|apac|us only|united states only|"
    r"must be (a )?us citizen|clearance required)\b",
    re.IGNORECASE,
)


def _enabled() -> bool:
    return os.getenv("ATS_BOARD_API_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _concurrency() -> int:
    try:
        return max(1, min(int(os.getenv("ATS_BOARD_API_CONCURRENCY", "20") or "20"), 50))
    except ValueError:
        return 20


def _max_slugs_per_platform() -> int:
    """Return the fair board budget for one discovery cycle.

    External company directories are deliberately much larger than a safe
    single sweep.  A platform-level budget prevents Greenhouse or BambooHR
    from starving the other target ATSs while still allowing every healthy
    board to rotate through over subsequent runs.
    """
    try:
        return max(1, min(int(os.getenv("ATS_BOARD_API_MAX_SLUGS_PER_PLATFORM", "250") or "250"), 10_000))
    except ValueError:
        return 250


def _select_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick the least-recently successful boards fairly across ATSs."""
    budget = _max_slugs_per_platform()
    selected: list[dict[str, Any]] = []
    for platform in (PLATFORM_GREENHOUSE, PLATFORM_LEVER, PLATFORM_ASHBY, PLATFORM_BAMBOOHR):
        platform_records = [r for r in records if r.get("platform") == platform]
        # Never-polled records go first; after that, rotate oldest successes.
        platform_records.sort(
            key=lambda r: (
                r.get("last_successful_poll_at") is not None,
                str(r.get("last_successful_poll_at") or ""),
                str(r.get("slug_id") or ""),
            )
        )
        selected.extend(platform_records[:budget])
    return selected


def _timeout() -> float:
    try:
        return max(3.0, float(os.getenv("ATS_BOARD_API_TIMEOUT", "15") or "15"))
    except ValueError:
        return 15.0


def _title_filter_enabled() -> bool:
    return os.getenv("ATS_BOARD_API_TITLE_FILTER", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _include_content() -> bool:
    return os.getenv("ATS_BOARD_API_INCLUDE_CONTENT", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ---------------------------------------------------------------------------
# Record conversion (pure — unit-testable without HTTP)
# ---------------------------------------------------------------------------

def gh_job_to_raw(slug: str, company: str, job: dict[str, Any], geo_keep: bool = True) -> RawJob | None:
    """Convert one Greenhouse API job dict to a geo-qualified RawJob."""
    title = (job.get("title") or "").strip()
    loc_raw = ((job.get("location") or {}).get("name") or "").strip()
    geo = resolve_ats_location(loc_raw)
    if geo_keep and not _geo_allowed(geo):
        return None
    job_id = str(job.get("id") or "").strip()
    apply_url = (job.get("absolute_url") or "").strip()
    if not title or not apply_url:
        return None
    return RawJob(
        source_platform=PLATFORM_GREENHOUSE,
        source_job_id=f"gh-{slug}-{job_id}",
        title=title,
        company=company or slug,
        location=geo.canonical_location,
        description=(job.get("content") or "") if _include_content() else "",
        listing_url=apply_url,
        destination_url=apply_url,
        date_posted=(job.get("updated_at") or job.get("created_at") or "")[:10] or None,
        easy_apply_evidence="ats_board_api_direct",
        is_remote=geo.is_remote,
        raw_extras={
            "ats_platform": PLATFORM_GREENHOUSE,
            "board_slug": slug,
            "discovered_by": "ats_board_api",
            "geo_raw": loc_raw,
            "geo_region": geo.region,
            "geo_scope": geo.remote_scope,
            "geo_notes": list(geo.notes),
            "work_mode_hint": geo.work_mode_hint,
        },
    )


def lever_job_to_raw(slug: str, company: str, job: dict[str, Any], geo_keep: bool = True) -> RawJob | None:
    """Convert one Lever API posting dict to a geo-qualified RawJob."""
    title = (job.get("text") or "").strip()
    cats = job.get("categories") or {}
    loc_raw = (cats.get("location") or "").strip()
    geo = resolve_ats_location(loc_raw)
    if geo_keep and not _geo_allowed(geo):
        return None
    job_id = str(job.get("id") or "").strip()
    apply_url = (job.get("hostedUrl") or "").strip()
    if not title or not apply_url:
        return None
    created = job.get("createdAt")
    date_posted = None
    if isinstance(created, (int, float)):
        from datetime import datetime, timezone

        date_posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return RawJob(
        source_platform=PLATFORM_LEVER,
        source_job_id=f"lv-{slug}-{job_id}",
        title=title,
        company=company or slug,
        location=geo.canonical_location,
        description=(job.get("descriptionPlain") or "") if _include_content() else "",
        listing_url=apply_url,
        destination_url=apply_url,
        date_posted=date_posted,
        easy_apply_evidence="ats_board_api_direct",
        is_remote=geo.is_remote,
        raw_extras={
            "ats_platform": PLATFORM_LEVER,
            "board_slug": slug,
            "discovered_by": "ats_board_api",
            "geo_raw": loc_raw,
            "geo_region": geo.region,
            "geo_scope": geo.remote_scope,
            "geo_notes": list(geo.notes),
            "work_mode_hint": geo.work_mode_hint,
            "lever_team": (cats.get("team") or ""),
        },
    )


def _ashby_location_raw(job: dict[str, Any]) -> str:
    """Ashby uses free-text ``location``; also mine secondaryLocations/address."""
    parts: list[str] = []
    for key in ("location", "locationName"):
        val = job.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
        elif isinstance(val, dict):
            for k in ("city", "state", "province", "country", "name", "locationName"):
                v = val.get(k)
                if v:
                    parts.append(str(v).strip())
    for sec in job.get("secondaryLocations") or []:
        if isinstance(sec, str) and sec.strip():
            parts.append(sec.strip())
        elif isinstance(sec, dict):
            for k in ("location", "locationName", "city", "state", "country", "name"):
                v = sec.get(k)
                if v:
                    parts.append(str(v).strip())
    addr = job.get("address")
    if isinstance(addr, dict):
        for k in ("city", "state", "province", "country", "postalCode"):
            v = addr.get(k)
            if v:
                parts.append(str(v).strip())
    if job.get("isRemote") and not any("remote" in p.lower() for p in parts):
        parts.append("Remote")
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return ", ".join(out)


def ashby_job_to_raw(slug: str, company: str, job: dict[str, Any], geo_keep: bool = True) -> RawJob | None:
    """Convert one Ashby API job dict to a geo-qualified RawJob."""
    title = (job.get("title") or "").strip()
    loc_raw = _ashby_location_raw(job)
    geo = resolve_ats_location(loc_raw)
    if geo_keep and not _geo_allowed(geo):
        return None
    job_id = str(job.get("id") or "").strip()
    apply_url = (
        job.get("jobUrl")
        or job.get("applyUrl")
        or f"https://jobs.ashbyhq.com/{slug}/{job_id}"
    ).strip()
    if not title or not apply_url:
        return None
    return RawJob(
        source_platform=PLATFORM_ASHBY,
        source_job_id=f"ashby-{slug}-{job_id}",
        title=title,
        company=company or slug,
        location=geo.canonical_location,
        description="",
        listing_url=apply_url,
        destination_url=apply_url,
        easy_apply_evidence="ats_board_api_direct",
        is_remote=geo.is_remote or bool(job.get("isRemote")),
        raw_extras={
            "ats_platform": PLATFORM_ASHBY,
            "board_slug": slug,
            "discovered_by": "ats_board_api",
            "geo_raw": loc_raw,
        },
    )


def _bamboohr_location_raw(job: dict[str, Any]) -> str:
    """Bamboo careers/list uses location + atsLocation dicts and isRemote."""
    parts: list[str] = []
    for key in ("location", "atsLocation"):
        info = job.get(key)
        if isinstance(info, dict):
            for k in ("city", "state", "province", "country"):
                v = info.get(k)
                if v:
                    parts.append(str(v).strip())
        elif isinstance(info, str) and info.strip():
            parts.append(info.strip())
    if job.get("isRemote") and not any("remote" in p.lower() for p in parts):
        parts.append("Remote")
    # locationType "1" is often remote in BambooHR embeds
    loc_type = str(job.get("locationType") or "").strip()
    if loc_type in {"1", "remote"} and not any("remote" in p.lower() for p in parts):
        parts.append("Remote")
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return ", ".join(out)


def bamboohr_job_to_raw(slug: str, company: str, job: dict[str, Any], geo_keep: bool = True) -> RawJob | None:
    """Convert one BambooHR API job dict to a geo-qualified RawJob.

    Public ``/careers/list`` payloads use ``jobOpeningName`` (not jobTitle)
    and nest city/state under ``location`` / ``atsLocation``.
    """
    title = (
        job.get("jobOpeningName")
        or job.get("jobTitle")
        or job.get("title")
        or ""
    ).strip()
    loc_raw = _bamboohr_location_raw(job)
    geo = resolve_ats_location(loc_raw)
    if geo_keep and not _geo_allowed(geo):
        return None
    job_id = str(job.get("id") or "").strip()
    apply_url = f"https://{slug}.bamboohr.com/careers/{job_id}"
    if not title or not job_id:
        return None
    return RawJob(
        source_platform=PLATFORM_BAMBOOHR,
        source_job_id=f"bamboohr-{slug}-{job_id}",
        title=title,
        company=company or slug,
        location=geo.canonical_location,
        description="",
        listing_url=apply_url,
        destination_url=apply_url,
        easy_apply_evidence="ats_board_api_direct",
        is_remote=geo.is_remote or bool(job.get("isRemote")),
        raw_extras={
            "ats_platform": PLATFORM_BAMBOOHR,
            "board_slug": slug,
            "discovered_by": "ats_board_api",
            "geo_raw": loc_raw,
        },
    )


# ---------------------------------------------------------------------------
# Async polling internals
# ---------------------------------------------------------------------------

async def _fetch_board(
    client: Any,
    sem: asyncio.Semaphore,
    record: dict[str, Any],
    registry: SlugRegistry,
) -> tuple[str, str, list[RawJob]]:
    """Poll one board; update registry; return (platform, slug, jobs)."""
    platform = (record.get("platform") or "").strip().lower()
    slug = (record.get("slug_id") or "").strip()
    company = (record.get("company_name") or "").strip()
    if not platform or not slug:
        return platform, slug, []

    params: dict[str, Any] = {}
    if platform == PLATFORM_GREENHOUSE:
        url = _GH_API.format(slug=slug)
        params = {"content": "true" if _include_content() else "false"}
    elif platform == PLATFORM_LEVER:
        url = _LEVER_API.format(slug=slug)
        params = {"mode": "json"}
    elif platform == PLATFORM_ASHBY:
        url = _ASHBY_API.format(slug=slug)
    elif platform == PLATFORM_BAMBOOHR:
        url = _BAMBOOHR_API.format(slug=slug)
    else:
        url = _GH_API.format(slug=slug)

    async with sem:
        try:
            resp = await client.get(url, params=params if params else None)
        except Exception as exc:
            _log.debug("board fetch error %s/%s: %s", platform, slug, exc)
            return platform, slug, []

    status = resp.status_code
    if status in _DEAD_STATUSES:
        registry.mark_poll_failure(slug, platform, reason=f"http_{status}")
        return platform, slug, []
    if status != 200:
        _log.debug("board %s/%s HTTP %d (transient)", platform, slug, status)
        return platform, slug, []

    try:
        payload = resp.json()
    except Exception:
        registry.mark_poll_failure(slug, platform, reason="bad_json")
        return platform, slug, []

    if platform == PLATFORM_GREENHOUSE:
        jobs_raw = payload.get("jobs", [])
    elif platform == PLATFORM_ASHBY:
        jobs_raw = payload.get("jobs", []) if isinstance(payload, dict) else payload
    elif platform == PLATFORM_BAMBOOHR:
        jobs_raw = payload.get("result", payload) if isinstance(payload, dict) else payload
    else:
        jobs_raw = payload

    if not isinstance(jobs_raw, list):
        jobs_raw = []
    registry.mark_poll_success(slug, platform)

    converters = {
        PLATFORM_GREENHOUSE: gh_job_to_raw,
        PLATFORM_LEVER: lever_job_to_raw,
        PLATFORM_ASHBY: ashby_job_to_raw,
        PLATFORM_BAMBOOHR: bamboohr_job_to_raw,
    }
    converter = converters.get(platform, gh_job_to_raw)
    out: list[RawJob] = []
    for job in jobs_raw:
        try:
            raw = converter(slug, company, job)
            if raw is None:
                continue
            if _title_filter_enabled():
                title = raw.title or ""
                if _TITLE_REJECT_RE.search(title):
                    continue
                if not _TITLE_FILTER_RE.search(title):
                    continue
            out.append(raw)
        except Exception as exc:
            _log.debug("job convert failed %s/%s: %s", platform, slug, exc)
            continue
    return platform, slug, out


async def _poll_all(
    records: list[dict[str, Any]],
    registry: SlugRegistry,
) -> list[RawJob]:
    import httpx

    sem = asyncio.Semaphore(_concurrency())
    headers = {"User-Agent": "automation-monorepo/ats-board-api (+job-discovery)"}
    async with httpx.AsyncClient(timeout=_timeout(), headers=headers, follow_redirects=True) as client:
        tasks = [_fetch_board(client, sem, rec, registry) for rec in records]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    jobs: list[RawJob] = []
    for res in results:
        if isinstance(res, Exception):
            _log.debug("board task raised: %s", res)
            continue
        _, _, batch = res
        jobs.extend(batch)
    return jobs


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class AtsBoardApiProvider:
    """DiscoveryProvider that enumerates GH/Lever/Ashby/BambooHR boards from the registry."""

    name = "ats_board_api"
    supported_platforms = [
        PLATFORM_GREENHOUSE,
        PLATFORM_LEVER,
        PLATFORM_ASHBY,
        PLATFORM_BAMBOOHR,
    ]

    def __init__(self, registry: SlugRegistry | None = None) -> None:
        self._registry = registry

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        if not _enabled():
            _log.info("ats_board_api disabled via ATS_BOARD_API_ENABLED")
            return []
        try:
            registry = self._registry or get_registry()
        except Exception as exc:
            _log.warning("ats_board_api: registry unavailable: %s", exc)
            return []

        all_records = registry.iter_active_slugs()
        if not all_records:
            _log.info("ats_board_api: no active slugs in registry")
            return []
        records = _select_records(all_records)
        _log.info(
            "ats_board_api: polling %d of %d active slugs (budget/platform=%d, concurrency=%d)",
            len(records), len(all_records), _max_slugs_per_platform(), _concurrency(),
        )

        try:
            jobs = asyncio.run(_poll_all(records, registry))
        except RuntimeError as exc:
            # Defensive: a caller already inside an event loop (notebooks).
            if "asyncio.run() cannot be called" in str(exc):
                jobs = _run_in_fresh_thread(records, registry)
            else:
                _log.warning("ats_board_api poll failed: %s", exc)
                return []
        except Exception as exc:  # protocol: never raise on partial failure
            _log.warning("ats_board_api poll failed: %s", exc)
            return []

        _log.info("ats_board_api: %d geo-qualified jobs from %d slugs", len(jobs), len(records))
        return jobs


def _run_in_fresh_thread(records: list[dict[str, Any]], registry: SlugRegistry) -> list[RawJob]:
    """Run the async poll in a dedicated thread (loop-safe fallback)."""
    import threading

    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["jobs"] = asyncio.run(_poll_all(records, registry))
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        _log.warning("ats_board_api thread poll failed: %s", box["error"])
        return []
    return box.get("jobs", [])
