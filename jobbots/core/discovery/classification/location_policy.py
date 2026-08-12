"""Geo + work-mode + apply-type policy — runs in **Phase I-B** (pre-queue).

This is a *discovery-side* screening gate: it executes inside
``planner._screen_and_enqueue`` before a job is ever written to the application
queue, so invalid postings never reach Phase II (application). It is NOT part of
the application/execution phase.

Metro-Vancouver-only rules (all source platforms, default):

  Only a confirmed Metro Vancouver municipality may enter the queue. Remote
  Canada, other British Columbia cities, unknown locations, and every other
  region are rejected before AI screening. Set ``METRO_VANCOUVER_ONLY=0`` only
  to deliberately restore the legacy remote policy below.

Legacy Indeed-family rules (``source_platform`` not glassdoor/workopolis,
when ``METRO_VANCOUVER_ONLY=0``):

  Metro Vancouver
    • EASY_APPLY   → APPLY  (``application_method=easy_apply``)
    • COMPANY_APPLY → SAVE  (``application_method=company_site``)
    • UNKNOWN      → VERIFY (``application_method=unverified``, status=queued;
      Phase II bookmarks first, submits only after detecting Easy Apply/SmartApply)

  Outside Metro Vancouver
    • Confirmed remote + EASY_APPLY → APPLY
    • Hybrid / not confirmed remote / COMPANY_APPLY / UNKNOWN → REJECT

Glassdoor-strict rules (Wave B.1, ``source_platform == glassdoor``):

  Metro Vancouver + EASY_APPLY → APPLY (any work mode: on-prem / hybrid / remote)
  Outside Metro Vancouver (incl. Canada-wide remote) → REJECT
  Non-Easy-Apply (company-site / unverified) → REJECT (no SAVE / VERIFY)

Workopolis-strict rules (mirror Glassdoor, ``source_platform == workopolis``):

  Metro Vancouver + Quick/Easy Apply → APPLY (any work mode)
  Outside Metro Vancouver → REJECT
  Non–Quick-Apply → REJECT (no SAVE / VERIFY)

Empty-location / ``Remote`` discovery uses an Easy Apply–filtered pass for
Indeed only. Glassdoor / Workopolis-only discovery must not use Remote / empty
location passes.

``METRO_VANCOUVER_ONLY=1`` is the default and cannot be bypassed through
``DISCOVERY_GEO_POLICY=0``. That flag can disable only the legacy expanded
geo policy when the strict boundary is deliberately turned off.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from jobbots.core.discovery.contracts import NormalizedJob


# ---------------------------------------------------------------------------
# Region — Metro Vancouver commute range
# ---------------------------------------------------------------------------

# Greater Vancouver / Lower Mainland municipalities the candidate can commute to.
# Matched as whole words against the (lower-cased) job location string.
_METRO_VAN_CITIES: tuple[str, ...] = (
    "metro vancouver", "greater vancouver", "lower mainland",
    "vancouver", "north vancouver", "west vancouver",
    "surrey", "richmond", "burnaby", "coquitlam", "port coquitlam",
    "port moody", "new westminster", "delta", "ladner", "tsawwassen",
    "langley", "white rock", "maple ridge", "pitt meadows",
    "anmore", "belcarra", "bowen island", "lions bay",
)

# Guard against "Vancouver, WA" (USA) matching the Metro Van rule.
_US_STATE_MARKERS: tuple[str, ...] = (
    ", wa", ", washington", " wa ", "united states", ", usa", ", us",
)

REGION_METRO_VAN = "METRO_VAN"
REGION_OTHER = "OTHER"
REGION_UNKNOWN = "UNKNOWN"


def classify_region(location: str) -> str:
    """Classify a job location as Metro-Vancouver, other, or unknown."""
    loc = (location or "").strip().lower()
    if not loc:
        return REGION_UNKNOWN

    if any(marker in loc for marker in _US_STATE_MARKERS):
        # Explicitly non-Canadian / US-state location.
        if "british columbia" not in loc and ", bc" not in loc:
            return REGION_OTHER

    # False friends: metro city tokens that refer to other places.
    # ``\brichmond\b`` matches inside "Richmond Hill, ON" — reject those first.
    if re.search(r"\brichmond\s+hill\b", loc):
        return REGION_OTHER
    if "vancouver island" in loc and "north vancouver" not in loc and "west vancouver" not in loc:
        return REGION_OTHER
    if re.search(r"\bvancouver,?\s*wa\b", loc) or "vancouver washington" in loc:
        return REGION_OTHER

    # Prefer longer city names first so "north vancouver" wins over "vancouver".
    for city in sorted(_METRO_VAN_CITIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", loc):
            if city == "richmond" and re.search(r"\brichmond\s+hill\b", loc):
                continue
            return REGION_METRO_VAN

    return REGION_OTHER


# ---------------------------------------------------------------------------
# Title-level exclusive geo (discovery SERP often tags the *search centre*
# as location while the posting itself is Quebec / Mexico / Toronto-only).
# ---------------------------------------------------------------------------

# Strong exclusive / non-metro geography markers commonly embedded in titles
# or parenthetical location clauses. These override a Metro Van search-centre
# location when no Metro Van city is also present in the title.
_TITLE_EXCLUSIVE_OUT_OF_AREA_RE = re.compile(
    r"(?:"
    # Explicit exclusive scopes
    r"\bmexico\s+only\b|"
    r"\bonly\s+(?:in\s+)?mexico\b|"
    r"\bremote\s*[-–—/]\s*mexico\b|"
    r"\bmexico\s*[-–—/]\s*only\b|"
    r"\bus\s+only\b|\busa\s+only\b|\bunited\s+states\s+only\b|"
    # Explicit non-metro Canadian / foreign cities (title-scoped)
    r"\bquebec\s+city\b|"
    r"\bprovince\s+of\s+quebec\b|"
    r"\bmontr[eé]al\b|"
    r"\btoronto\b|"
    r"\bottawa\b|"
    r"\bcalgary\b|"
    r"\bedmonton\b|"
    r"\bwinnipeg\b|"
    r"\bhalifax\b|"
    r"\bwaterloo\b|"
    r"\bmississauga\b|"
    r"\bscarborough\b|"
    r"\bbrampton\b|"
    r"\bmarkham\b|"
    r"\bkitchener\b|"
    r"\bhamilton\b|"
    r"\blondon,\s*on\b|"
    r"\bmexico\s+city\b|"
    r"\bguadalajara\b|"
    r"\bmonterrey\b|"
    r"\b(?:based|located|onsite|on[- ]site|hybrid)\s+in\s+"
    r"(?:quebec|montr[eé]al|toronto|ottawa|calgary|edmonton|mexico)\b|"
    # Pipe / parenthetical location clauses: "Role | Quebec City (...)"
    r"\|\s*(?:quebec(?:\s+city)?|montr[eé]al|toronto|ottawa|calgary|mexico)\b|"
    r"\(\s*(?:quebec(?:\s+city)?|province of quebec|montr[eé]al|toronto|"
    r"ottawa|calgary|mexico(?:\s+only)?)\b"
    r")",
    re.IGNORECASE,
)

_TITLE_METRO_RESCUE_RE = re.compile(
    r"\b(?:"
    r"metro vancouver|greater vancouver|lower mainland|"
    r"vancouver|north vancouver|west vancouver|surrey|richmond|burnaby|"
    r"coquitlam|port coquitlam|port moody|new westminster|delta|"
    r"langley|white rock|maple ridge|pitt meadows"
    r")\b",
    re.IGNORECASE,
)


def title_exclusive_out_of_area(title: str, *, location: str = "") -> str | None:
    """Return a reject reason when *title* pins the job outside Metro Vancouver.

    Google / ATS discovery often copies the search centre (``Vancouver, BC``)
    into the location field while the real posting is "Quebec City" or
    "Remote - Mexico Only". Those waste apply cycles on relocation thrashing
    and eventually die. Reject them as soon as the title is exclusive and
    does not also name a Metro Van city.
    """
    title = (title or "").strip()
    if not title:
        return None
    if not _TITLE_EXCLUSIVE_OUT_OF_AREA_RE.search(title):
        return None
    # Multi-location titles like "Toronto / Vancouver" still rescue when metro
    # is explicitly named. Location-field metro alone is NOT a rescue.
    if _TITLE_METRO_RESCUE_RE.search(title):
        return None
    return "title_geo_outside_metro"


# ---------------------------------------------------------------------------
# Work mode — remote / hybrid / on-site
# ---------------------------------------------------------------------------

WORK_REMOTE = "REMOTE"
WORK_HYBRID = "HYBRID"
WORK_ONSITE = "ONSITE"
WORK_UNKNOWN = "UNKNOWN"

_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_REMOTE_RE = re.compile(
    r"\b(?:fully remote|100% remote|remote[- ]first|work from home|"
    r"work[- ]from[- ]home|wfh|telecommut\w*|telework\w*|remote)\b",
    re.IGNORECASE,
)
_ONSITE_RE = re.compile(
    r"\b(?:on[- ]?site|in[- ]office|in[- ]person|onsite)\b", re.IGNORECASE
)


def detect_work_mode(
    location: str,
    description: str,
    *,
    is_remote_hint: bool = False,
) -> str:
    """Best-effort work-mode detection.

    Priority: **hybrid** wins over remote (an out-of-metro "hybrid" is a reject
    even when the board tags it remote), then explicit remote signals or the
    provider ``is_remote`` hint, then on-site.
    """
    location = location or ""
    description = description or ""
    # Only scan the leading slice of the description for mode keywords so a
    # passing mention of "remote desktop" deep in the JD doesn't flip the mode.
    text = f"{location}\n{description[:1200]}"

    if _HYBRID_RE.search(location) or _HYBRID_RE.search(text):
        return WORK_HYBRID
    if is_remote_hint or _REMOTE_RE.search(location) or _REMOTE_RE.search(text):
        return WORK_REMOTE
    if _ONSITE_RE.search(text):
        return WORK_ONSITE
    return WORK_UNKNOWN


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

ACTION_APPLY = "APPLY"
ACTION_SAVE = "SAVE"
ACTION_VERIFY = "VERIFY"  # metro-van, apply-type unverified → visit & route
ACTION_REJECT = "REJECT"


@dataclass
class PolicyDecision:
    """Outcome of the geo/work-mode/apply-type policy for one job."""

    action: str  # "APPLY" | "SAVE" | "VERIFY" | "REJECT"
    region: str
    work_mode: str
    apply_type: str
    reason: str
    # Queue routing (only meaningful when action != REJECT)
    #   application_method: "easy_apply" | "company_site" | "unverified"
    #   initial_status:     "queued" | "bookmarked" | "unverified"
    # A metro-van record whose apply type is unknown keeps
    # application_method="unverified" (NOT easy_apply) so Phase II must visit &
    # verify (Easy Apply → apply; external → bookmark) rather than blind-apply.
    application_method: str = "easy_apply"
    initial_status: str = "queued"
    gate_easy_apply: bool = True  # which AI gate to run (lenient vs strict-save)

    @property
    def keep(self) -> bool:
        return self.action != ACTION_REJECT


def _confirmed_remote(job: NormalizedJob) -> bool:
    """Whether a job is *confirmed* fully-remote (not merely a loose mention).

    Search-pass names such as ``remote_easy_apply`` do **not** prove remote
    work — JobSpy's Indeed filters are mutually exclusive (``hours_old`` /
    ``easy_apply`` / ``is_remote``), so that pass only confirms Easy Apply.

    Confirmation requires an explicit remote token in the **location** field.
    JobSpy's row-level ``is_remote`` flag is not enough by itself: it can be
    incorrectly set for a posting that has a physical city location.

    A stray "remote" buried in the description is not sufficient for
    out-of-province APPLY. Hybrid always wins in ``detect_work_mode`` and
    rejects outside Metro even when Easy Apply is confirmed.
    """
    return bool(_REMOTE_RE.search(job.location or ""))


def policy_enabled() -> bool:
    """Whether the geo policy is active (strict Metro Vancouver is always on)."""
    if _metro_vancouver_only():
        return True
    return os.getenv("DISCOVERY_GEO_POLICY", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _metro_vancouver_only() -> bool:
    """Return whether the non-negotiable Metro Vancouver boundary is active."""
    return os.getenv("METRO_VANCOUVER_ONLY", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _is_glassdoor(job: NormalizedJob) -> bool:
    return (getattr(job, "source_platform", "") or "").strip().lower() == "glassdoor"


def _is_workopolis(job: NormalizedJob) -> bool:
    return (getattr(job, "source_platform", "") or "").strip().lower() == "workopolis"


def _decide_metro_easy_apply_only(
    *,
    region: str,
    work_mode: str,
    apply_type: str,
    portal: str,
) -> PolicyDecision:
    """Metro Van + Easy/Quick Apply only (Glassdoor / Workopolis)."""
    outside_reason = f"{portal}_outside_metro"
    non_ea_reason = f"{portal}_non_easy_apply"
    keep_reason = f"{portal}_metro_easy_apply"

    if region != REGION_METRO_VAN:
        return PolicyDecision(
            action=ACTION_REJECT, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason=outside_reason,
        )
    if apply_type != "EASY_APPLY":
        return PolicyDecision(
            action=ACTION_REJECT, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason=non_ea_reason,
        )
    return PolicyDecision(
        action=ACTION_APPLY, region=region, work_mode=work_mode,
        apply_type=apply_type,
        reason=keep_reason,
        application_method="easy_apply", initial_status="queued",
        gate_easy_apply=True,
    )


def _decide_glassdoor_strict(
    *,
    region: str,
    work_mode: str,
    apply_type: str,
) -> PolicyDecision:
    """Glassdoor Wave B.1: Metro Van + EASY_APPLY only (reject company/unknown)."""
    if region != REGION_METRO_VAN:
        return PolicyDecision(
            action=ACTION_REJECT, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="glassdoor_outside_metro",
        )
    if apply_type == "EASY_APPLY":
        return PolicyDecision(
            action=ACTION_APPLY, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="glassdoor_metro_easy_apply",
            application_method="easy_apply", initial_status="queued",
            gate_easy_apply=True,
        )
    if apply_type == "COMPANY_APPLY":
        return PolicyDecision(
            action=ACTION_REJECT, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="glassdoor_company_site_rejected",
        )
    # UNKNOWN / non-EA: Wave B.1 Easy Apply only — never queue for verify
    return PolicyDecision(
        action=ACTION_REJECT, region=region, work_mode=work_mode,
        apply_type=apply_type,
        reason="glassdoor_non_easy_apply",
    )


def _decide_workopolis_strict(
    *,
    region: str,
    work_mode: str,
    apply_type: str,
) -> PolicyDecision:
    """Workopolis-strict: Metro Van + Quick/Easy Apply only (any work mode)."""
    return _decide_metro_easy_apply_only(
        region=region,
        work_mode=work_mode,
        apply_type=apply_type,
        portal="workopolis",
    )


def decide_job_policy(job: NormalizedJob) -> PolicyDecision:
    """Apply the Metro-Vancouver-first geo/work-mode/apply-type policy."""
    region = classify_region(job.location)
    work_mode = detect_work_mode(
        job.location, job.description, is_remote_hint=bool(job.is_remote_hint)
    )
    apply_type = (job.apply_type or "UNKNOWN").upper()

    # Title-embedded exclusive geography beats a search-centre location field.
    # Example: location="Vancouver, BC" + title="... Quebec City" must REJECT.
    title_geo = title_exclusive_out_of_area(
        getattr(job, "job_title", "") or "",
        location=job.location or "",
    )
    if title_geo:
        return PolicyDecision(
            action=ACTION_REJECT,
            region=REGION_OTHER,
            work_mode=work_mode,
            apply_type=apply_type,
            reason=title_geo,
        )

    # The default job-search boundary is intentionally stricter than a
    # province/country or remote filter: only a proven Metro Vancouver job can
    # spend screening or application capacity. Unknown is a reject—not a
    # permissive fallback—because the target location is explicit.
    if _metro_vancouver_only() and region != REGION_METRO_VAN:
        return PolicyDecision(
            action=ACTION_REJECT, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="outside_metro_vancouver_only",
        )

    # ── Wave B.1 Glassdoor-strict (does not alter Indeed-family rules) ────
    if _is_glassdoor(job):
        return _decide_glassdoor_strict(
            region=region, work_mode=work_mode, apply_type=apply_type,
        )

    # ── Workopolis-strict (mirror Glassdoor; Quick Apply badge → EASY_APPLY)
    if _is_workopolis(job):
        return _decide_workopolis_strict(
            region=region, work_mode=work_mode, apply_type=apply_type,
        )

    # ── Outside Metro Vancouver ───────────────────────────────────────────
    # Require CONFIRMED fully-remote AND CONFIRMED easy-apply. Anything hybrid,
    # on-site, company-site, or with an unverified apply type is rejected and
    # never enters the queue (so it can never be leased for application).
    if region == REGION_OTHER:
        if work_mode == WORK_HYBRID:
            return PolicyDecision(
                action=ACTION_REJECT, region=region, work_mode=work_mode,
                apply_type=apply_type,
                reason="outside_metro_hybrid",
            )
        if work_mode != WORK_REMOTE or not _confirmed_remote(job):
            return PolicyDecision(
                action=ACTION_REJECT, region=region, work_mode=work_mode,
                apply_type=apply_type,
                reason=f"outside_metro_not_confirmed_remote (work_mode={work_mode.lower()})",
            )
        if apply_type == "COMPANY_APPLY":
            is_direct_ats = (job.source_platform or "").lower() in {"greenhouse", "lever", "ashby", "bamboohr", "company_apply"}
            if is_direct_ats and work_mode == WORK_REMOTE and _confirmed_remote(job):
                return PolicyDecision(
                    action=ACTION_APPLY, region=region, work_mode=work_mode,
                    apply_type=apply_type,
                    reason="outside_metro_remote_ats_apply",
                    application_method="company_site", initial_status="queued",
                    gate_easy_apply=False,
                )
            return PolicyDecision(
                action=ACTION_REJECT, region=region, work_mode=work_mode,
                apply_type=apply_type,
                reason="outside_metro_company_site",
            )
        if apply_type != "EASY_APPLY":
            # UNKNOWN / unverified apply type outside Metro Van → never queue.
            return PolicyDecision(
                action=ACTION_REJECT, region=region, work_mode=work_mode,
                apply_type=apply_type,
                reason="outside_metro_apply_type_unverified",
            )
        # Confirmed remote + confirmed easy-apply.
        return PolicyDecision(
            action=ACTION_APPLY, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="outside_metro_remote_easy_apply",
            application_method="easy_apply", initial_status="queued",
            gate_easy_apply=True,
        )

    # ── Metro Vancouver (and unknown location from a metro-targeted pass) ──
    if apply_type == "EASY_APPLY":
        return PolicyDecision(
            action=ACTION_APPLY, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="metro_van_easy_apply",
            application_method="easy_apply", initial_status="queued",
            gate_easy_apply=True,
        )

    if apply_type == "COMPANY_APPLY":
        is_direct_ats = (job.source_platform or "").lower() in {"greenhouse", "lever", "ashby", "bamboohr", "company_apply"}
        act = ACTION_APPLY if is_direct_ats else ACTION_SAVE
        return PolicyDecision(
            action=act, region=region, work_mode=work_mode,
            apply_type=apply_type,
            reason="metro_van_company_site_ats" if is_direct_ats else "metro_van_company_site_bookmark",
            application_method="company_site", initial_status="queued",
            gate_easy_apply=False,
        )

    # UNKNOWN / unverified apply type in Metro Van: do NOT convert to
    # easy-apply. Preserve application_method="unverified" and route through the
    # Phase II verification path — the applier visits the page and applies only
    # if it confirms an Indeed Easy Apply button, otherwise it bookmarks.
    return PolicyDecision(
        action=ACTION_VERIFY, region=region, work_mode=work_mode,
        apply_type=apply_type,
        reason="metro_van_apply_type_unverified_route_to_verification",
        application_method="unverified", initial_status="queued",
        gate_easy_apply=True,
    )
