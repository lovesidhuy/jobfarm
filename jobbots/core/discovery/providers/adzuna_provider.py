"""Adzuna discovery fallback, restricted to resolvable direct ATS destinations."""
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

_log = logging.getLogger("discovery.providers.adzuna")
_SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "bamboohr"}


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _secret(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    try:
        from jobbots.core.secret_manager import get_secret
        return (get_secret(name) or "").strip()
    except Exception:
        return ""


def _accounts() -> list[tuple[str, str]]:
    pairs = [(_secret("ADZUNA_APP_ID"), _secret("ADZUNA_APP_KEY")), (_secret("ADZUNA_APP_ID_2"), _secret("ADZUNA_APP_KEY_2"))]
    return [(app_id, key) for app_id, key in pairs if app_id and key]


def adzuna_enabled() -> bool:
    return _truthy(os.getenv("ADZUNA_ENABLED")) and bool(_accounts())


def _int_env(name: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(os.getenv(name, str(default)) or default), maximum))
    except ValueError:
        return default


def _date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10] or None


def adzuna_result_to_raw(result: dict[str, Any], *, search_term: str, destination_url: str) -> RawJob | None:
    """Turn a redirect-resolved Adzuna result into a supported direct ATS lead."""
    platform = platform_for_url(destination_url)
    title = str(result.get("title") or "").strip()
    job_id = str(result.get("id") or "").strip()
    if platform not in _SUPPORTED_ATS or not title or not job_id:
        return None
    company = result.get("company") or {}
    location = result.get("location") or {}
    location_text = location.get("display_name") if isinstance(location, dict) else location
    return RawJob(
        source_platform=platform,
        source_job_id=f"adz-{job_id}",
        title=title,
        company=str(company.get("display_name") if isinstance(company, dict) else company or "").strip(),
        location=str(location_text or "").strip(),
        description=str(result.get("description") or "").strip(),
        listing_url=destination_url,
        destination_url=destination_url,
        date_posted=_date(result.get("created")),
        easy_apply_evidence="adzuna_resolved_direct_ats",
        is_remote="remote" in f"{title} {location_text}".lower(),
        raw_extras={"search_term": search_term, "discovered_by": "adzuna", "adzuna_id": job_id, "adzuna_redirect_url": result.get("redirect_url"), "ats_platform": platform},
    )


class AdzunaProvider:
    name = "adzuna"
    supported_platforms = ["greenhouse", "lever", "ashby", "bamboohr"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        accounts = _accounts()
        if not _truthy(os.getenv("ADZUNA_ENABLED")):
            _log.info("Adzuna disabled (set ADZUNA_ENABLED=1 to enable)")
            return []
        if not accounts:
            _log.warning("Adzuna enabled but no account is available")
            return []
        max_queries = _int_env("ADZUNA_MAX_QUERIES_PER_RUN", 4, 20)
        per_query = min(request.max_results_per_term, _int_env("ADZUNA_RESULTS_PER_QUERY", 10, 50))
        terms = request.search_terms[:max_queries]
        locations = [x for x in request.locations if x.strip() and not request.is_remote_location(x)]
        where = (locations[0] if locations else "Canada").strip()
        jobs: list[RawJob] = []
        for index, term in enumerate(terms):
            results = self._search(term, where, per_query, accounts, index)
            for result in results:
                redirect = str(result.get("redirect_url") or "").strip()
                destination = self._resolve_direct_ats(redirect)
                if not destination:
                    continue
                raw = adzuna_result_to_raw(result, search_term=term, destination_url=destination)
                if raw:
                    jobs.append(raw)
                    try:
                        from jobbots.core.discovery.slug_registry import register_slugs_from_url
                        register_slugs_from_url(destination, source="adzuna")
                    except Exception as exc:
                        _log.debug("Adzuna slug registration skipped: %s", exc)
            if index + 1 < len(terms):
                time.sleep(max(0.3, float(os.getenv("ADZUNA_REQUEST_PAUSE_SECONDS", "0.5") or "0.5")))
        return jobs

    @staticmethod
    def _search(term: str, where: str, per_query: int, accounts: list[tuple[str, str]], offset: int) -> list[dict[str, Any]]:
        for attempt in range(min(2, len(accounts))):
            app_id, app_key = accounts[(offset + attempt) % len(accounts)]
            try:
                response = requests.get(
                    "https://api.adzuna.com/v1/api/jobs/ca/search/1",
                    params={"app_id": app_id, "app_key": app_key, "what": term, "where": where, "results_per_page": per_query, "content-type": "application/json"},
                    timeout=25,
                )
            except requests.RequestException as exc:
                _log.warning("Adzuna request failed: %s", exc)
                continue
            if response.status_code == 200:
                body = response.json()
                results = body.get("results", []) if isinstance(body, dict) else []
                return results if isinstance(results, list) else []
            _log.warning("Adzuna returned HTTP %s", response.status_code)
            if response.status_code not in {403, 429, 500, 502, 503, 504}:
                break
        return []

    @staticmethod
    def _resolve_direct_ats(url: str) -> str | None:
        if not url:
            return None
        try:
            response = requests.get(url, timeout=20, allow_redirects=True, stream=True, headers={"User-Agent": "Mozilla/5.0"})
            destination = response.url
            response.close()
        except requests.RequestException:
            return None
        return destination if platform_for_url(destination) in _SUPPORTED_ATS else None
