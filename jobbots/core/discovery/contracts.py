"""Core data contracts for the discovery engine.

Every discovery provider emits ``RawJob`` objects.  The normalizer converts them
to ``NormalizedJob``.  The compatibility adapter converts ``NormalizedJob`` to
``QueueRecord`` — the exact field set expected by
``enqueue_approved_job()`` / ``JobQueue.enqueue()`` / ``application_worker.py``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Raw job — returned by providers
# ---------------------------------------------------------------------------

@dataclass
class RawJob:
    """Unprocessed job record as returned by a discovery provider."""

    source_platform: str  # "indeed" | "glassdoor" | "linkedin" | "workopolis"
    source_job_id: str  # platform-native ID (jk, LinkedIn numeric id, etc.)
    title: str
    company: str
    location: str = ""
    description: str = ""
    listing_url: str = ""
    destination_url: str | None = None
    date_posted: str | None = None  # ISO date string, e.g. "2026-07-10"
    easy_apply_evidence: str = ""  # provenance tag, e.g. "linkedin_easy_apply_filter_click"
    # Provider work-mode hint (e.g. JobSpy ``is_remote`` column). ``None`` = unknown.
    is_remote: bool | None = None
    raw_extras: dict[str, Any] = field(default_factory=dict)

    def payload_hash(self) -> str:
        """SHA-256 fingerprint of the serialised raw payload."""
        blob = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Normalized job — output of normalizer + classification
# ---------------------------------------------------------------------------

@dataclass
class NormalizedJob:
    """Fully normalised, classified, and de-duplicated job record."""

    source_platform: str
    source_job_id: str
    discovery_engine: str  # "jobspy" | "linkedin_guest" | "workopolis_http" | "workopolis_browser"
    query_id: str  # e.g. "it_support_vancouver_7d"

    job_title: str
    company_name: str
    location: str
    description: str  # full text — never discarded
    date_posted: str | None

    listing_url: str
    destination_url: str | None

    # Apply classification (set by classification/apply_type.py)
    apply_type: str = "UNKNOWN"  # "EASY_APPLY" | "COMPANY_APPLY" | "UNKNOWN"
    apply_type_source: str = "not_verified"
    apply_type_confidence: float = 0.0
    verification_required: bool = True
    # True only when apply_type was confirmed by filtered-pass provenance,
    # an explicit provider flag, or a positively identified external ATS URL.
    # Absence of Easy Apply evidence must NEVER set this for COMPANY_APPLY.
    apply_type_confirmed: bool = False

    # Provider work-mode hint (from JobSpy ``is_remote`` etc.). Used by the
    # geo/work-mode policy to enforce "remote-only outside Metro Vancouver".
    is_remote_hint: bool = False

    first_seen_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_payload_hash: str = ""
    normalizer_version: str = "1"

    # Cross-platform references — merged by deduplicator
    source_refs: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Queue record — compatibility layer for existing downstream
# ---------------------------------------------------------------------------

@dataclass
class QueueRecord:
    """Exact field set consumed by ``enqueue_approved_job()`` →
    ``JobQueue.enqueue()`` → ``application_worker.py``.

    Field names intentionally match the keyword arguments of
    ``core.shared_modules.job_queue_bridge.enqueue_approved_job()``.
    """

    portal: str  # "indeed" | "glassdoor" | "linkedin" | "workopolis"
    profile: str  # "it" | "general"
    source_job_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    gate_score: int | None  # filled after AI screening
    gate_reason: str  # filled after AI screening
    resume_policy: str  # "tailored" | "default"
    initial_status: str  # "queued"
    application_method: str  # "easy_apply" | "company_site" | "unverified"
    # Phase I-B geo classification (e.g. "METRO_VAN"). Persisted into queue
    # metadata so Phase II can defensively confirm lease-and-verify is Metro-Van.
    region: str = ""
    # Set only after the confirmed company-site listing passes the batched AI
    # save review; Phase II may bookmark only records carrying this approval.
    company_ai_approved: bool = False
