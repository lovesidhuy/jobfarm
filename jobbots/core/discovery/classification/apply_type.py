"""Evidence-based apply-type classification.

Three states: ``EASY_APPLY``, ``COMPANY_APPLY``, ``UNKNOWN``.

An Indeed or Glassdoor listing URL usually points to the job-board detail page
regardless of whether the eventual application stays on the platform or
redirects externally.  **We never classify from the listing URL alone.**
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from jobbots.core.discovery.contracts import NormalizedJob


# Known external ATS domains that indicate COMPANY_APPLY.
_EXTERNAL_ATS_DOMAINS: frozenset[str] = frozenset({
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "icims.com",
    "smartrecruiters.com",
    "jobs.smartrecruiters.com",
    "applytojob.com",
    "jobvite.com",
    "jobs.jobvite.com",
    "ultipro.com",
    "successfactors.com",
    "successfactors.eu",
    "taleo.net",
    "brassring.com",
    "avature.net",
    "phenom.com",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "bamboohr.com",
    "workable.com",
    "apply.workable.com",
    "breezy.hr",
    "recruitee.com",
    "teamtailor.com",
    "dayforcehcm.com",
    "workforcenow.adp.com",
    "myjobs.adp.com",
    "paylocity.com",
    "recruiting.paylocity.com",
    "jazz.co",
    "jazzhr.com",
    "pinpointhq.com",
    "eightfold.ai",
    "fountain.com",
    "hireology.com",
    "oraclecloud.com",
    "rec.pro.workday.com",
    "gh.io",
    "join.com",
    "ripplingats.com",
    "gohire.io",
})

# Evidence tags that positively indicate EASY_APPLY.
# Jobs returned from an Indeed Easy Apply *filtered search pass* are confirmed
# Easy Apply even when JobSpy's per-row ``easy_apply`` column is missing/False.
_EASY_APPLY_EVIDENCE_TAGS: frozenset[str] = frozenset({
    "indeed_easy_apply_filtered_pass",
    "linkedin_easy_apply_filtered_pass",
    "jobspy_easy_apply_filtered_search",  # legacy alias (row flag or older passes)
    "linkedin_easy_apply_filter_click",
    "workopolis_quick_apply_badge",
    "glassdoor_easy_apply_badge",
    # legacy CDP mis-tag (pre-fix); still accept so old scrapes classify
    "glassdoor_easy_apply_filtered_search",
})


@dataclass
class ApplyClassification:
    """Result of evidence-based apply-type classification."""

    apply_type: str  # "EASY_APPLY" | "COMPANY_APPLY" | "UNKNOWN"
    confidence: float  # 0.0 – 1.0
    source: str  # human-readable provenance
    verification_required: bool
    confirmed: bool = False


def _is_external_ats(url: str | None) -> bool:
    """Return *True* if *url* points to a known external ATS domain."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in _EXTERNAL_ATS_DOMAINS)
    except Exception:
        return False


def classify_apply_type(job: NormalizedJob) -> ApplyClassification:
    """Classify the apply type for a normalised job record.

    Rules
    -----
    1. Job has positive easy-apply evidence (Indeed Easy Apply filtered pass,
       LinkedIn Easy Apply filter click, Workopolis Quick-apply badge, or an
       explicit JobSpy easy_apply row flag from a filtered search)
       → ``EASY_APPLY``, confirmed, verification not required.

    2. ``destination_url`` points to a known external ATS domain
       → ``COMPANY_APPLY``, confirmed, verification not required.

    3. No positive evidence
       → ``UNKNOWN``, not confirmed, verification required.

    Never classify as COMPANY_APPLY merely because an Easy Apply field is absent.
    """
    evidence = (job.apply_type_source or "").strip()

    # ── Rule 1: explicit easy-apply evidence ──────────────────────────────
    if evidence in _EASY_APPLY_EVIDENCE_TAGS:
        return ApplyClassification(
            apply_type="EASY_APPLY",
            confidence=0.9,
            source=evidence,
            verification_required=False,
            confirmed=True,
        )

    # ── Rule 2: confirmed external destination URL ────────────────────────
    if _is_external_ats(job.destination_url):
        return ApplyClassification(
            apply_type="COMPANY_APPLY",
            confidence=0.85,
            source=f"external_ats_url:{urlparse(job.destination_url or '').hostname}",
            verification_required=False,
            confirmed=True,
        )

    # ── Rule 3: no positive evidence — preserve UNKNOWN (never guess) ─────
    return ApplyClassification(
        apply_type="UNKNOWN",
        confidence=0.0,
        source="not_verified",
        verification_required=True,
        confirmed=False,
    )
