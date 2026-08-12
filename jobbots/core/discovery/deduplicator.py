"""Four-level deduplication for normalised discovery records.

Priority order
--------------
1. ``source_platform`` + ``source_job_id``  (exact match)
2. Canonical destination URL                 (normalised URL comparison)
3. Normalised company + title + location     (case-insensitive, stripped)
4. Description fingerprint                   (supporting signal only — never
   used as the sole dedup key)

Cross-posted jobs retain all their source references in ``source_refs``.
When a duplicate is found the first-seen record wins by default; ``source_refs``
and ``last_seen_at`` are merged from the duplicate. Wave B.1: **Indeed wins**
over Glassdoor when the same job is cross-posted (platform identity promoted).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from jobbots.core.discovery.contracts import NormalizedJob


_PLATFORM_RANK = {
    "indeed": 40,
    "linkedin": 30,
    "workopolis": 20,
    "glassdoor": 10,
}


def _canonical_url(url: str | None) -> str:
    """Normalise a URL for comparison (lower host, strip tracking params)."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().lstrip("www.")
        # Strip common tracking params
        qs = parse_qs(parsed.query, keep_blank_values=False)
        for drop in ("utm_source", "utm_medium", "utm_campaign",
                      "utm_content", "utm_term", "ref", "src",
                      "from", "trk", "refId", "trackingId"):
            qs.pop(drop, None)
        clean_qs = urlencode(qs, doseq=True)
        return urlunparse(("", host, parsed.path.rstrip("/"), "", clean_qs, ""))
    except Exception:
        return url.strip().lower()


