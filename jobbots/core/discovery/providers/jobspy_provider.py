"""JobSpy provider — concurrent HTTP discovery for job boards and Google ATS.

Uses ``python-jobspy`` (``jobspy.scrape_jobs``) with ``ThreadPoolExecutor``
internally. Scrape proxies use a smart ladder (local → webshare ↔ alternate →
dataimpulse) via ``core.discovery.scrape_proxy``.

JobSpy officially supports Indeed and Glassdoor concurrent scraping, proxy
lists, normalised DataFrame output, offsets and descriptions.  It uses
``ThreadPoolExecutor``, so this is **concurrent HTTP discovery**, not async.

Important constraints (from JobSpy docs):
- Indeed filters (e.g. is_remote, easy_apply) cannot always be combined with
  freshness.  The planner may need separate passes.
- Glassdoor locationAjax rejects ``City, BC`` (HTTP 400) and mis-resolves bare
  ``Richmond`` → Ontario. Wave B.1 normalises Glassdoor locations separately
  from Indeed and pauses between Glassdoor requests to avoid 429s.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.scrape_proxy import (
    ScrapeProxyLadder,
    build_scrape_proxy_ladder,
)

_log = logging.getLogger("discovery.providers.jobspy")


def _jobspy_full_description() -> bool:
    """Scout default False; set JOBSPY_FULL_DESCRIPTION=1 for deep JD harvest."""
    return os.getenv("JOBSPY_FULL_DESCRIPTION", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


# Glassdoor findPopularLocationAjax quirks → search terms that resolve to Metro Van.
_GLASSDOOR_LOCATION_MAP: dict[str, str] = {
    "vancouver, bc": "Vancouver",
    "vancouver, british columbia, canada": "Vancouver",
    "vancouver, british columbia": "Vancouver",
    "vancouver": "Vancouver",
    "surrey, bc": "Surrey",
    "surrey": "Surrey",
    "burnaby, bc": "Burnaby",
    "burnaby": "Burnaby",
    "richmond, bc": "Richmond BC",
    "richmond": "Richmond BC",  # bare Richmond → Richmond Hill, ON
    "richmond bc": "Richmond BC",
    "coquitlam, bc": "Coquitlam",
    "coquitlam": "Coquitlam",
    "langley, bc": "Langley",
    "langley": "Langley",
    "delta, bc": "Delta",
    "delta": "Delta",
    "white rock, bc": "White Rock BC",
    "white rock": "White Rock BC",
    "white rock bc": "White Rock BC",
    "north vancouver, bc": "North Vancouver",
    "north vancouver": "North Vancouver",
    "north vancouver bc": "North Vancouver",
    "new westminster, bc": "New Westminster",
    "new westminster": "New Westminster",
    "abbotsford, bc": "Abbotsford",
    "abbotsford": "Abbotsford",
    "maple ridge, bc": "Maple Ridge",
    "maple ridge": "Maple Ridge",
    "port coquitlam, bc": "Port Coquitlam",
    "port coquitlam": "Port Coquitlam",
    "port moody, bc": "Port Moody",
    "port moody": "Port Moody",
    # Metro / compound labels used by hero location lists
    "metro vancouver, bc": "Vancouver",
    "metro vancouver": "Vancouver",
    "greater vancouver, bc": "Vancouver",
    "greater vancouver": "Vancouver",
    "vancouver bc": "Vancouver",
    "vancouver, bc, canada": "Vancouver",
    "burnaby, bc, canada": "Burnaby",
    "surrey, bc, canada": "Surrey",
    "richmond, bc, canada": "Richmond BC",
    "coquitlam, bc, canada": "Coquitlam",
    "langley, bc, canada": "Langley",
    "delta, bc, canada": "Delta",
    "new westminster, bc, canada": "New Westminster",
    "north vancouver, bc, canada": "North Vancouver",
    "port coquitlam, bc, canada": "Port Coquitlam",
    "white rock, bc, canada": "White Rock BC",
    "abbotsford, bc, canada": "Abbotsford",
    "maple ridge, bc, canada": "Maple Ridge",
    "port moody, bc, canada": "Port Moody",
}


def normalize_glassdoor_location(location: str) -> str:
    """Map Indeed-style locations to Glassdoor locationAjax-friendly strings.

    JobSpy builds ``findPopularLocationAjax.htm?term={location}`` without
    URL-encoding, so spaces must be pre-encoded as ``%20``.
    """
    raw = (location or "").strip()
    if not raw:
        return raw
    # Already encoded by a previous normalise pass.
    if "%20" in raw:
        return raw
    key = re.sub(r"\s+", " ", raw.lower())
    if key in _GLASSDOOR_LOCATION_MAP:
        resolved = _GLASSDOOR_LOCATION_MAP[key]
    else:
        # Strip trailing ", BC" / ", British Columbia..." → often still 400; prefer
        # "City BC" for ambiguous names, bare city otherwise.
        stripped = re.sub(
            r",?\s*(british columbia|bc)(,?\s*canada)?\s*$",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        if stripped.lower() in {"richmond", "white rock"}:
            resolved = f"{stripped} BC"
        else:
            resolved = stripped or raw
    return resolved.replace(" ", "%20")


class JobSpyProvider:
    """Indeed/Glassdoor/LinkedIn/Google discovery via python-jobspy."""

    name = "jobspy"
    supported_platforms = ["indeed", "glassdoor", "linkedin", "google"]

    def __init__(self, portals: list[str] | None = None) -> None:
        self.portals = [p.strip().lower() for p in portals] if portals else ["indeed", "linkedin"]
        # Distinct names so parallel Indeed + LinkedIn providers do not clash in
        # planner ``provider_results``.
        if self.portals == ["linkedin"]:
            self.name = "jobspy_linkedin"
        elif self.portals == ["indeed"]:
            self.name = "jobspy_indeed"
        elif self.portals == ["glassdoor"]:
            self.name = "jobspy_glassdoor"
        elif self.portals == ["google"]:
            self.name = "jobspy_google"

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        """Scrape Indeed, Glassdoor and LinkedIn using JobSpy.

        Returns partial results on failure (e.g. Indeed blocked but
        Glassdoor succeeds).

        When both Indeed and Glassdoor are requested, scrapes are split so
        each portal gets a location string its location parser accepts.
        """
        try:
            from jobspy import scrape_jobs
        except ImportError:
            _log.error(
                "python-jobspy is not installed. "
                "Run: pip install python-jobspy==1.1.82"
            )
            return []

        ladder = build_scrape_proxy_ladder()
        all_jobs: list[RawJob] = []
        glassdoor_only = set(self.portals) == {"glassdoor"}
        pause = float(os.getenv("GLASSDOOR_REQUEST_PAUSE_SECONDS", "3.0") or 0)

        # Split portals so Glassdoor gets normalised locations.
        portal_groups: list[list[str]] = []
        if "indeed" in self.portals:
            portal_groups.append(["indeed"])
        if "glassdoor" in self.portals:
            portal_groups.append(["glassdoor"])
        if "linkedin" in self.portals:
            portal_groups.append(["linkedin"])
        if "google" in self.portals:
            portal_groups.append(["google"])
        if not portal_groups:
            portal_groups = [list(self.portals)]

        for term in request.search_terms:
            for location in request.locations:
                for portals in portal_groups:
                    loc = location
                    portal_name = portals[0] if portals else "indeed"
                    if portals == ["glassdoor"]:
                        loc = normalize_glassdoor_location(location)
                        if pause > 0:
                            time.sleep(pause)
                    try:
                        from jobbots.core.discovery.term_productivity import should_skip
                        if should_skip(portal_name, term, loc):
                            continue
                    except Exception:
                        pass
                    try:
                        from jobbots.core.discovery.serp_cache import get_raw_jobs, jobs_from_dicts, put_raw_jobs
                        cached = get_raw_jobs(portal_name, term, loc)
                        if cached is not None:
                            jobs = jobs_from_dicts(cached)
                            all_jobs.extend(jobs)
                            continue
                    except Exception:
                        pass
                    try:
                        jobs = self._scrape_term(
                            scrape_jobs,
                            term=term,
                            location=loc,
                            request=request,
                            ladder=ladder,
                            portals_override=portals,
                        )
                        all_jobs.extend(jobs)
                        try:
                            from jobbots.core.discovery.serp_cache import put_raw_jobs
                            put_raw_jobs(portal_name, term, loc, jobs)
                        except Exception:
                            pass
                    except Exception as exc:
                        _log.warning(
                            "JobSpy scrape failed for term=%r location=%r portals=%s: %s",
                            term, loc, portals, exc,
                        )
                        ladder.note_failure(exc)
                        # Partial success — continue with remaining terms
                    if glassdoor_only and pause > 0:
                        time.sleep(pause)

        _log.info(
            "JobSpy total raw jobs: %d (final proxy tier=%s)",
            len(all_jobs), ladder.current_label(),
        )
        # Flywheel: capture GH/Lever board slugs from destination URLs so the
        # ats_board_api provider can poll them directly next cycle.
        try:
            from jobbots.core.discovery.slug_registry import register_slugs_from_url

            for job in all_jobs:
                for url in (job.destination_url, job.listing_url):
                    if url:
                        register_slugs_from_url(url, source="jobspy")
        except Exception:
            pass
        return all_jobs

    def _scrape_term(
        self,
        scrape_fn: Any,
        *,
        term: str,
        location: str,
        request: DiscoveryRequest,
        ladder: ScrapeProxyLadder,
        portals_override: list[str] | None = None,
    ) -> list[RawJob]:
        """Single search-term scrape for one JobSpy portal.

        Indeed metro / local locations use **two logical passes** (Wave A.1):

          1. Easy Apply filtered pass — Indeed's Easy Apply filter is on; every
             returned row is tagged ``indeed_easy_apply_filtered_pass``
             (confirmed Easy Apply). Provenance comes from the *pass*, not from
             JobSpy's unreliable per-row ``easy_apply`` column.
          2. All/local leads pass — no Easy Apply filter; captures company-site
             and unknown jobs for bookmark/verification. Never labels a job
             company-site merely because Easy Apply is absent.

        Empty / ``Remote`` locations remain a single Easy Apply–filtered pass
        (``remote_easy_apply``). That provenance confirms Easy Apply only;
        ``is_remote`` is omitted so the Indeed filter applies, then rows are
        post-filtered client-side. Outside-Metro geo policy still requires
        independent row-level remote evidence (``is_remote_hint`` / location).
        LinkedIn normally runs only the Easy Apply-filtered pass. When
        ``LINKEDIN_EXTERNAL_ATS_DISCOVERY=1`` is enabled, it also runs a
        description-fetching all-listings pass. JobSpy can expose LinkedIn's
        ``applyUrl`` as ``job_url_direct`` for external Greenhouse/Lever
        applications; those URLs are then preserved for the downstream ATS
        classifier. This pass is opt-in because it is slower and LinkedIn
        does not expose an external URL for every listing.
        """
        portals = portals_override or self.portals
        portal_name = (portals[0] if portals else "").strip().lower()
        linkedin_only = portal_name == "linkedin"
        google_only = portal_name == "google"
        remote_pass = request.is_remote_location(location)
        base_kwargs: dict[str, Any] = {
            "site_name": portals,
            "search_term": term,
            "location": location,
            "results_wanted": request.max_results_per_term,
            "country_indeed": "canada",
            # Scout default: title/card only. Full JD is expensive proxy bandwidth;
            # set JOBSPY_FULL_DESCRIPTION=1 for deep harvest / company-site nights.
            "full_description": _jobspy_full_description(),
        }
        if google_only:
            # python-jobspy's Google scraper consumes google_search_term rather
            # than search_term and returns Google result/apply URLs.
            base_kwargs["google_search_term"] = term
        if request.freshness_days is not None:
            base_kwargs["hours_old"] = request.freshness_days * 24
        # proxies injected per-pass via ladder

        raw_jobs: list[RawJob] = []

        if google_only:
            return self._run_pass(
                scrape_fn,
                base_kwargs,
                term=term,
                location=location,
                pass_name="google_ats",
                force_easy_apply_evidence=False,
                freshness_days=request.freshness_days,
                ladder=ladder,
            )

        if remote_pass:
            kwargs = dict(base_kwargs)
            # Indeed cannot combine is_remote + easy_apply; prefer Easy Apply
            # confirmation and post-filter remote client-side.
            kwargs["is_remote"] = True
            kwargs["easy_apply"] = True
            raw_jobs.extend(
                self._run_pass(
                    scrape_fn, kwargs,
                    term=term, location=location,
                    pass_name="linkedin_remote_easy_apply" if linkedin_only else "remote_easy_apply",
                    force_easy_apply_evidence=True,
                    freshness_days=request.freshness_days,
                    ladder=ladder,
                )
            )
            return raw_jobs

        # ── Metro / local: Easy Apply pass (confirmed) ────────────────────
        if request.radius_km > 0:
            base_kwargs["distance"] = request.radius_km

        ea_kwargs = dict(base_kwargs)
        ea_kwargs["easy_apply"] = True
        raw_jobs.extend(
            self._run_pass(
                scrape_fn, ea_kwargs,
                term=term, location=location,
                pass_name="linkedin_easy_apply" if linkedin_only else "metro_easy_apply",
                force_easy_apply_evidence=True,
                freshness_days=request.freshness_days,
                ladder=ladder,
            )
        )

        if linkedin_only:
            external_ats_enabled = os.getenv(
                "LINKEDIN_EXTERNAL_ATS_DISCOVERY", "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            if external_ats_enabled and not request.easy_apply_only:
                external_kwargs = dict(base_kwargs)
                external_kwargs["linkedin_fetch_description"] = True
                raw_jobs.extend(
                    self._run_pass(
                        scrape_fn,
                        external_kwargs,
                        term=term,
                        location=location,
                        pass_name="linkedin_external_ats",
                        force_easy_apply_evidence=False,
                        freshness_days=request.freshness_days,
                        ladder=ladder,
                    )
                )
            return raw_jobs

        # ── Metro / local: all-leads pass (company-site + unknown) ─────────
        # Skip when the caller already asked for easy-apply-only.
        if not request.easy_apply_only:
            all_kwargs = dict(base_kwargs)
            # Explicitly do NOT set easy_apply — this pass must surface
            # company-site and unverified leads. hours_old is allowed here.
            raw_jobs.extend(
                self._run_pass(
                    scrape_fn, all_kwargs,
                    term=term, location=location,
                    pass_name="metro_all_leads",
                    force_easy_apply_evidence=False,
                    freshness_days=request.freshness_days,
                    ladder=ladder,
                )
            )

        return raw_jobs

    def _run_pass(
        self,
        scrape_fn: Any,
        kwargs: dict[str, Any],
        *,
        term: str,
        location: str,
        pass_name: str,
        force_easy_apply_evidence: bool,
        freshness_days: int | None = None,
        ladder: ScrapeProxyLadder | None = None,
    ) -> list[RawJob]:
        """Execute one JobSpy scrape pass.

        Indeed accepts only **one** of ``hours_old`` / ``easy_apply`` /
        ``is_remote(+job_type)`` (JobSpy if/elif). When this pass needs a
        confirmed Easy Apply filter, we **omit** ``hours_old`` and
        ``is_remote`` so ``indeedApplyScope`` is actually applied, then
        optionally post-filter by ``date_posted`` / remote hints.
        """
        call_kwargs = dict(kwargs)
        client_freshness_days = None
        client_remote_only = False

        if call_kwargs.get("easy_apply"):
            # Provenance pass: Easy Apply filter must win. Drop conflicting filters.
            if "hours_old" in call_kwargs:
                client_freshness_days = freshness_days
                call_kwargs.pop("hours_old", None)
                _log.info(
                    "JobSpy pass=%s: omitting hours_old so Indeed easy_apply filter applies "
                    "(will post-filter freshness client-side if needed)",
                    pass_name,
                )
            if call_kwargs.pop("is_remote", None):
                client_remote_only = True
                _log.info(
                    "JobSpy pass=%s: omitting is_remote so Indeed easy_apply filter applies "
                    "(will post-filter remote client-side)",
                    pass_name,
                )

        _log.info(
            "JobSpy pass=%s term=%r location=%r max=%d freshness=%s easy_apply=%s remote=%s proxy=%s",
            pass_name, term, location, call_kwargs.get("results_wanted"),
            f"{call_kwargs.get('hours_old')}h" if call_kwargs.get("hours_old") else (
                f"client:{client_freshness_days}d" if client_freshness_days else "all"
            ),
            bool(call_kwargs.get("easy_apply")), bool(call_kwargs.get("is_remote")),
            (ladder.current_label() if ladder else "n/a"),
        )
        df = self._scrape_with_proxy_ladder(scrape_fn, call_kwargs, ladder=ladder)
        if df is None or df.empty:
            _log.info("JobSpy pass=%s term=%r location=%r → 0 jobs", pass_name, term, location)
            return []

        raw_jobs: list[RawJob] = []
        for _, row in df.iterrows():
            try:
                raw = self._row_to_raw_job(
                    row, term,
                    search_pass=pass_name,
                    force_easy_apply_evidence=force_easy_apply_evidence,
                )
                if client_freshness_days is not None and not _within_freshness(
                    raw.date_posted, client_freshness_days
                ):
                    continue
                if client_remote_only and not _looks_remote(raw):
                    continue
                raw_jobs.append(raw)
            except Exception as exc:
                _log.debug("Skipping malformed JobSpy row: %s", exc)

        _log.info(
            "JobSpy pass=%s term=%r location=%r → %d jobs",
            pass_name, term, location, len(raw_jobs),
        )
        return raw_jobs

    def _scrape_with_proxy_ladder(
        self,
        scrape_fn: Any,
        kwargs: dict[str, Any],
        *,
        ladder: ScrapeProxyLadder | None,
    ) -> Any:
        """Run JobSpy with smart proxy escalate on rate-limit / proxy errors."""
        attempts = 0
        max_attempts = 3 if ladder and ladder.mode == "smart" else 1
        last_exc: Exception | None = None

        while attempts < max_attempts:
            attempts += 1
            call = dict(kwargs)
            proxies = ladder.current_proxies() if ladder else None
            if proxies:
                call["proxies"] = proxies
            else:
                call.pop("proxies", None)
            try:
                df = self._scrape_with_freshness_fallback(scrape_fn, call)
                if ladder:
                    # Soft-block: Glassdoor often returns empty on 429 without raising.
                    if (df is None or getattr(df, "empty", True)) and any(site in str(
                        call.get("site_name") or ""
                    ).lower() for site in ("glassdoor", "google")):
                        if ladder.note_soft_block():
                            continue
                    else:
                        ladder.note_success()
                return df
            except Exception as exc:
                last_exc = exc
                if ladder:
                    escalated = ladder.note_failure(exc)
                    _log.warning(
                        "JobSpy proxy tier=%s error (%s)%s",
                        ladder.current_label(),
                        exc,
                        "; retrying next tier" if escalated else "",
                    )
                    if escalated:
                        continue
                raise

        if last_exc:
            raise last_exc
        return None

    @staticmethod
    def _scrape_with_freshness_fallback(scrape_fn: Any, kwargs: dict[str, Any]) -> Any:
        """Run JobSpy, retrying without ``hours_old`` if the filter combination
        fails.

        JobSpy cannot always combine Indeed's ``easy_apply`` / ``is_remote``
        filters with ``hours_old`` freshness.  When both are present and the
        scrape raises, we drop freshness (never the geo/apply-type policy) and
        retry once so the remote easy-apply pass still returns results.
        """
        try:
            return scrape_fn(**kwargs)
        except Exception as exc:
            if "hours_old" in kwargs and (kwargs.get("easy_apply") or kwargs.get("is_remote")):
                retry = {k: v for k, v in kwargs.items() if k != "hours_old"}
                _log.warning(
                    "JobSpy filter+freshness combo failed (%s); retrying without freshness",
                    exc,
                )
                return scrape_fn(**retry)
            raise

    @staticmethod
    def _row_to_raw_job(
        row: Any,
        search_term: str,
        *,
        search_pass: str = "",
        force_easy_apply_evidence: bool = False,
    ) -> RawJob:
        """Convert a pandas DataFrame row from JobSpy to RawJob.

        When ``force_easy_apply_evidence`` is True, every row is tagged with
        portal-specific filtered-pass evidence — the filter itself is the
        Do not rely on JobSpy's per-row ``easy_apply`` column alone.
        """
        site = str(row.get("site", "") or "").strip().lower()
        platform = "indeed" if "indeed" in site else (
            "glassdoor" if "glassdoor" in site else (
                "linkedin" if "linkedin" in site else (
                    "google" if "google" in site else site
                )
            )
        )

        job_id = str(row.get("id", "") or "").strip()
        if not job_id:
            job_id = str(row.get("job_url_direct", "") or "").strip()

        row_easy = _coerce_bool(row.get("easy_apply"))
        if force_easy_apply_evidence:
            evidence = (
                "linkedin_easy_apply_filtered_pass"
                if platform == "linkedin"
                else "indeed_easy_apply_filtered_pass"
            )
        elif row_easy is True:
            evidence = "jobspy_easy_apply_filtered_search"
        else:
            evidence = ""

        listing_url = str(row.get("job_url", "") or "").strip()
        if platform == "linkedin":
            listing_url = _canonical_linkedin_url(job_id, listing_url)
        dest_url = str(row.get("job_url_direct", "") or "").strip() or None
        if platform == "google" and not _is_ats_destination(dest_url):
            # Google often places the ATS URL in job_url and leaves
            # job_url_direct empty. Keep only validated ATS destinations for
            # downstream Greenhouse/Lever application routing.
            candidate = str(row.get("job_url", "") or "").strip()
            dest_url = candidate if _is_ats_destination(candidate) else None

        is_remote_val = _coerce_bool(row.get("is_remote"))

        return RawJob(
            source_platform=platform,
            source_job_id=job_id,
            title=str(row.get("title", "") or "").strip(),
            company=str(row.get("company", "") or "").strip(),
            location=str(row.get("location", "") or "").strip(),
            description=str(row.get("description", "") or "").strip(),
            listing_url=listing_url,
            destination_url=dest_url,
            date_posted=_parse_date(row.get("date_posted")),
            easy_apply_evidence=evidence,
            is_remote=is_remote_val,
            raw_extras={
                "search_term": search_term,
                "search_pass": search_pass,
                "site": site,
                "is_remote": is_remote_val,
                "jobspy_easy_apply_row": row_easy,
                "salary_source": str(row.get("salary_source", "") or ""),
                "min_amount": row.get("min_amount"),
                "max_amount": row.get("max_amount"),
                "interval": str(row.get("interval", "") or ""),
                "job_type": str(row.get("job_type", "") or ""),
                "num_urgent_words": row.get("num_urgent_words"),
                "company_industry": str(row.get("company_industry", "") or ""),
            },
        )


def _is_ats_destination(url: str | None) -> bool:
    """True for direct Greenhouse/Lever/Ashby/BambooHR application URLs."""
    return bool(re.search(
        r"(?:greenhouse\.io|grnh\.se|lever\.co|ashbyhq\.com|bamboohr\.com)",
        (url or ""),
        re.IGNORECASE,
    ))


def _within_freshness(date_posted: str | None, freshness_days: int) -> bool:
    """Return True if date_posted is within freshness_days (inclusive)."""
    if not date_posted or freshness_days is None:
        return True
    try:
        from datetime import datetime, timezone, timedelta
        posted = datetime.strptime(str(date_posted)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(freshness_days))
        return posted >= cutoff
    except Exception:
        return True  # keep if unparseable


def _looks_remote(raw: RawJob) -> bool:
    """Client-side remote check when Indeed is_remote filter was omitted."""
    if raw.is_remote is True:
        return True
    blob = f"{raw.location} {raw.title} {raw.description[:500]}".lower()
    if "hybrid" in blob:
        return False
    return any(tok in blob for tok in ("remote", "work from home", "wfh", "fully remote"))


def _coerce_bool(val: Any) -> bool | None:
    """Best-effort truthiness for a JobSpy cell (handles NaN / strings)."""
    if val is None:
        return None
    try:
        import pandas as pd
        if pd.isna(val):
            return None
    except Exception:
        pass
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "y", "remote"):
        return True
    if s in ("false", "0", "no", "n", "nan", "none", ""):
        return False
    return None


def _canonical_linkedin_url(job_id: str, listing_url: str) -> str:
    """Return the direct LinkedIn job URL consumed by the apply worker."""
    import re
    candidate = str(job_id or "").strip()
    if not candidate:
        candidate = str(listing_url or "").strip()
    match = re.search(r"(?:/jobs/view/|[?&]currentJobId=)([0-9]+)", candidate)
    if not match:
        match = re.search(r"(?:/jobs/view/|[?&]currentJobId=)([0-9]+)", str(listing_url or ""))
    if match:
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"
    return str(listing_url or "").strip()


def _parse_date(val: Any) -> str | None:
    """Best-effort date string extraction from a JobSpy row value."""
    if val is None:
        return None
    import pandas as pd
    if isinstance(val, pd.Timestamp):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    return s[:10]  # "YYYY-MM-DD"
