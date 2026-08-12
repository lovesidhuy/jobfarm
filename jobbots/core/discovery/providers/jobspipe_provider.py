"""JobsPipe discovery provider for automation-ready direct ATS leads.

JobsPipe aggregates many job sources.  It is deliberately used here as a
bounded supplement, not as an unverified Easy Apply source: only direct URLs
for ATS platforms the worker already supports are emitted by default.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import requests

from jobbots.core.discovery.ats_slugs import platform_for_url
from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest

_log = logging.getLogger("discovery.providers.jobspipe")
_URL = "https://api.jobspipe.dev/v1/jobs/search"
_SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "bamboohr"}


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _secret(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    try:
        from jobbots.core.secret_manager import get_secret
        return (get_secret(name) or "").strip()
    except Exception:
        return ""


def jobspipe_enabled() -> bool:
    return _truthy(os.getenv("JOBSPIPE_ENABLED")) and bool(_api_keys())


def _api_keys() -> list[str]:
    return [key for key in (_secret("JOBSPIPE_API_KEY"), _secret("JOBSPIPE_API_KEY_2")) if key]


def _int_env(name: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(os.getenv(name, str(default)) or default), maximum))
    except ValueError:
        return default


def _date(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10] or None


def _company(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("display_name") or "").strip()
    return str(value or "").strip()


def jobspipe_job_to_raw(job: dict[str, Any], *, search_term: str) -> RawJob | None:
    """Convert a JobsPipe record, retaining only a known direct ATS URL."""
    url = str(job.get("final_url") or job.get("url") or job.get("job_url") or "").strip()
    platform = platform_for_url(url)
    if platform not in _SUPPORTED_ATS:
        return None
    job_id = str(job.get("id") or job.get("job_id") or url).strip()
    title = str(job.get("job_title") or job.get("title") or "").strip()
    if not job_id or not title or not url:
        return None
    location = job.get("location") or job.get("location_name") or ""
    if isinstance(location, dict):
        location = location.get("display_name") or location.get("name") or ""
    remote = job.get("remote")
    return RawJob(
        source_platform=platform,
        source_job_id=f"jp-{job_id}",
        title=title,
        company=_company(job.get("company")),
        location=str(location).strip(),
        description=str(job.get("description") or "").strip(),
        listing_url=url,
        destination_url=url,
        date_posted=_date(job.get("date_posted") or job.get("posted_at")),
        easy_apply_evidence="jobspipe_direct_ats",
        is_remote=bool(remote) if isinstance(remote, bool) else ("remote" in str(location).lower()),
        raw_extras={
            "search_term": search_term,
            "discovered_by": "jobspipe",
            "jobspipe_id": job_id,
            "jobspipe_source_refs": job.get("source_refs") or [],
            "jobspipe_easy_apply": job.get("easy_apply"),
            "ats_platform": platform,
        },
    )


class JobsPipeProvider:
    name = "jobspipe"
    supported_platforms = ["greenhouse", "lever", "ashby", "bamboohr"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        keys = _api_keys()
        if not _truthy(os.getenv("JOBSPIPE_ENABLED")):
            _log.info("JobsPipe disabled (set JOBSPIPE_ENABLED=1 to enable)")
            return []
        if not keys:
            _log.warning("JobsPipe enabled but no API key is available")
            return []
        max_queries = _int_env("JOBSPIPE_MAX_QUERIES_PER_RUN", 6, 20)
        limit = min(request.max_results_per_term, _int_env("JOBSPIPE_MAX_RESULTS_PER_QUERY", 10, 25))
        pause = max(0.5, float(os.getenv("JOBSPIPE_REQUEST_PAUSE_SECONDS", "0.55") or "0.55"))
        locations = [x.strip() for x in request.locations if x.strip() and not request.is_remote_location(x)]
        jobs: list[RawJob] = []
        for index, term in enumerate(request.search_terms[:max_queries]):
            payload: dict[str, Any] = {
                "job_title_or": [term],
                "job_country_code_or": [os.getenv("JOBSPIPE_COUNTRY_CODE", "CA").upper()],
                "limit": limit,
            }
            if locations:
                payload["job_location_or"] = locations
            response = self._request(payload, keys, index)
            if response is None:
                continue
            for item in response:
                if not isinstance(item, dict):
                    continue
                raw = jobspipe_job_to_raw(item, search_term=term)
                if raw:
                    jobs.append(raw)
                    try:
                        from jobbots.core.discovery.slug_registry import register_slugs_from_url
                        register_slugs_from_url(raw.destination_url, source="jobspipe")
                    except Exception as exc:
                        _log.debug("JobsPipe slug registration skipped: %s", exc)
            if index + 1 < min(len(request.search_terms), max_queries):
                time.sleep(pause)
        return jobs

    @staticmethod
    def _request(payload: dict[str, Any], keys: list[str], offset: int) -> list[dict[str, Any]] | None:
        """Make one bounded request, failing over once to the other account."""
        for attempt in range(min(2, len(keys))):
            key = keys[(offset + attempt) % len(keys)]
            try:
                timeout = float(os.getenv("JOBSPIPE_TIMEOUT_SECONDS", "45") or "45")
                response = requests.post(
                    _URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=max(15.0, timeout),
                )
            except requests.RequestException as exc:
                _log.warning("JobsPipe request failed: %s", exc)
                continue
            if response.status_code == 200:
                body = response.json()
                data = body.get("data", []) if isinstance(body, dict) else []
                return data if isinstance(data, list) else []
            _log.warning("JobsPipe returned HTTP %s", response.status_code)
            if response.status_code not in {402, 429, 500, 502, 503, 504}:
                break
        return None
