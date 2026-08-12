"""Canonical portal adapter interface.

Every portal — browser-driven (Indeed, Glassdoor, LinkedIn, Workopolis,
Job Bank) and API/ATS (Greenhouse, Ashby, Lever, BambooHR) — exposes the same
five operations. Portal adapters contain ONLY portal-specific behavior:
selectors, navigation, DOM quirks, ATS JSON shapes. Queueing, retries, AI
calls, Telegram, logging, browser lifecycle, and persistence live once in
``jobbots.core`` (currently facades over ``automation_monorepo/core``).

Phase 1 note: these Protocols are contracts for the adapters that later
phases extract from ``master/*`` bots and ``core/portals/*`` / ``core/ats/*``.
They are typed with ``Any`` at the boundaries so existing payloads
(job dicts, SeleniumBase pages, Playwright contexts) fit without conversion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class JobLead:
    """Normalized job posting — the only shape that crosses portal boundaries."""

    portal: str
    source_job_id: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    profile: str = ""  # owning profile name: "it" | "general" — always explicit
    date_posted: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreenDecision:
    """Outcome of screening a lead against a profile's gates/thresholds."""

    qualified: bool
    score: float | None = None
    reason: str = ""
    resume_policy: str = "default"


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of one application attempt."""

    status: str  # "applied" | "skipped" | "failed" | "manual_review" | "already_applied"
    result_url: str = ""
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Verification:
    """Post-submit verification (confirmation page / email / portal history)."""

    verified: bool
    method: str = ""
    evidence: str = ""


@runtime_checkable
class PortalAdapter(Protocol):
    """The stable interface all portals implement.

    Implementations may be browser sessions or pure API clients; the
    orchestrator only ever sees this surface.
    """

    name: str

    def discover(self, search: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Yield raw portal-native job postings for one search spec."""
        ...

    def normalize_job(self, raw: dict[str, Any]) -> JobLead:
        """Convert a portal-native posting into the canonical JobLead."""
        ...

    def screen(self, lead: JobLead, *, profile: str) -> ScreenDecision:
        """Gate a lead for the given profile (thresholds, hero terms, geo)."""
        ...

    def apply(self, lead: JobLead, *, profile: str) -> ApplyResult:
        """Submit one application. Q&A goes through jobbots.core.qa only."""
        ...

    def verify(self, lead: JobLead, result: ApplyResult) -> Verification:
        """Confirm the application actually landed."""
        ...
