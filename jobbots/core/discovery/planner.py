"""Main Query Planner — **Phase I** of the two-phase job pipeline.

Phase boundaries (authoritative):

  Phase I  — Discovery, screening, and queueing  (this module)
    I-A  scrape → normalise (location / work-mode / apply-type) → dedup
    I-B  policy screen (Metro-Van / out-of-province rules) → IT-fit screen →
         queue only approved jobs (``_screen_and_enqueue``)
    Invalid jobs (out-of-province hybrid/on-site, remote company-site, etc.)
    are rejected here and NEVER reach the application queue.

  Phase II — Application / bookmark execution  (``scripts/application_worker.py``)
    Leases already-approved queued jobs and applies (Easy Apply) or bookmarks
    (Metro-Van company-site). It performs NO primary job-fit / geography /
    remote-status screening — only a defensive final validation before acting.

Do not move primary screening out of Phase I-B into Phase II.

Dispatches providers in parallel, normalises results, classifies apply types,
deduplicates across platforms, applies the geo/work-mode/apply-type policy,
screens through the IT-fit AI gate, and enqueues approved jobs into the
existing MongoDB application queue.

Feature flag via ``DISCOVERY_ENGINE`` environment variable:
  ``legacy``  — existing browser-based discovery (no-op here)
  ``new``     — new dual-engine discovery
  ``shadow``  — run both, log diff, only enqueue from legacy
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

from jobbots.core.discovery.contracts import RawJob, NormalizedJob
from jobbots.core.discovery.normalizer import normalize_batch
from jobbots.core.discovery.deduplicator import deduplicate
from jobbots.core.discovery.compatibility_adapter import to_queue_record, queue_record_to_enqueue_kwargs
from jobbots.core.discovery.classification.location_policy import decide_job_policy, policy_enabled
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.telemetry.discovery_metrics import (
    record_jobs_found,
    record_jobs_normalized,
    record_jobs_deduplicated,
    record_screening_result,
    record_jobs_enqueued,
    record_provider_error,
    timed_provider,
    timed_run,
)

_log = logging.getLogger("discovery.planner")

# Monorepo root (one level above core/)
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT


def _load_module(profile: str, filename: str, label: str):
    """Load ``config/<profile>/<filename>`` via importlib."""
    config_dir = _MONOREPO_ROOT / "config" / profile.lower()
    path = config_dir / filename
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        f"config.{profile}.{label}", str(path)
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        _log.warning("Could not load %s for %s: %s", path, profile, exc)
        return None
    return mod


def _glassdoor_only(portals: list[str] | None) -> bool:
    return bool(portals) and set(p.strip().lower() for p in portals) == {"glassdoor"}


def _workopolis_only(portals: list[str] | None) -> bool:
    return bool(portals) and set(p.strip().lower() for p in portals) == {"workopolis"}


def _google_only(portals: list[str] | None) -> bool:
    return bool(portals) and set(p.strip().lower() for p in portals) == {"google"}


def _metro_only_locations_from_override(
    profile: str,
    override_filename: str,
    override_modname: str,
) -> list[str] | None:
    """Load metro city list from a portal override module (no Remote/\"\")."""
    override = _load_module(profile, override_filename, override_modname)
    if override is not None:
        locations = getattr(override, "search_locations", None)
        if locations:
            return [loc for loc in locations if (loc or "").strip()]
    return None


def _load_search_terms(profile: str) -> list[str]:
    """Load search terms from ``config/<profile>/search.py``."""
    mod = _load_module(profile, "search.py", "search")
    if mod is None:
        _log.warning("Search config not found for profile %s", profile)
        return []
    return list(getattr(mod, "search_terms", []))


def _load_linkedin_search_terms(profile: str) -> list[str]:
    """Load LinkedIn-only main terms; fall back to full ``search_terms``."""
    mod = _load_module(profile, "search.py", "search_linkedin")
    if mod is None:
        return []
    terms = getattr(mod, "linkedin_search_terms", None)
    if terms:
        return list(terms)
    return list(getattr(mod, "search_terms", []) or [])


def _linkedin_term_batch_size() -> int:
    """Term chunk size for LinkedIn JobSpy batches (env ``LINKEDIN_DISCOVERY_TERM_BATCH``)."""
    raw = os.getenv("LINKEDIN_DISCOVERY_TERM_BATCH", "5")
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = 5
    return max(1, size)


def _chunk_terms(terms: list[str], size: int) -> list[list[str]]:
    return [terms[i : i + size] for i in range(0, len(terms), size)]


def _is_linkedin_jobspy(provider: Any) -> bool:
    portals = [str(p).strip().lower() for p in (getattr(provider, "portals", None) or [])]
    name = getattr(provider, "name", "") or ""
    return portals == ["linkedin"] and name.startswith("jobspy")


def _merge_screening_stats(into: dict[str, int], part: dict[str, int]) -> dict[str, int]:
    for key, value in part.items():
        if isinstance(value, int):
            into[key] = int(into.get(key, 0) or 0) + value
    return into


def _normalize_raw_jobs(
    raw_jobs: list[RawJob],
    *,
    locations: list[str],
    freshness_days: int | None,
    provider_results: dict[str, int],
) -> list[NormalizedJob]:
    """Normalize raw jobs in true batches (one call per engine/search_term group)."""
    if not raw_jobs:
        return []
    loc = locations[0] if locations else ""
    # Group by (platform engine, search_term) to avoid per-job normalize_batch.
    groups: dict[tuple[str, str], list[RawJob]] = {}
    for raw in raw_jobs:
        search_term = (raw.raw_extras or {}).get("search_term", "") or ""
        engine = _engine_for_platform(raw.source_platform, provider_results)
        groups.setdefault((engine, search_term), []).append(raw)
    normalized: list[NormalizedJob] = []
    for (engine, search_term), batch_raw in groups.items():
        batch = normalize_batch(
            batch_raw,
            discovery_engine=engine,
            search_term=search_term,
            location=loc,
            freshness_days=freshness_days,
        )
        normalized.extend(batch)
    return normalized


def _job_identity(job: NormalizedJob) -> tuple[str, str]:
    return ((job.source_platform or "").lower(), job.source_job_id or job.listing_url or "")


def _load_search_locations(
    profile: str,
    portals: list[str] | None = None,
) -> list[str]:
    """Load search locations; Glassdoor/Workopolis/Google-only use metro cities (no Remote)."""
    if _glassdoor_only(portals):
        locs = _metro_only_locations_from_override(
            profile, "glassdoor_search.py", "glassdoor_search",
        )
        if locs:
            return locs
        # Fallback: strip Remote/empty from main search.py
        mod = _load_module(profile, "search.py", "search_locs_fallback")
        if mod is not None:
            locs = list(getattr(mod, "glassdoor_search_locations", None) or [])
            if locs:
                return locs
            locs = list(getattr(mod, "search_locations", None) or [])
            return [
                loc for loc in locs
                if (loc or "").strip() and (loc or "").strip().lower() != "remote"
            ]

    if _workopolis_only(portals):
        locs = _metro_only_locations_from_override(
            profile, "workopolis_search.py", "workopolis_search",
        )
        if locs:
            return locs
        mod = _load_module(profile, "search.py", "search_locs_wp_fallback")
        if mod is not None:
            locs = list(getattr(mod, "search_locations", None) or [])
            return [
                loc for loc in locs
                if (loc or "").strip() and (loc or "").strip().lower() != "remote"
            ]

    if _google_only(portals):
        # ATS Google/Tavily: never fan out on bare Remote (US senior SWE pollution).
        # Query builder expands metro pack inside one dork — return a single anchor
        # (prefer Vancouver) so we do not waste N_cities × terms API credits.
        mod = _load_module(profile, "search.py", "search_locs_google")
        if mod is not None:
            locs = list(getattr(mod, "search_locations", None) or [])
            metro = [
                loc for loc in locs
                if (loc or "").strip() and (loc or "").strip().lower() != "remote"
            ]
            if metro:
                for loc in metro:
                    if "vancouver" in (loc or "").lower() and "wa" not in (loc or "").lower().split(","):
                        return [loc]
                return [metro[0]]

    mod = _load_module(profile, "search.py", "search_locs")
    if mod is None:
        return ["Vancouver, BC"]

    locations = getattr(mod, "search_locations", None)
    if locations:
        return list(locations)
    single = getattr(mod, "search_location", "Vancouver, BC")
    # Empty is intentional: it represents the legacy browser bot's remote-only
    # pass and must never be rewritten to Vancouver.
    return [single] if single is not None else ["Vancouver, BC"]


def _load_search_policy(
    profile: str,
    portals: list[str] | None = None,
) -> dict[str, Any]:
    """Port legacy per-profile search filters into the discovery request."""
    if _glassdoor_only(portals):
        gd = _load_module(profile, "glassdoor_search.py", "glassdoor_policy")
        if gd is not None:
            return {
                "radius_km": int(getattr(gd, "search_radius_km", 25) or 25),
                "easy_apply_only": bool(getattr(gd, "easy_apply_only", True)),
                "job_types": [],
                "experience_levels": [],
                "workplace_types": [],
            }

    if _workopolis_only(portals):
        wp = _load_module(profile, "workopolis_search.py", "workopolis_policy")
        if wp is not None:
            return {
                "radius_km": int(getattr(wp, "search_radius_km", 25) or 25),
                "easy_apply_only": bool(getattr(wp, "easy_apply_only", True)),
                "job_types": [],
                "experience_levels": [],
                "workplace_types": [],
            }

    mod = _load_module(profile, "search.py", "search_policy")
    if mod is None:
        return {}
    return {
        "radius_km": int(getattr(mod, "search_radius_km", 25) or 25),
        "easy_apply_only": bool(getattr(mod, "easy_apply_only", False)),
        "job_types": list(getattr(mod, "job_type", []) or []),
        "experience_levels": list(getattr(mod, "experience_level", []) or []),
        "workplace_types": list(getattr(mod, "on_site", []) or []),
    }


def _build_providers(portals: list[str] | None) -> list[Any]:
    """Instantiate discovery providers filtered by requested portals."""
    providers = []

    want_indeed = not portals or "indeed" in portals
    want_glassdoor = not portals or "glassdoor" in portals
    want_linkedin = not portals or "linkedin" in portals
    want_workopolis = not portals or "workopolis" in portals
    # Google is opt-in only — JobSpy Google HTTP is broken; use CDP provider.
    want_google = bool(portals) and "google" in portals
    # ATS board API (GH/Lever/Ashby/BambooHR direct poller) is included by default or when requested explicitly.
    want_ats_board_api = not portals or any(p in (portals or []) for p in ("ats_board_api", "greenhouse", "lever", "ashby", "bamboohr", "bamboo"))
    # LinkedIn→ATS crossmatch is opt-in via "ats_crossmatch".
    want_ats_crossmatch = bool(portals) and "ats_crossmatch" in portals
    # Paid/credit-backed API sources stay flag-gated (JOBSPIPE_ENABLED /
    # ADZUNA_ENABLED). When enabled in production they supplement every IT
    # discovery cycle (not only empty-portal full runs) so Greenhouse/Lever
    # leads keep arriving without burning NST quota.
    _api_leads_on = (
        not portals
        or any(
            p in (portals or [])
            for p in (
                "jobspipe",
                "adzuna",
                "ats_board_api",
                "ats_crossmatch",
                "firecrawl_ats",
                "tavily_ats",
                "greenhouse",
                "lever",
                "google",
                "indeed",
                "linkedin",
            )
        )
    )
    want_jobspipe = (bool(portals) and "jobspipe" in portals) or (
        _api_leads_on
        and os.getenv("JOBSPIPE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    )
    want_adzuna = (bool(portals) and "adzuna" in portals) or (
        _api_leads_on
        and os.getenv("ADZUNA_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    )

    if want_indeed:
        from jobbots.core.discovery.providers.jobspy_provider import JobSpyProvider
        providers.append(JobSpyProvider(portals=["indeed"]))

    if want_glassdoor:
        # Default: CDP (stealth browser). JobSpy HTTP has locationAjax quirks but
        # still yields leads when Cloudflare blocks CDP. hybrid = both sources.
        # Production can force jobspy-only to avoid CDP/CapMonster cost.
        _gd_prov = (os.environ.get("GLASSDOOR_DISCOVERY_PROVIDER") or "cdp").strip().lower()
        if _gd_prov in {"jobspy", "http", "job_spy"}:
            from jobbots.core.discovery.providers.jobspy_provider import JobSpyProvider
            providers.append(JobSpyProvider(portals=["glassdoor"]))
        elif _gd_prov in {"hybrid", "both", "cdp+jobspy", "cdp_jobspy"}:
            from jobbots.core.discovery.providers.glassdoor_cdp_provider import GlassdoorCDPProvider
            from jobbots.core.discovery.providers.jobspy_provider import JobSpyProvider
            providers.append(GlassdoorCDPProvider())
            providers.append(JobSpyProvider(portals=["glassdoor"]))
        else:
            from jobbots.core.discovery.providers.glassdoor_cdp_provider import GlassdoorCDPProvider
            providers.append(GlassdoorCDPProvider())

    if want_linkedin:
        from jobbots.core.discovery.providers.jobspy_provider import JobSpyProvider
        providers.append(JobSpyProvider(portals=["linkedin"]))

    if want_workopolis:
        # Workopolis HTTP is the primary; browser fallback is handled below
        from jobbots.core.discovery.providers.workopolis_http_provider import WorkopolisHTTPProvider
        providers.append(WorkopolisHTTPProvider())

    if want_google:
        # JobSpy Google is a cheap HTTP scout; Google-CDP remains the
        # proxy/CapMonster fallback and can recover from Google HTTP blocks.
        if os.getenv("JOBSPY_GOOGLE_ENABLED", "0").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            from jobbots.core.discovery.providers.jobspy_provider import JobSpyProvider
            providers.append(JobSpyProvider(portals=["google"]))
        from jobbots.core.discovery.providers.google_cdp_provider import GoogleCDPProvider
        providers.append(GoogleCDPProvider())

    if want_ats_board_api:
        from jobbots.core.discovery.providers.ats_board_api import AtsBoardApiProvider
        providers.append(AtsBoardApiProvider())

    if want_ats_crossmatch:
        from jobbots.core.discovery.providers.ats_crossmatch_provider import AtsCrossmatchProvider
        providers.append(AtsCrossmatchProvider())

    if want_jobspipe or (not portals and os.getenv("JOBSPIPE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}):
        from jobbots.core.discovery.providers.jobspipe_provider import JobsPipeProvider, jobspipe_enabled
        if want_jobspipe or jobspipe_enabled():
            providers.append(JobsPipeProvider())

    if want_adzuna or (not portals and os.getenv("ADZUNA_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}):
        from jobbots.core.discovery.providers.adzuna_provider import AdzunaProvider, adzuna_enabled
        if want_adzuna or adzuna_enabled():
            providers.append(AdzunaProvider())

    # ── ATS web-search fallbacks (Firecrawl + Tavily) — discover Greenhouse/Lever
    #     jobs via Google-dork search, not just the pre-registered slug registry.
    #     Enabled by default alongside ats_board_api, or explicitly via portal name. ──
    want_firecrawl_ats = bool(portals) and "firecrawl_ats" in portals
    want_tavily_ats = bool(portals) and "tavily_ats" in portals
    _ats_web_auto = want_ats_board_api and not portals  # auto-enable only when no portals specified
    if _ats_web_auto or want_firecrawl_ats:
        _ats_web_enabled = os.getenv("ATS_WEB_SEARCH_ENABLED", "1").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if _ats_web_enabled or want_firecrawl_ats:
            try:
                from jobbots.core.discovery.providers.firecrawl_ats import FirecrawlATSProvider
                from jobbots.core.firecrawl_client import firecrawl_enabled
                if want_firecrawl_ats or firecrawl_enabled():
                    providers.append(FirecrawlATSProvider())
                    _log.info("ATS: Firecrawl web-search provider enabled")
            except Exception as exc:
                _log.debug("Firecrawl ATS provider not available: %s", exc)
    if _ats_web_auto or want_tavily_ats:
        try:
            from jobbots.core.discovery.providers.tavily_ats import TavilyATSProvider
            from jobbots.core.discovery.providers.tavily_ats import tavily_enabled as _tavily_enabled
            if want_tavily_ats or _tavily_enabled():
                providers.append(TavilyATSProvider())
                _log.info("ATS: Tavily web-search provider enabled")
        except Exception as exc:
            _log.debug("Tavily ATS provider not available: %s", exc)

    return providers


def _run_provider(provider, request: DiscoveryRequest) -> tuple[str, list[RawJob]]:
    """Execute a single provider and return (provider_name, raw_jobs)."""
    with timed_provider(provider.name):
        try:
            jobs = provider.discover(request)
            for platform in provider.supported_platforms:
                platform_jobs = [j for j in jobs if j.source_platform == platform]
                record_jobs_found(provider.name, platform, len(platform_jobs))
            return provider.name, jobs
        except Exception as exc:
            # Workopolis HTTP incomplete → NST browser only if explicitly allowed.
            # Default OFF (quota + user preference: HTTP leads only).
            from jobbots.core.discovery.providers.workopolis_http_provider import WorkopolisHTTPIncomplete
            if isinstance(exc, WorkopolisHTTPIncomplete):
                allow_fb = str(os.environ.get("WORKOPOLIS_ALLOW_BROWSER_FALLBACK") or "0").strip().lower() in {
                    "1", "true", "yes", "on",
                }
                if not allow_fb:
                    _log.warning(
                        "Workopolis HTTP incomplete (%s) — browser fallback DISABLED "
                        "(set WORKOPOLIS_ALLOW_BROWSER_FALLBACK=1 to enable NST scrape)",
                        exc,
                    )
                    record_provider_error(provider.name, str(exc))
                    return provider.name, []
                _log.info("Workopolis HTTP incomplete — invoking browser fallback (opt-in)")
                try:
                    from jobbots.core.discovery.providers.workopolis_browser_fallback import (
                        WorkopolisBrowserFallback,
                    )
                    fallback = WorkopolisBrowserFallback()
                    jobs = fallback.discover(request)
                    record_jobs_found(fallback.name, "workopolis", len(jobs))
                    return fallback.name, jobs
                except Exception as fb_exc:
                    _log.error("Workopolis browser fallback also failed: %s", fb_exc)
                    record_provider_error("workopolis_browser", str(fb_exc))
                    return provider.name, []

            _log.error("Provider %s failed: %s", provider.name, exc)
            record_provider_error(provider.name, str(exc))
            return provider.name, []


def _screen_and_enqueue(
    jobs: list[NormalizedJob],
    profile: str,
    *,
    dry_run: bool = False,
    indeed_sync_index=None,
) -> dict[str, int]:
    """**Phase I-B** — pre-queue policy screen + IT-fit screen + enqueue.

    This is part of Phase I (discovery), NOT Phase II (application). It is the
    last gate before the application queue, so every reject here keeps an
    invalid job out of the queue entirely:

      1. Geo/work-mode/apply-type policy (``decide_job_policy``) — Metro-Van vs
         out-of-province, remote/hybrid/on-site, easy-apply vs company-site.
      2. Indeed-family sync skip — never enqueue Glassdoor/Workopolis twins
         already present on Indeed / Glassdoor-applied / email history.
      3. IT-fit AI screening (``screen_job_with_ai`` via ``_gate_adapter``).
      4. Enqueue approved jobs, routed by the policy decision
         (easy-apply → apply queue; Metro-Van company-site → bookmark/save).

    Uses ``enqueue_approved_job()`` from ``core.shared_modules.job_queue_bridge``.
    """
    stats = {
        # Legacy aliases (kept for callers)
        "screened": 0, "passed": 0, "rejected": 0, "enqueued": 0, "new": 0,
        "policy_rejected": 0, "policy_apply": 0, "policy_save": 0,
        "policy_verify": 0,
        # Wave A.2 explicit policy-vs-AI counters
        "policy_rejected_before_ai": 0,
        "ai_screened": 0,
        "ai_passed": 0,
        "ai_rejected": 0,
        "final_apply": 0,
        "final_save": 0,
        "final_verify": 0,
        # Wave B.1 Glassdoor / Workopolis Indeed-sync counters
        "glassdoor_skipped_indeed_sync": 0,
        "workopolis_skipped_indeed_sync": 0,
        "glassdoor_enqueued_ea": 0,
        "glassdoor_rejected_outside_metro": 0,
        "glassdoor_rejected_non_ea": 0,
        "already_known_skipped": 0,
    }

    geo_policy_on = policy_enabled()
    profile_key = str(profile or "").strip().lower()
    _log.info("Geo/work-mode policy: %s", "ON" if geo_policy_on else "OFF")

    sync_index = indeed_sync_index
    needs_sync = any(
        (j.source_platform or "").lower() in {"glassdoor", "workopolis"}
        for j in jobs
    )
    if sync_index is None and needs_sync:
        try:
            from jobbots.core.discovery.indeed_sync import IndeedSyncIndex
            from jobbots.core.job_queue import JobQueue
            sync_index = IndeedSyncIndex(queue=JobQueue())
        except Exception as exc:
            _log.warning("Indeed sync index unavailable: %s", exc)
            from jobbots.core.discovery.indeed_sync import IndeedSyncIndex
            sync_index = IndeedSyncIndex(queue=None, load_history=True)

    # Direct Indeed discovery also needs the same ledger before AI screening.
    # Otherwise a listing already applied to manually (or queued earlier) still
    # consumes an AI fit-check before enqueue de-duplication rejects it.
    known_indeed_index = sync_index
    if known_indeed_index is None:
        try:
            from jobbots.core.discovery.indeed_sync import IndeedSyncIndex
            from jobbots.core.job_queue import JobQueue
            known_indeed_index = IndeedSyncIndex(queue=JobQueue())
        except Exception as exc:
            _log.warning("Known-Indeed ledger unavailable: %s", exc)

    # Log five sample records immediately before screening
    sample_jobs = jobs[:5]
    for idx, sample in enumerate(sample_jobs, start=1):
        msg = (
            f"[Sample Job {idx}] Title: {sample.job_title!r}, Company: {sample.company_name!r}, "
            f"Location: {sample.location!r}, URL: {sample.listing_url!r}, "
            f"Desc Length: {len(sample.description) if sample.description else 0}, Method: {sample.apply_type!r}"
        )
        _log.info(msg)
        print(msg)

    # Phase I cleaning mirrors Indeed browser gates (seniority, non-IT, JD
    # blockers) so Phase II only applies/bookmarks — no re-screen redundancy.
    try:
        _ensure_monorepo_path()
        from jobbots.core.discovery._gate_adapter import (
            batch_ai_screen_jobs,
            hard_screen_job,
            is_ambiguous_title_reason,
        )
    except ImportError as exc:
        _log.error("Cannot import deterministic hard gate: %s", exc)
        return stats

    from jobbots.core.discovery.indeed_sync import already_synced_with_indeed_family

    # AI is limited to two deferred cases: (1) ambiguous title that local Easy
    # Apply / IT signals cannot classify, and (2) Indeed company-site leads
    # that passed local company-site gate and may be worth saving.

    batch_requests = []
    batch_kind: dict[str, str] = {}
    for job in jobs:
        policy = decide_job_policy(job)
        # Match Phase II method: policy gate_easy_apply (Indeed EA vs company_site).
        gate_ea = (
            policy.gate_easy_apply if geo_policy_on
            else (job.apply_type == "EASY_APPLY")
        )
        local_pass, _, local_reason = hard_screen_job(
            title=job.job_title, company=job.company_name, description=job.description,
            location=job.location, easy_apply=gate_ea, profile=profile,
        )
        key = str(job.source_job_id)
        if (
            policy.action == "SAVE"
            and local_pass
            and (job.source_platform or "").lower() == "indeed"
            and str(profile or "").strip().lower() != "general"
        ):
            batch_kind[key] = "company_save"
            batch_requests.append({"jid": key, "title": job.job_title, "company": job.company_name,
                                   "location": job.location, "has_easy_apply": False,
                                   "card_text": job.description})
        elif (not local_pass) and is_ambiguous_title_reason(local_reason):
            # Unsure titles only — obvious non-IT already hard-rejected.
            batch_kind[key] = "ambiguous_title"
            batch_requests.append({"jid": key, "title": job.job_title, "company": job.company_name,
                                   "location": job.location, "has_easy_apply": bool(gate_ea),
                                   "card_text": (job.description or "")[:1500]})
    # Cap batch AI size to avoid mega-batch timeout → fail-closed.
    try:
        ai_cap = int(os.getenv("DISCOVERY_BATCH_AI_MAX", "40") or "40")
    except ValueError:
        ai_cap = 40
    ai_cap = max(1, min(ai_cap, 200))
    if len(batch_requests) > ai_cap:
        # Prefer ambiguous EA titles over company_save when truncating.
        amb = [r for r in batch_requests if batch_kind.get(str(r.get("jid"))) == "ambiguous_title"]
        sav = [r for r in batch_requests if batch_kind.get(str(r.get("jid"))) == "company_save"]
        kept = (amb + sav)[:ai_cap]
        kept_ids = {str(r.get("jid")) for r in kept}
        for jid, kind in list(batch_kind.items()):
            if jid not in kept_ids:
                batch_kind.pop(jid, None)
        batch_requests = kept
        _log.info("Batch AI capped to %d of deferred titles (DISCOVERY_BATCH_AI_MAX)", ai_cap)
    try:
        batch_decisions = batch_ai_screen_jobs(batch_requests) if batch_requests else {}
    except Exception as exc:
        _log.warning("Deferred batch AI review failed; failing closed: %s", exc)
        batch_decisions = {}

    import datetime
    telemetry_records = []

    def _write_screening_telemetry(records: list[dict]) -> None:
        if not records:
            return
        import json
        path = "/tmp/discovery_screening_telemetry.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, default=str) + "\n")
        except Exception as e_telemetry:
            _log.warning("Failed to write discovery screening telemetry: %s", e_telemetry)

    for job in jobs:
        # ── Geo/work-mode/apply-type policy (before spending AI tokens) ────
        decision = decide_job_policy(job)

        def add_telemetry(action, reason_str, gate_score=None, gate_reason_str=None, enqueued=False):
            telemetry_records.append({
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "portal": (job.source_platform or "").lower(),
                "job_id": job.source_job_id,
                "title": job.job_title,
                "company": job.company_name,
                "location": job.location,
                "url": job.listing_url,
                "apply_type": job.apply_type,
                "region": decision.region,
                "work_mode": decision.work_mode,
                "decision_action": action,
                "decision_reason": reason_str,
                "gate_score": gate_score,
                "gate_reason": gate_reason_str,
                "is_enqueued": enqueued,
            })

        if geo_policy_on and not decision.keep:
            # Deterministic policy reject — never call the AI gate.
            stats["policy_rejected"] += 1
            stats["policy_rejected_before_ai"] += 1
            if (job.source_platform or "").lower() == "glassdoor":
                if decision.reason == "glassdoor_outside_metro":
                    stats["glassdoor_rejected_outside_metro"] += 1
                elif decision.reason == "glassdoor_non_easy_apply":
                    stats["glassdoor_rejected_non_ea"] += 1
            _log.info(
                "Policy reject: %s (%s) [%s] — %s",
                job.job_title, job.company_name, job.location, decision.reason,
            )
            add_telemetry("REJECT", decision.reason, gate_reason_str="Policy rejected before AI screening")
            continue

        # ── Indeed-family sync (Glassdoor + Workopolis) ───────────────────
        platform = (job.source_platform or "").lower()
        if platform == "indeed" and known_indeed_index is not None:
            known_reason = known_indeed_index.match_known_indeed(job)
            if known_reason:
                stats["already_known_skipped"] += 1
                stats["policy_rejected"] += 1
                stats["policy_rejected_before_ai"] += 1
                _log.info(
                    "Known-Indeed skip before AI: %s (%s) [%s] — %s",
                    job.job_title, job.company_name, job.location, known_reason,
                )
                add_telemetry("SKIP", known_reason, gate_reason_str="Already applied or queued duplicate on Indeed")
                continue

        if platform in {"glassdoor", "workopolis"}:
            skip, sync_reason = already_synced_with_indeed_family(
                job, index=sync_index,
            )
            if skip:
                if platform == "glassdoor":
                    stats["glassdoor_skipped_indeed_sync"] += 1
                else:
                    stats["workopolis_skipped_indeed_sync"] += 1
                stats["policy_rejected"] += 1
                stats["policy_rejected_before_ai"] += 1
                _log.info(
                    "Indeed-sync skip: %s (%s) [%s] — %s",
                    job.job_title, job.company_name, platform, sync_reason,
                )
                add_telemetry("SKIP", sync_reason, gate_reason_str="Already applied or synced duplicate on Indeed")
                continue

        stats["screened"] += 1
        stats["ai_screened"] += 1
        gate_easy_apply = (
            decision.gate_easy_apply if geo_policy_on
            else (job.apply_type == "EASY_APPLY")
        )
        try:
            if profile_key == "general":
                # General has its own bounded office/customer-service title
                # family. Keep it deterministic here so an IT-only legacy
                # reviewer can never reject valid General leads.
                from jobbots.core.discovery._gate_adapter import (
                    _GENERAL_HARD_REJECT_MARKERS,
                    _GENERAL_SENIORITY_MARKERS,
                    _GENERAL_TITLE_SIGNALS,
                )
                title_text = " ".join((job.job_title or "").lower().split())
                company_text = " ".join((job.company_name or "").lower().split())
                general_ok = (
                    company_text not in {"", "nan", "none", "null", "n/a", "na", "-"}
                    and not any(marker in title_text for marker in _GENERAL_SENIORITY_MARKERS)
                    and not any(marker in title_text for marker in _GENERAL_HARD_REJECT_MARKERS)
                    and any(signal in title_text for signal in _GENERAL_TITLE_SIGNALS)
                )
                if general_ok:
                    passed, score, reason = True, 100, "general deterministic title gate"
                else:
                    passed, score, reason = hard_screen_job(
                        title=job.job_title,
                        company=job.company_name,
                        description=job.description,
                        location=job.location,
                        easy_apply=gate_easy_apply,
                        profile=profile_key,
                    )
            else:
                passed, score, reason = hard_screen_job(
                    title=job.job_title,
                    company=job.company_name,
                    description=job.description,
                    location=job.location,
                    easy_apply=gate_easy_apply,
                    profile=profile_key,
                )
        except Exception as exc:
            _log.warning("Hard screening failed for %s: %s", job.source_job_id, exc)
            stats["rejected"] += 1
            stats["ai_rejected"] += 1
            add_telemetry("REJECT", "screening_exception", gate_reason_str=f"Exception: {exc}")
            continue

        if not passed:
            batch = batch_decisions.get(str(job.source_job_id), {})
            if (batch_kind.get(str(job.source_job_id)) == "ambiguous_title"
                    and batch.get("decision") == "PROCEED"):
                passed, score = True, 85
                reason = f"batch AI title approval: {batch.get('reason', '')}"
            elif (
                batch_kind.get(str(job.source_job_id)) == "ambiguous_title"
                and bool(gate_ea)
                and str(profile or "").strip().lower() == "it"
            ):
                # Fail-open Easy Apply IT-signal titles when:
                #  (a) LLM chain returned nothing (timeout / empty), OR
                #  (b) LLM returned SKIP/reject but the title still carries a
                #      clear IT discipline signal (help desk, sysadmin, QA…).
                # Batch AI has been fail-closed on whole ticks (0/178 pass);
                # without this, LinkedIn/Indeed IT starve while general CSR
                # floods the only non-empty queue.
                try:
                    from jobbots.core.discovery._gate_adapter import _IT_TITLE_SIGNALS

                    title_l = (job.job_title or "").lower()
                    desc_l = (job.description or "").lower()[:2000]
                    has_it_title = any(sig in title_l for sig in _IT_TITLE_SIGNALS)
                    has_it_desc = any(
                        sig in desc_l
                        for sig in (
                            "information technology", "help desk", "service desk",
                            "desktop support", "systems administrator", "active directory",
                            "windows server", "network", "troubleshooting", "ticket",
                            "service now", "servicenow", "itil", "azure", "aws ",
                            "quality assurance", "test case", "software test",
                        )
                    )
                    decision_l = str(batch.get("decision") or "").upper()
                    llm_miss = not batch
                    llm_soft_reject = decision_l in {"", "SKIP", "REJECT", "NO", "FALSE"}
                    if has_it_title and (llm_miss or llm_soft_reject):
                        passed, score = True, 80 if llm_miss else 75
                        reason = (
                            "fail-open: ambiguous EA IT title after LLM failover miss"
                            if llm_miss
                            else f"fail-open: EA IT title signal overrode batch AI ({decision_l or 'empty'})"
                        )
                        _log.info(
                            "Fail-open enqueue candidate: %s (%s) — %s",
                            job.job_title,
                            job.company_name,
                            reason,
                        )
                    elif (not has_it_title) and has_it_desc and llm_miss:
                        passed, score = True, 70
                        reason = "fail-open: ambiguous EA title with IT JD after LLM miss"
                        _log.info(
                            "Fail-open enqueue candidate (JD IT): %s (%s)",
                            job.job_title,
                            job.company_name,
                        )
                except Exception:
                    pass
            if not passed:
                _log.debug(
                    "Gate reject: %s (%s) — score=%d reason=%s",
                    job.job_title, job.company_name, score, reason,
                )
                stats["rejected"] += 1
                stats["ai_rejected"] += 1
                add_telemetry("REJECT", "gate_rejected", gate_score=score, gate_reason_str=reason)
                continue

        if decision.action == "SAVE":
            batch = batch_decisions.get(str(job.source_job_id), {})
            general_save = str(profile or "").strip().lower() == "general"
            # Direct Greenhouse/Lever/Ashby/Bamboo boards are applied by Playwright
            # (google_it), not "save for later". Never drop them on company_save AI
            # — that gate is for Indeed company-site bookmarks only.
            plat = (job.source_platform or "").strip().lower()
            is_direct_ats = plat in {
                "greenhouse", "lever", "ashby", "bamboohr", "bamboo",
            } or bool(
                (job.destination_url or job.listing_url or "")
                and any(
                    h in (job.destination_url or job.listing_url or "").lower()
                    for h in (
                        "greenhouse.io", "jobs.lever.co", "lever.co",
                        "ashbyhq.com", "bamboohr.com",
                    )
                )
            )
            if is_direct_ats:
                reason = f"direct ATS apply enqueue ({plat or 'url'})"
                _log.info(
                    "Direct ATS enqueue (skip company_save AI): %s (%s) platform=%s",
                    job.job_title, job.company_name, plat or "url",
                )
                # Grow slug registry from every direct ATS URL we accept.
                try:
                    from jobbots.core.discovery.slug_registry import register_slugs_from_url
                    register_slugs_from_url(
                        job.destination_url or job.listing_url,
                        source=f"enqueue_{plat or 'ats'}",
                    )
                except Exception:
                    pass
            elif (
                not general_save
                and (
                    batch_kind.get(str(job.source_job_id)) != "company_save"
                    or batch.get("decision") != "PROCEED"
                )
            ):
                stats["rejected"] += 1
                stats["ai_rejected"] += 1
                _log.info("Company-site skip: %s (%s) — batch AI did not approve saving", job.job_title, job.company_name)
                add_telemetry("REJECT", "company_save_rejected", gate_score=score, gate_reason_str="Company save not approved by AI")
                continue
            elif general_save:
                reason = "general profile deterministic company-site save"
            else:
                reason = f"batch AI company save: {batch.get('reason', reason)}"

        stats["passed"] += 1
        stats["ai_passed"] += 1
        if geo_policy_on:
            if decision.action == "SAVE":
                stats["policy_save"] += 1
                stats["final_save"] += 1
            elif decision.action == "VERIFY":
                stats["policy_verify"] += 1
                stats["final_verify"] += 1
            else:
                stats["policy_apply"] += 1
                stats["final_apply"] += 1

        if dry_run:
            _log.info(
                "[DRY-RUN] Would %s: %s at %s [%s] (score=%d) — %s",
                decision.action if geo_policy_on else "ENQUEUE",
                job.job_title, job.company_name, job.location, score, decision.reason,
            )
            if (job.source_platform or "").lower() == "glassdoor":
                stats["glassdoor_enqueued_ea"] += 1
            add_telemetry(decision.action if geo_policy_on else "ENQUEUE", "dry_run_passed", gate_score=score, gate_reason_str=reason)
            continue

        # Enqueue via compatibility adapter
        rec = to_queue_record(job, profile)
        rec.gate_score = score
        rec.gate_reason = reason
        # Route according to the geo policy (metro-van company-site → save,
        # remote out-of-metro → easy-apply, etc.).
        if geo_policy_on:
            rec.application_method = decision.application_method
            rec.initial_status = decision.initial_status
            # Phase-I AI approval (title batch OR company-site save) — bridge
            # must not re-hard-reject these after batch PROCEED.
            jid_key = str(job.source_job_id)
            rec.company_ai_approved = (
                (
                    decision.action == "SAVE"
                    and batch_kind.get(jid_key) == "company_save"
                    and batch_decisions.get(jid_key, {}).get("decision") == "PROCEED"
                )
                or (
                    batch_kind.get(jid_key) == "ambiguous_title"
                    and batch_decisions.get(jid_key, {}).get("decision") == "PROCEED"
                )
                or "batch ai title approval" in (reason or "").lower()
            )
            # Persist the geo classification so Phase II can defensively confirm
            # lease-and-verify runs only for Metro-Vancouver jobs (safeguard #1).
            rec.region = decision.region

        try:
            from jobbots.core.shared_modules.job_queue_bridge import enqueue_approved_job
            kwargs = queue_record_to_enqueue_kwargs(rec)
            queue_id, created = enqueue_approved_job(**kwargs)
            stats["enqueued"] += 1
            if created:
                stats["new"] += 1
            if (job.source_platform or "").lower() == "glassdoor":
                stats["glassdoor_enqueued_ea"] += 1
            _log.info(
                "%s [%s/%s]: %s at %s → queue %s (score=%d)",
                "NEW" if created else "DUP",
                (decision.action if geo_policy_on else "ENQUEUE"),
                rec.application_method,
                job.job_title, job.company_name, queue_id, score,
            )
            add_telemetry(decision.action if geo_policy_on else "ENQUEUE", "enqueued", gate_score=score, gate_reason_str=reason, enqueued=True)
        except Exception as exc:
            _log.error("Enqueue failed for %s: %s", job.source_job_id, exc)
            add_telemetry("REJECT", "enqueue_failed", gate_score=score, gate_reason_str=f"Exception: {exc}")

    _write_screening_telemetry(telemetry_records)
    record_screening_result(stats["passed"], stats["rejected"])
    record_jobs_enqueued(stats["enqueued"], stats["new"])
    return stats


def _ensure_monorepo_path() -> None:
    """Make sure the monorepo root is on sys.path."""
    root_str = str(_MONOREPO_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def run_discovery(
    *,
    profile: str = "it",
    portals: list[str] | None = None,
    dry_run: bool = False,
    max_results_per_term: int = 50,
    freshness_days: int | None = 7,
    timeout_seconds: int = 172800,
    search_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Top-level discovery entry point.

    Parameters
    ----------
    profile:
        ``"it"`` or ``"general"``.
    portals:
        Restrict to specific portals (e.g. ``["indeed", "linkedin"]``).
        ``None`` means all.
    dry_run:
        If ``True``, scrape + normalise + screen but do NOT enqueue.
    max_results_per_term:
        Per-term result cap per provider.
    freshness_days:
        Date freshness filter (``None`` for all dates).
    timeout_seconds:
        Per-provider timeout.
    search_terms:
        Optional search terms list override.

    Returns
    -------
    dict with keys: ``raw_count``, ``normalized_count``, ``deduped_count``,
    ``screened``, ``passed``, ``rejected``, ``enqueued``, ``new``,
    ``provider_results`` (per-provider counts).
    """
    _ensure_monorepo_path()

    with timed_run():
        _log.info(
            "Discovery starting: profile=%s portals=%s dry_run=%s",
            profile, portals, dry_run,
        )

        # Always refresh confirmation-email ledger before screening so
        # Glassdoor/Workopolis/Indeed already-applied twins are skipped
        # without a manual "check email" step.
        try:
            from jobbots.core.discovery.email_history_refresh import refresh_email_applied_history
            email_stats = refresh_email_applied_history()
            _log.info("Pre-discovery email ledger: %s", email_stats)
        except Exception as exc:
            _log.warning("Pre-discovery email ledger refresh failed (continuing): %s", exc)

        # Load search config. Explicit CLI/--keyword override applies to all portals
        # including LinkedIn; otherwise LinkedIn uses ``linkedin_search_terms``.
        terms_overridden = search_terms is not None
        if not search_terms:
            search_terms = _load_search_terms(profile)
        if not search_terms:
            _log.error("No search terms found for profile %r", profile)
            return {"error": "no_search_terms"}

        linkedin_terms = (
            list(search_terms)
            if terms_overridden
            else (_load_linkedin_search_terms(profile) or list(search_terms))
        )

        locations = _load_search_locations(profile, portals)
        search_policy = _load_search_policy(profile, portals)
        _log.info(
            "Loaded %d search terms (%d LinkedIn main), %d locations "
            "(easy_apply_only=%s)",
            len(search_terms),
            len(linkedin_terms),
            len(locations),
            search_policy.get("easy_apply_only"),
        )

        # Compile planning manifest
        active_portals = portals or ["indeed", "glassdoor", "linkedin", "workopolis"]
        linkedin_only = set(p.strip().lower() for p in active_portals) == {"linkedin"}
        manifest_terms = linkedin_terms if linkedin_only else search_terms
        est_queries = len(manifest_terms) * len(locations) * len(active_portals)
        if "linkedin" in active_portals and not linkedin_only and not terms_overridden:
            # Mixed run: LinkedIn uses the short main-term list.
            est_queries = (
                len(search_terms) * len(locations) * (len(active_portals) - 1)
                + len(linkedin_terms) * len(locations)
            )
        msg = (
            f"[Planner Manifest] Profile: {profile}, Portal: {', '.join(active_portals)}, "
            f"Keywords generated: {len(manifest_terms)}"
            + (
                f" (LinkedIn main: {len(linkedin_terms)})"
                if "linkedin" in active_portals and not terms_overridden
                else ""
            )
            + f", Locations generated: {len(locations)}, "
            f"Queries generated: {est_queries}, Maximum results per query: {max_results_per_term}, "
            f"Maximum expected raw jobs: {est_queries * max_results_per_term}"
        )
        _log.info(msg)
        print(msg)

        # Build request (JobSpy resolves proxies via scrape_proxy ladder;
        # Workopolis HTTP reads PROXY_URL itself.)
        request = DiscoveryRequest(
            profile=profile,
            search_terms=search_terms,
            locations=locations,
            max_results_per_term=max_results_per_term,
            freshness_days=freshness_days,
            **search_policy,
            proxies=None,
            timeout_seconds=timeout_seconds,
        )

        # Instantiate providers
        providers = _build_providers(portals)
        if not providers:
            _log.warning("No providers match the requested portals: %s", portals)
            return {"error": "no_providers"}

        linkedin_providers = [p for p in providers if _is_linkedin_jobspy(p)]
        other_providers = [p for p in providers if not _is_linkedin_jobspy(p)]
        linkedin_requested = bool(linkedin_providers)

        _log.info(
            "Dispatching %d provider(s): %s",
            len(providers),
            [p.name for p in providers],
        )

        all_raw: list[RawJob] = []
        all_deduped: list[NormalizedJob] = []
        provider_results: dict[str, int] = {}
        screening_stats: dict[str, int] = {}
        seen_identities: set[tuple[str, str]] = set()

        # ── LinkedIn JobSpy: short main-term list (parallel with other providers) ─
        # Historically LinkedIn ran fully *before* the ThreadPool (critical-path
        # tax). With ≤~30 main terms we run it in parallel. Batching is kept only
        # as an optional sequential mode via LINKEDIN_DISCOVERY_SEQUENTIAL=1.
        linkedin_sequential = os.getenv("LINKEDIN_DISCOVERY_SEQUENTIAL", "0").strip().lower() in {
            "1", "true", "yes", "on",
        }

        if linkedin_providers and linkedin_sequential:
            li_provider = linkedin_providers[0]
            batch_size = _linkedin_term_batch_size()
            term_batches = _chunk_terms(linkedin_terms, batch_size)
            _log.info(
                "LinkedIn JobSpy sequential: %d main terms → %d batch(es) of ≤%d",
                len(linkedin_terms),
                len(term_batches),
                batch_size,
            )
            li_key = getattr(li_provider, "name", "jobspy_linkedin")
            for batch_idx, term_batch in enumerate(term_batches, start=1):
                li_request = replace(request, search_terms=list(term_batch))
                try:
                    name, jobs = _run_provider(li_provider, li_request)
                except Exception as exc:
                    _log.error(
                        "LinkedIn batch %d/%d crashed: %s",
                        batch_idx, len(term_batches), exc,
                    )
                    record_provider_error(li_key, str(exc))
                    continue

                all_raw.extend(jobs)
                provider_results[name] = provider_results.get(name, 0) + len(jobs)

                normalized = _normalize_raw_jobs(
                    jobs,
                    locations=locations,
                    freshness_days=freshness_days,
                    provider_results=provider_results,
                )
                record_jobs_normalized(len(normalized))
                before_dedup = len(normalized)
                deduped = deduplicate(normalized)
                record_jobs_deduplicated(before_dedup, len(deduped))

                fresh: list[NormalizedJob] = []
                for job in deduped:
                    ident = _job_identity(job)
                    if not ident[1] or ident in seen_identities:
                        continue
                    seen_identities.add(ident)
                    fresh.append(job)

                batch_stats = _screen_and_enqueue(fresh, profile, dry_run=dry_run)
                _merge_screening_stats(screening_stats, batch_stats)
                all_deduped.extend(fresh)
                _log.info(
                    "LinkedIn batch %d/%d done: terms=%s raw=%d fresh=%d enqueued=%d",
                    batch_idx,
                    len(term_batches),
                    term_batch,
                    len(jobs),
                    len(fresh),
                    batch_stats.get("enqueued", 0),
                )
            linkedin_providers = []  # already finished

        # ── Providers in parallel (Indeed/Glassdoor/WP/Google + optional LI) ─
        pool_providers = list(other_providers)
        li_request_for_pool = None
        if linkedin_providers:
            # Parallel path: one LinkedIn request with full main-term list.
            li_request_for_pool = replace(request, search_terms=list(linkedin_terms))
            pool_providers = list(other_providers) + list(linkedin_providers)
            _log.info(
                "LinkedIn JobSpy parallel with other portals (%d main terms)",
                len(linkedin_terms),
            )

        if pool_providers:
            with ThreadPoolExecutor(max_workers=max(1, len(pool_providers))) as executor:
                futures = {}
                for p in pool_providers:
                    req = li_request_for_pool if _is_linkedin_jobspy(p) and li_request_for_pool is not None else request
                    futures[executor.submit(_run_provider, p, req)] = p.name
                for future in as_completed(futures):
                    pname = futures[future]
                    jobs: list[RawJob] = []
                    try:
                        name, jobs = future.result()
                        all_raw.extend(jobs)
                        provider_results[name] = provider_results.get(name, 0) + len(jobs)
                        _log.info("Provider %s returned %d raw jobs", name, len(jobs))
                    except Exception as exc:
                        _log.error("Provider %s crashed: %s", pname, exc)
                        record_provider_error(pname, str(exc))
                        provider_results[pname] = 0
                        continue

                    # Screen + enqueue each provider as it finishes so the
                    # application worker can start applying while slower
                    # providers (e.g. Workopolis browser fallback) still run.
                    if not jobs:
                        continue
                    if linkedin_sequential and all(
                        (j.source_platform or "").lower() == "linkedin" for j in jobs
                    ):
                        # LinkedIn already screened in sequential mode above.
                        continue
                    try:
                        normalized = _normalize_raw_jobs(
                            jobs,
                            locations=locations,
                            freshness_days=freshness_days,
                            provider_results=provider_results,
                        )
                        record_jobs_normalized(len(normalized))
                        before_dedup = len(normalized)
                        deduped = deduplicate(normalized)
                        record_jobs_deduplicated(before_dedup, len(deduped))

                        fresh = []
                        for job in deduped:
                            ident = _job_identity(job)
                            if not ident[1] or ident in seen_identities:
                                continue
                            seen_identities.add(ident)
                            fresh.append(job)

                        batch_stats = _screen_and_enqueue(fresh, profile, dry_run=dry_run)
                        _merge_screening_stats(screening_stats, batch_stats)
                        all_deduped.extend(fresh)
                        _log.info(
                            "Provider %s screened: raw=%d fresh=%d enqueued=%d",
                            name,
                            len(jobs),
                            len(fresh),
                            batch_stats.get("enqueued", 0),
                        )
                    except Exception as screen_exc:
                        _log.error(
                            "Provider %s screen/enqueue failed: %s",
                            pname,
                            screen_exc,
                        )

        raw_count = len(all_raw)
        _log.info("Total raw jobs from all providers: %d", raw_count)

        result = {
            "raw_count": raw_count,
            "normalized_count": len(all_deduped),
            "deduped_count": len(all_deduped),
            "deduped": len(all_deduped),
            "provider_results": provider_results,
            "linkedin_terms": len(linkedin_terms) if linkedin_requested else 0,
            "linkedin_batches": (
                len(_chunk_terms(linkedin_terms, _linkedin_term_batch_size()))
                if linkedin_requested
                else 0
            ),
            "linkedin_sequential": bool(linkedin_sequential) if linkedin_requested else False,
            **screening_stats,
        }

        _log.info("Discovery complete: %s", result)
        return result


def _engine_for_platform(
    platform: str, provider_results: dict[str, int]
) -> str:
    """Infer which engine name produced a given platform's results."""
    mapping = {
        "indeed": "jobspy",
        "glassdoor": "glassdoor_cdp",
        "linkedin": "jobspy",
        "workopolis": "workopolis_http",
        "google": "google_cdp",
        "company_apply": "ats_board_api",
        "greenhouse": "ats_board_api",
        "lever": "ats_board_api",
        "ashby": "ats_board_api",
        "bamboohr": "ats_board_api",
        "ats_board_api": "ats_board_api",
        "jobspipe": "jobspipe",
        "adzuna": "adzuna",
    }
    engine = mapping.get(platform, "unknown")
    # Prefer jobspy_linkedin / jobspy_indeed keys when present
    if platform == "linkedin" and any(
        k.startswith("jobspy") for k in provider_results
    ):
        engine = "jobspy"
    # Check if browser fallback was used for Workopolis
    if platform == "workopolis" and "workopolis_browser" in provider_results:
        engine = "workopolis_browser"
    return engine
