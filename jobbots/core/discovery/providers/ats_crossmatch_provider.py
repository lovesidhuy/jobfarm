"""LinkedIn→ATS crossmatch provider — reverse-engineer hidden apply URLs.

Strategy (multi-way lead discovery, way #3):
  1. Scrape LinkedIn guest search for Metro-Van jobs — full metadata
     (company/title/location) even though LinkedIn hides the off-site apply
     URL (verified: ``applyUrl`` is gone from the guest page).
  2. Poll the slug registry's GH/Lever boards via ``ats_board_api``
     (companies we already know use Greenhouse/Lever).
  3. Crossmatch: LinkedIn company → registry slug, LinkedIn title → board
     title. A match means the company posted the same job on its ATS board —
     emit the board posting as the lead (directly applyable).

Only LinkedIn rows **without** their own actionable URL are crossmatched;
Easy Apply rows and external-URL rows stay with their original providers.

Env
---
``ATS_CROSSMATCH_ENABLED``        default ``1``
``ATS_CROSSMATCH_MAX_PER_TERM``   LinkedIn results per term (default ``40``)
``ATS_CROSSMATCH_MIN_OVERLAP``    title token overlap gate (default ``0.75``)
``ATS_CROSSMATCH_MIN_RATIO``      title sequence-ratio gate (default ``0.80``)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from jobbots.core.discovery.ats_crossmatch import crossmatch_linkedin_jobs
from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.providers.ats_board_api import AtsBoardApiProvider
from jobbots.core.discovery.providers.jobspy_provider import JobSpyProvider
from jobbots.core.discovery.scrape_proxy import build_scrape_proxy_ladder
from jobbots.core.discovery.slug_registry import SlugRegistry, get_registry

_log = logging.getLogger("discovery.providers.ats_crossmatch")


def _enabled() -> bool:
    return os.getenv("ATS_CROSSMATCH_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _max_per_term() -> int:
    try:
        return max(5, min(int(os.getenv("ATS_CROSSMATCH_MAX_PER_TERM", "40") or "40"), 100))
    except ValueError:
        return 40


def _min_overlap() -> float:
    try:
        return float(os.getenv("ATS_CROSSMATCH_MIN_OVERLAP", "0.75") or "0.75")
    except ValueError:
        return 0.75


def _min_ratio() -> float:
    try:
        return float(os.getenv("ATS_CROSSMATCH_MIN_RATIO", "0.80") or "0.80")
    except ValueError:
        return 0.80


class AtsCrossmatchProvider:
    """DiscoveryProvider: LinkedIn sensor + GH/Lever board crossmatch."""

    name = "ats_crossmatch"
    supported_platforms = ["greenhouse", "lever"]

    def __init__(self, registry: SlugRegistry | None = None) -> None:
        self._registry = registry

    # -- LinkedIn sensor scrape -------------------------------------------
    def _scrape_linkedin(self, request: DiscoveryRequest) -> list[RawJob]:
        """One all-listings LinkedIn pass per term (no easy-apply filter).

        Reuses JobSpy's row converter but deliberately does NOT force
        easy-apply evidence — crossmatch only needs unactionable rows.
        """
        try:
            from jobspy import scrape_jobs
        except ImportError:
            _log.error("python-jobspy is not installed")
            return []

        ladder = build_scrape_proxy_ladder()
        converter = JobSpyProvider._row_to_raw_job
        jobs: list[RawJob] = []

        for term in request.search_terms:
            for location in request.locations:
                kwargs: dict[str, Any] = {
                    "site_name": ["linkedin"],
                    "search_term": term,
                    "location": location,
                    "results_wanted": _max_per_term(),
                    "country_indeed": "canada",
                }
                if request.freshness_days is not None:
                    kwargs["hours_old"] = request.freshness_days * 24
                proxies = ladder.current_proxies()
                if proxies:
                    kwargs["proxies"] = proxies
                try:
                    df = scrape_jobs(**kwargs)
                    ladder.note_success()
                except Exception as exc:
                    _log.warning("crossmatch LinkedIn scrape failed %r/%r: %s", term, location, exc)
                    ladder.note_failure(exc)
                    continue
                for _, row in df.iterrows():
                    try:
                        raw = converter(
                            row, term,
                            search_pass="linkedin_crossmatch_sensor",
                            force_easy_apply_evidence=False,
                        )
                        jobs.append(raw)
                    except Exception:
                        continue
        _log.info("crossmatch: LinkedIn sensor returned %d jobs", len(jobs))
        return jobs

    # -- Provider entry ----------------------------------------------------
    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        if not _enabled():
            _log.info("ats_crossmatch disabled via ATS_CROSSMATCH_ENABLED")
            return []
        try:
            registry = self._registry or get_registry()
        except Exception as exc:
            _log.warning("ats_crossmatch: registry unavailable: %s", exc)
            return []

        # Board jobs via the shared poller (geo-qualified already).
        board_jobs = AtsBoardApiProvider(registry=registry).discover(request)
        if not board_jobs:
            _log.info("ats_crossmatch: no board jobs — nothing to match against")
            return []

        linkedin_jobs = self._scrape_linkedin(request)
        if not linkedin_jobs:
            _log.info("ats_crossmatch: LinkedIn sensor returned 0 jobs")
            return []

        matched, stats = crossmatch_linkedin_jobs(
            linkedin_jobs,
            board_jobs,
            registry,
            min_overlap=_min_overlap(),
            min_ratio=_min_ratio(),
        )
        _log.info(
            "ats_crossmatch: linkedin=%d resolved=%d unresolved=%d matches=%d",
            stats.linkedin_jobs, stats.companies_resolved,
            stats.companies_unresolved, stats.matches,
        )
        return matched
