"""Geographic normalizer for ATS board locations.

Greenhouse/Lever boards carry free-text locations (``"Vancouver - Hybrid"``,
``"Remote, Canada"``, ``"Anywhere - Western Canada"``, ``"Toronto / Vancouver"``).
This module canonicalises that drift into a structured decision *before* jobs
hit the geo policy screens in ``location_policy``.

Pipeline
--------
``resolve_ats_location(raw, search_locations=...)``
  → split multi-location strings (``/``, ``|``, ``•``, ``;``)
  → classify each fragment (metro city / BC / Canada-remote / other / US-guard)
  → pick the best fragment (Metro Van > BC remote-eligible > Canada remote)
  → return ``AtsGeoResolution`` with canonical location + region + remote flags

The ``region`` value reuses ``location_policy.classify_region`` so downstream
policy (``decide_job_policy``) sees exactly the vocabulary it already knows:
``METRO_VAN`` | ``OTHER`` | ``UNKNOWN``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobbots.core.discovery.classification.location_policy import (
    REGION_METRO_VAN,
    REGION_OTHER,
    REGION_UNKNOWN,
    classify_region,
)

# ---------------------------------------------------------------------------
# Fragment splitters — multi-location boards
# ---------------------------------------------------------------------------
_SPLIT_RE = re.compile(r"\s*(?:/|\||•|·|;|\bor\b\s*/\s*)\s*", re.IGNORECASE)

# Metro Van + BC evidence (superset of location_policy's list, matched loosely
# because ATS text adds noise like "(on-site)" / "- Hybrid").
_METRO_VAN_RE = re.compile(
    r"\b("
    r"metro vancouver|greater vancouver|lower mainland|"
    r"vancouver|north vancouver|west vancouver|surrey|richmond|burnaby|"
    r"coquitlam|port coquitlam|port moody|new westminster|delta|ladner|"
    r"tsawwassen|langley|white rock|maple ridge|pitt meadows|anmore|belcarra|"
    r"bowen island|lions bay"
    r")\b",
    re.IGNORECASE,
)
_BC_RE = re.compile(
    r"\b(british columbia|bc|victoria|kelowna|kamloops|nanaimo|prince george)\b",
    re.IGNORECASE,
)
_CANADA_RE = re.compile(r"\b(canada|canadian)\b", re.IGNORECASE)
_REMOTE_RE = re.compile(
    r"\b(remote|anywhere|work from home|wfh|distributed|telecommute)\b",
    re.IGNORECASE,
)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]?office|onsite)\b", re.IGNORECASE)
# US guard — "Vancouver, WA" / "Portland, OR" must not match Metro Van.
_US_STATE_RE = re.compile(
    r",\s*(wa|washington|or|oregon|ca|california|ny|new york|tx|texas|ma|"
    r"massachusetts|il|illinois|co|colorado|ga|georgia|nc|fl|florida|az|"
    r"ut|nv|tn|va|md|dc|pa|oh|mi|mn|wi|nj|ct|ri|mo|in|ky|sc|al|la|ok|ks|"
    r"ia|ne|ar|ms|nm|id|mt|wy|nd|sd|ak|hi|me|nh|vt|de)\b"
    r"|\b(usa|united states|u\.s\.)\b",
    re.IGNORECASE,
)
# Canadian provinces other than BC — remote-eligible when tagged remote.
_OTHER_CA_PROVINCE_RE = re.compile(
    r"\b(ontario|on|toronto|ottawa|waterloo|alberta|ab|calgary|edmonton|"
    r"quebec|qc|montreal|manitoba|mb|winnipeg|saskatchewan|nova scotia|"
    r"halifax|new brunswick|pei|newfoundland)\b",
    re.IGNORECASE,
)

REMOTE_SCOPE_NONE = "none"
REMOTE_SCOPE_BC = "bc"
REMOTE_SCOPE_CANADA = "canada"
REMOTE_SCOPE_GLOBAL = "global"


@dataclass
class AtsGeoResolution:
    """Structured geo verdict for one ATS location string."""

    raw: str
    canonical_location: str
    region: str  # METRO_VAN | OTHER | UNKNOWN (location_policy vocabulary)
    is_remote: bool = False
    remote_scope: str = REMOTE_SCOPE_NONE
    work_mode_hint: str = ""  # "hybrid" | "onsite" | "" (policy refines later)
    in_search_area: bool = False
    matched_fragment: str = ""
    notes: list[str] = field(default_factory=list)


def _strip_mode_noise(fragment: str) -> str:
    """Remove parenthetical mode annotations that break city matching."""
    s = re.sub(r"\((?:on[- ]?site|hybrid|remote)[^)]*\)", "", fragment, flags=re.IGNORECASE)
    s = re.sub(r"\s*[-–—]\s*(hybrid|remote|on[- ]?site)\b.*$", "", s, flags=re.IGNORECASE)
    return s.strip(" ,-")


def _classify_fragment(fragment: str, res: AtsGeoResolution) -> tuple[int, str]:
    """Score one fragment. Higher = better for our search area.

    Returns ``(score, canonical)``:
      5 = Metro Vancouver, 4 = elsewhere in BC, 3 = Canada (remote ok),
      1 = known other/foreign, 0 = unknown.
    """
    frag = fragment.strip()
    if not frag:
        return 0, ""
    low = frag.lower()
    us_guarded = bool(_US_STATE_RE.search(low)) and not _BC_RE.search(low) and not _CANADA_RE.search(low)

    has_metro = bool(_METRO_VAN_RE.search(low))
    has_bc = bool(_BC_RE.search(low))
    has_ca = bool(_CANADA_RE.search(low))
    has_remote = bool(_REMOTE_RE.search(low))

    if has_metro and not us_guarded:
        return 5, _canonical_metro(frag)
    if has_bc and not us_guarded:
        return 4, _canonical_bc(frag, remote=has_remote)
    if has_ca or ("remote" in low and not us_guarded and not _OTHER_CA_PROVINCE_RE.search(low)):
        # "Remote, Canada" / "Anywhere - Western Canada" / bare "Remote"
        if has_remote or "anywhere" in low or "western canada" in low:
            return 3, "Remote, Canada"
        if has_ca:
            return 3, _canonical_bc(frag, remote=has_remote) if has_bc else frag
    if _OTHER_CA_PROVINCE_RE.search(low) and not has_bc:
        # Toronto/Montreal etc. — Canada but outside our geo fence.
        return 2 if has_remote else 1, frag
    if us_guarded:
        return 1, frag
    return 0, frag


def _canonical_metro(fragment: str) -> str:
    m = _METRO_VAN_RE.search(fragment)
    city = m.group(1) if m else fragment
    city = re.sub(r"\s+", " ", city.strip()).title()
    # Normalise "Vancouver" variants to one token.
    if city.lower() in {"north vancouver", "west vancouver"}:
        return f"{city}, BC"
    return f"{city}, BC"


def _canonical_bc(fragment: str, *, remote: bool) -> str:
    if remote:
        return "Remote, BC" if _BC_RE.search(fragment) else "Remote, Canada"
    m = re.search(r"\b(victoria|kelowna|kamloops|nanaimo|prince george)\b", fragment, re.IGNORECASE)
    if m:
        return f"{m.group(1).title()}, BC"
    return "British Columbia, Canada" if _CANADA_RE.search(fragment) else "BC, Canada"


def resolve_ats_location(
    raw_location: str | None,
    *,
    search_locations: list[str] | None = None,
) -> AtsGeoResolution:
    """Map one loose ATS location string to a structured verdict.

    ``search_locations`` is accepted for interface parity with the policy
    layer; matching is rule-based (Metro Van / BC / Canada-remote), not a
    literal string compare, so drift like ``"Vancouver - Hybrid"`` resolves.
    """
    raw = (raw_location or "").strip()
    res = AtsGeoResolution(
        raw=raw,
        canonical_location=raw or "Unknown",
        region=REGION_UNKNOWN,
    )
    if not raw:
        res.notes.append("empty_location")
        return res

    if _HYBRID_RE.search(raw):
        res.work_mode_hint = "hybrid"
    elif _ONSITE_RE.search(raw):
        res.work_mode_hint = "onsite"

    fragments = [f for f in _SPLIT_RE.split(raw) if f.strip()]
    if not fragments:
        fragments = [raw]

    best_score = -1
    best_canonical = ""
    best_fragment = ""
    for frag in fragments:
        cleaned = _strip_mode_noise(frag)
        score, canonical = _classify_fragment(cleaned or frag, res)
        if score > best_score:
            best_score, best_canonical, best_fragment = score, canonical, frag

    if best_score >= 5:
        res.region = REGION_METRO_VAN
        res.canonical_location = best_canonical
        res.in_search_area = True
        res.remote_scope = REMOTE_SCOPE_NONE
    elif best_score == 4:
        res.region = REGION_OTHER  # BC outside Metro Van
        res.canonical_location = best_canonical
        res.is_remote = _REMOTE_RE.search(raw) is not None
        res.remote_scope = REMOTE_SCOPE_BC
        # BC remote is eligible (policy treats confirmed remote + EASY_APPLY ok)
        res.in_search_area = res.is_remote
        res.notes.append("bc_outside_metro")
    elif best_score == 3:
        res.region = REGION_OTHER
        res.canonical_location = best_canonical or "Remote, Canada"
        res.is_remote = True
        res.remote_scope = REMOTE_SCOPE_CANADA
        res.in_search_area = True  # remote-Canada passes geo fence
        res.notes.append("canada_remote")
    elif best_score == 2:
        res.region = REGION_OTHER
        res.canonical_location = best_canonical or raw
        res.is_remote = True
        res.remote_scope = REMOTE_SCOPE_CANADA
        res.in_search_area = True
        res.notes.append("other_province_remote")
    elif best_score == 1:
        res.region = REGION_OTHER
        res.canonical_location = best_canonical or raw
        res.in_search_area = False
        res.notes.append("foreign_or_us")
    else:
        # Unknown — let location_policy make the final call on the raw string.
        res.region = classify_region(raw)
        res.canonical_location = raw
        res.in_search_area = res.region == REGION_METRO_VAN
        res.notes.append("unclassified_fallback")

    res.matched_fragment = best_fragment
    # Remote flag for hybrid metro text like "Vancouver - Hybrid": not remote.
    if res.region == REGION_METRO_VAN:
        res.is_remote = False
    return res
