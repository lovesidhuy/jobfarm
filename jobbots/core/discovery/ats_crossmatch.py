"""LinkedIn → ATS crossmatch — reverse-engineer hidden apply URLs.

LinkedIn search yields full job metadata (company, title, location) even when
it hides the off-site apply URL (verified: ``<code id="applyUrl">`` is gone
from the guest page). For non-Easy-Apply Metro-Van jobs this module answers:

    "Did this company post the SAME job on its Greenhouse/Lever board?"

If yes, the board posting is a first-class lead: directly applyable by the
ATS applier. The LinkedIn row was the *sensor*; the board is the *target*.

Matching pipeline
-----------------
1. **Company → slug resolution** — normalise the LinkedIn company string
   (``"AbCellera Biologics, Inc."`` → ``abcellera`` / ``abcellerabiologics``)
   and look it up in the slug registry's active slugs. Conservative rules
   only: exact, space-removed, hyphen-variant, and long-prefix matches —
   no fuzzy company guessing (false leads are worse than no leads).
2. **Board job index** — live GH/Lever jobs already polled by
   ``ats_board_api`` (geo-qualified), indexed by slug.
3. **Title match** — normalised token containment + ``difflib`` ratio; a
   match requires both token overlap ≥ threshold and ratio ≥ threshold so
   "Software Engineer II, Backend" ≠ "Senior Software Engineer, Backend".

The output RawJob carries the *board* identity (so it dedupes against the
direct board poll) plus full LinkedIn provenance in ``raw_extras``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.slug_registry import SlugRegistry

_log = logging.getLogger("discovery.ats_crossmatch")

# ---------------------------------------------------------------------------
# Company normalisation
# ---------------------------------------------------------------------------

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|ltd|limited|corp|corporation|co|company|llc|llp|"
    r"gmbh|pty|plc|holdings?|group|technologies|technology|tech|labs?|"
    r"software|systems|solutions?|industries|enterprises|ventures?)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_company(name: str | None) -> str:
    """Lowercase, strip legal suffixes + punctuation → compact compare key."""
    s = (name or "").strip().lower()
    s = re.sub(r"[.,'&()\-]", " ", s)
    s = _LEGAL_SUFFIX_RE.sub(" ", s)
    s = _NON_ALNUM_RE.sub("", s)
    return s


def company_slug_candidates(name: str | None) -> list[str]:
    """Ordered slug candidates for a company display name (most→least likely)."""
    raw = (name or "").strip()
    if not raw:
        return []
    compact = normalize_company(raw)
    # Keep suffix-bearing variant too: "Rival Technologies" → rivaltechnologies.
    suffixy = _NON_ALNUM_RE.sub(
        "", re.sub(r"[.,'&()\-]", " ", raw.lower())
    )
    tokens = [t for t in re.split(r"\s+", raw.lower()) if t]
    first = _NON_ALNUM_RE.sub("", tokens[0]) if tokens else ""
    cands: list[str] = []
    for c in (compact, suffixy, first):
        if c and len(c) >= 3 and c not in cands:
            cands.append(c)
    return cands


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

_TITLE_NOISE_RE = re.compile(r"[^a-z0-9 ]+")

# Seniority/level tokens must agree exactly — "SWE II" ≠ "Senior SWE",
# "Staff Engineer" ≠ "Engineer". A wrong match is worse than no match.
_SENIORITY_TOKENS = frozenset({
    "senior", "sr", "junior", "jr", "staff", "principal", "lead",
    "director", "head", "manager", "intern", "internship", "coop",
    "entry", "ii", "iii", "iv", "vp", "chief", "executive",
})


def _title_tokens(title: str) -> set[str]:
    norm = (title or "").lower().replace("co-op", "coop")
    return {
        t for t in _TITLE_NOISE_RE.sub("", norm).split()
        if len(t) > 1
    }


def _seniority_signature(title: str) -> frozenset[str]:
    return frozenset(_title_tokens(title) & _SENIORITY_TOKENS)


def titles_match(a: str, b: str, *, min_overlap: float = 0.75, min_ratio: float = 0.80) -> tuple[bool, float]:
    """Three-gate title match. Returns (matched, score).

    Gate 1 — seniority signature must be identical (kills level drift).
    Gate 2 — token containment ≥ ``min_overlap``; full containment (one
             title is a subset of the other, e.g. parenthetical suffixes
             like ``(12-month term)``) accepts immediately.
    Gate 3 — partial containment additionally requires sequence ratio ≥
             ``min_ratio`` (kills same-tokens-different-role).
    """
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False, 0.0
    if _seniority_signature(a) != _seniority_signature(b):
        return False, 0.0
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    if overlap < min_overlap:
        return False, round(overlap, 3)
    if overlap >= 1.0:
        return True, 1.0
    ratio = SequenceMatcher(
        None, " ".join(sorted(ta)), " ".join(sorted(tb))
    ).ratio()
    return (ratio >= min_ratio), round(min(overlap, ratio), 3)


# ---------------------------------------------------------------------------
# Crossmatch
# ---------------------------------------------------------------------------

@dataclass
class CrossmatchStats:
    linkedin_jobs: int = 0
    companies_resolved: int = 0
    companies_unresolved: int = 0
    matches: int = 0


def _board_index(board_jobs: Iterable[RawJob]) -> dict[str, list[RawJob]]:
    idx: dict[str, list[RawJob]] = {}
    for job in board_jobs:
        slug = (job.raw_extras or {}).get("board_slug") or ""
        if slug:
            idx.setdefault(slug, []).append(job)
    return idx


def resolve_company_slug(
    company: str,
    active_slugs: set[str],
) -> tuple[str | None, str]:
    """Map a display name to a registry slug. Returns (slug, method)."""
    for cand in company_slug_candidates(company):
        if cand in active_slugs:
            return cand, "exact" if cand == normalize_company(company) else "variant"
    # Long-prefix: registry slug that is a prefix of the compact name (≥6 chars)
    # e.g. slug "abcellera" ⊂ "abcellerabiologics".
    compact = normalize_company(company)
    if len(compact) >= 6:
        for slug in active_slugs:
            if len(slug) >= 6 and compact.startswith(slug):
                return slug, "prefix"
    return None, "none"


def crossmatch_linkedin_jobs(
    linkedin_jobs: list[RawJob],
    board_jobs: list[RawJob],
    registry: SlugRegistry | None,
    *,
    min_overlap: float = 0.75,
    min_ratio: float = 0.80,
) -> tuple[list[RawJob], CrossmatchStats]:
    """Match LinkedIn jobs to live GH/Lever board postings.

    Only LinkedIn jobs **without** a usable apply URL need crossmatching
    (Easy Apply and direct-URL rows are already actionable). Matching is
    company-scoped: titles are only compared within the resolved board.
    """
    stats = CrossmatchStats(linkedin_jobs=len(linkedin_jobs))
    if registry is None or not board_jobs:
        return [], stats

    try:
        active_slugs = {
            (rec.get("slug_id") or "").strip()
            for rec in registry.iter_active_slugs()
        } - {""}
    except Exception as exc:
        _log.debug("crossmatch: registry unavailable: %s", exc)
        return [], stats
    if not active_slugs:
        return [], stats

    by_slug = _board_index(board_jobs)
    matched: list[RawJob] = []
    seen_board_ids: set[str] = set()

    for li in linkedin_jobs:
        # Skip rows that are already actionable (Easy Apply evidence or an
        # external ATS destination URL) — crossmatch exists for the rest.
        if li.easy_apply_evidence:
            continue
        dest = (li.destination_url or "").strip()
        if dest and "linkedin.com" not in dest:
            continue

        slug, method = resolve_company_slug(li.company, active_slugs)
        if not slug:
            stats.companies_unresolved += 1
            continue
        stats.companies_resolved += 1

        best: tuple[float, RawJob | None] = (0.0, None)
        for bj in by_slug.get(slug, []):
            if bj.source_job_id in seen_board_ids:
                continue
            ok, score = titles_match(
                li.title, bj.title, min_overlap=min_overlap, min_ratio=min_ratio
            )
            if ok and score > best[0]:
                best = (score, bj)

        if best[1] is None:
            continue
        board_job = best[1]
        seen_board_ids.add(board_job.source_job_id)
        stats.matches += 1

        # Emit the BOARD posting as the lead, with LinkedIn provenance.
        matched.append(
            RawJob(
                source_platform=board_job.source_platform,
                source_job_id=board_job.source_job_id,
                title=board_job.title,
                company=li.company.strip() or board_job.company,
                location=board_job.location,
                description=board_job.description,
                listing_url=board_job.listing_url,
                destination_url=board_job.destination_url,
                date_posted=board_job.date_posted,
                easy_apply_evidence="",  # external ATS → COMPANY_APPLY downstream
                is_remote=board_job.is_remote,
                raw_extras={
                    **(board_job.raw_extras or {}),
                    "discovered_by": "linkedin_ats_crossmatch",
                    "crossmatch_score": best[0],
                    "crossmatch_slug_method": method,
                    "linkedin_url": li.listing_url,
                    "linkedin_title": li.title,
                    "linkedin_job_id": li.source_job_id,
                },
            )
        )
        _log.info(
            "crossmatch: %r @ %s → %s (%s score=%.2f)",
            li.title[:50], li.company[:25],
            (board_job.destination_url or "")[:80], slug, best[0],
        )

    _log.info(
        "crossmatch: linkedin=%d resolved=%d unresolved=%d matches=%d",
        stats.linkedin_jobs, stats.companies_resolved,
        stats.companies_unresolved, stats.matches,
    )
    return matched, stats