def _normalize_text(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _company_title_location_key(job: NormalizedJob) -> str:
    """Level-3 key: normalised company + title + location."""
    parts = [
        _normalize_text(job.company_name),
        _normalize_text(job.job_title),
        _normalize_text(job.location),
    ]
    return "|".join(parts)


def _description_fingerprint(desc: str) -> str:
    """SHA-256 of normalised description text (supporting signal only)."""
    normed = _normalize_text(desc)
    if len(normed) < 50:
        # Too short to be a reliable fingerprint
        return ""
    return hashlib.sha256(normed.encode("utf-8")).hexdigest()


def _merge_source_refs(
    existing_refs: list[dict[str, str]],
    new_refs: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge source references, avoiding duplicates."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, str]] = []
    for ref in existing_refs + new_refs:
        key = (ref.get("platform", ""), ref.get("job_id", ""))
        if key not in seen:
            seen.add(key)
            merged.append(ref)
    return merged


def deduplicate(jobs: list[NormalizedJob]) -> list[NormalizedJob]:
    """Deduplicate a list of normalised jobs using four-level matching.

    Returns the deduplicated list.  The first-seen record for each group
    is kept (with Indeed preferred over Glassdoor on cross-post); duplicates
    contribute their ``source_refs`` and update ``last_seen_at``.
    """
    # Prefer Indeed before Glassdoor so first-seen wins aligns with Wave B.1.
    ordered = sorted(
        jobs,
        key=lambda j: -_PLATFORM_RANK.get(
            (j.source_platform or "").strip().lower(), 0
        ),
    )

    # Indexes for each dedup level
    by_platform_id: dict[str, int] = {}  # "platform:job_id" → index
    by_canonical_url: dict[str, int] = {}  # canonical URL → index
    by_ctl: dict[str, int] = {}  # company+title+location → index
    # Description fingerprint is only a supporting signal, stored for
    # tie-breaking / logging but never used as the sole dedup key.

    result: list[NormalizedJob] = []

    for job in ordered:
        # ── Level 1: source_platform + source_job_id ──────────────────────
        l1_key = f"{job.source_platform}:{job.source_job_id}"
        if l1_key in by_platform_id:
            _absorb(result[by_platform_id[l1_key]], job)
            continue

        # ── Level 2: canonical destination URL ────────────────────────────
        canon = _canonical_url(job.destination_url or job.listing_url)
        if canon and canon in by_canonical_url:
            _absorb(result[by_canonical_url[canon]], job)
            continue

        # ── Level 3: normalised company + title + location ────────────────
        ctl = _company_title_location_key(job)
        if ctl in by_ctl:
            # Level 4 check: if descriptions are both present and their
            # fingerprints differ, this is probably a different position
            # at the same company — do NOT dedup.
            existing = result[by_ctl[ctl]]
            fp_new = _description_fingerprint(job.description)
            fp_old = _description_fingerprint(existing.description)
            if fp_new and fp_old and fp_new != fp_old:
                # Different descriptions → distinct positions, keep both
                pass
            else:
                _absorb(existing, job)
                continue

        # ── New unique job ────────────────────────────────────────────────
        idx = len(result)
        result.append(job)
        by_platform_id[l1_key] = idx
        if canon:
            by_canonical_url[canon] = idx
        by_ctl[ctl] = idx

    return result


def _absorb(winner: NormalizedJob, duplicate: NormalizedJob) -> None:
    """Merge *duplicate* into *winner* (in-place)."""
    winner.source_refs = _merge_source_refs(winner.source_refs, duplicate.source_refs)
    # Update last_seen_at to the more recent timestamp
    try:
        if duplicate.last_seen_at > winner.last_seen_at:
            winner.last_seen_at = duplicate.last_seen_at
    except Exception:
        pass
    # If winner lacks a description but duplicate has one, adopt it
    if not winner.description and duplicate.description:
        winner.description = duplicate.description
    # Prefer confirmed Easy Apply over company-site / unknown when the same
    # job appears in both Metro Van discovery passes (Wave A.1).
    _prefer_apply_type(winner, duplicate)
    # Wave B.1: Indeed identity wins over Glassdoor on cross-post.
    _prefer_platform(winner, duplicate)


def _prefer_platform(winner: NormalizedJob, duplicate: NormalizedJob) -> None:
    """Promote a higher-ranked platform identity onto *winner* (Indeed > Glassdoor)."""
    w_plat = (winner.source_platform or "").strip().lower()
    d_plat = (duplicate.source_platform or "").strip().lower()
    if _PLATFORM_RANK.get(d_plat, 0) <= _PLATFORM_RANK.get(w_plat, 0):
        return
    winner.source_platform = duplicate.source_platform
    winner.source_job_id = duplicate.source_job_id
    winner.discovery_engine = duplicate.discovery_engine
    if duplicate.listing_url:
        winner.listing_url = duplicate.listing_url
    if duplicate.destination_url:
        winner.destination_url = duplicate.destination_url


_APPLY_RANK = {"EASY_APPLY": 3, "COMPANY_APPLY": 2, "UNKNOWN": 1}


def _prefer_apply_type(winner: NormalizedJob, duplicate: NormalizedJob) -> None:
    """Keep the stronger apply-type evidence on *winner*."""
    w = (winner.apply_type or "UNKNOWN").upper()
    d = (duplicate.apply_type or "UNKNOWN").upper()
    if _APPLY_RANK.get(d, 0) > _APPLY_RANK.get(w, 0):
        winner.apply_type = duplicate.apply_type
        winner.apply_type_source = duplicate.apply_type_source
        winner.apply_type_confidence = duplicate.apply_type_confidence
        winner.verification_required = duplicate.verification_required
        winner.apply_type_confirmed = bool(getattr(duplicate, "apply_type_confirmed", False))
        return
    if w == "UNKNOWN" and d != "UNKNOWN":
        winner.apply_type = duplicate.apply_type
        winner.apply_type_source = duplicate.apply_type_source
        winner.apply_type_confidence = duplicate.apply_type_confidence
        winner.verification_required = duplicate.verification_required
        winner.apply_type_confirmed = bool(getattr(duplicate, "apply_type_confirmed", False))
        return
    # Same type: prefer confirmed / higher confidence.
    if d == w and bool(getattr(duplicate, "apply_type_confirmed", False)) and not bool(
        getattr(winner, "apply_type_confirmed", False)
    ):
        winner.apply_type_source = duplicate.apply_type_source
        winner.apply_type_confidence = max(
            float(winner.apply_type_confidence or 0),
            float(duplicate.apply_type_confidence or 0),
        )
        winner.apply_type_confirmed = True
        winner.verification_required = duplicate.verification_required
