"""Convert raw provider output to normalised job records.

Preserves ALL fields: descriptions, posting dates, locations, source IDs.
Never discards data that the screening gates need.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from jobbots.core.discovery.contracts import RawJob, NormalizedJob
from jobbots.core.discovery.classification.apply_type import classify_apply_type


NORMALIZER_VERSION = "1"


def _build_query_id(search_term: str, location: str, freshness_days: int | None) -> str:
    """Human-readable query identifier for traceability."""
    term_slug = search_term.strip().lower().replace(" ", "_")[:40]
    loc_slug = location.strip().lower().replace(" ", "_").replace(",", "")[:20]
    age = f"{freshness_days}d" if freshness_days else "all"
    return f"{term_slug}_{loc_slug}_{age}"


def normalize_raw_job(
    raw: RawJob,
    *,
    discovery_engine: str,
    search_term: str = "",
    location: str = "",
    freshness_days: int | None = None,
) -> NormalizedJob:
    """Convert a single ``RawJob`` into a ``NormalizedJob``.

    Parameters
    ----------
    raw:
        Raw job record from a discovery provider.
    discovery_engine:
        Identifier for the provider that produced the record
        (``"jobspy"`` | ``"linkedin_guest"`` | ``"workopolis_http"`` |
        ``"workopolis_browser"``).
    search_term:
        The search query that produced this result (for ``query_id``).
    location:
        The location filter that was active (for ``query_id``).
    freshness_days:
        The date freshness filter that was active (for ``query_id``).
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    job = NormalizedJob(
        source_platform=raw.source_platform.strip().lower(),
        source_job_id=str(raw.source_job_id).strip(),
        discovery_engine=discovery_engine,
        query_id=_build_query_id(search_term, location, freshness_days),
        job_title=raw.title.strip(),
        company_name=raw.company.strip(),
        location=raw.location.strip(),
        description=raw.description,  # never truncated
        date_posted=raw.date_posted,
        listing_url=raw.listing_url.strip(),
        destination_url=raw.destination_url,
        # Classification defaults — will be overwritten below
        apply_type="UNKNOWN",
        apply_type_source=raw.easy_apply_evidence or "not_verified",
        apply_type_confidence=0.0,
        verification_required=True,
        is_remote_hint=bool(
            raw.is_remote
            if raw.is_remote is not None
            else raw.raw_extras.get("is_remote") or False
        ),
        first_seen_at=now_iso,
        last_seen_at=now_iso,
        raw_payload_hash=raw.payload_hash(),
        normalizer_version=NORMALIZER_VERSION,
        source_refs=[{
            "platform": raw.source_platform.strip().lower(),
            "job_id": str(raw.source_job_id).strip(),
        }],
    )

    # Run evidence-based classification
    classification = classify_apply_type(job)
    job.apply_type = classification.apply_type
    job.apply_type_source = classification.source
    job.apply_type_confidence = classification.confidence
    job.verification_required = classification.verification_required
    job.apply_type_confirmed = bool(classification.confirmed)

    return job


def normalize_batch(
    raw_jobs: list[RawJob],
    *,
    discovery_engine: str,
    search_term: str = "",
    location: str = "",
    freshness_days: int | None = None,
) -> list[NormalizedJob]:
    """Normalise a batch of raw jobs, skipping any that fail validation."""
    results: list[NormalizedJob] = []
    for raw in raw_jobs:
        try:
            if not raw.source_job_id or not raw.title:
                continue
            job = normalize_raw_job(
                raw,
                discovery_engine=discovery_engine,
                search_term=search_term,
                location=location,
                freshness_days=freshness_days,
            )
            results.append(job)
        except Exception as exc:
            # Log but don't crash — partial success
            import logging
            logging.getLogger("discovery.normalizer").warning(
                "Failed to normalize job %s/%s: %s",
                raw.source_platform, raw.source_job_id, exc,
            )
    return results
