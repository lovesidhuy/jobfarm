"""Provider protocol and shared request type for discovery engines."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from jobbots.core.discovery.contracts import RawJob


@dataclass
class DiscoveryRequest:
    """Configuration bundle passed to every provider's ``discover()`` call."""

    profile: str  # "it" | "general"
    search_terms: list[str]  # from config/<profile>/search.py
    locations: list[str]  # from config/<profile>/search.py
    max_results_per_term: int = 50
    freshness_days: int | None = 7  # None = all dates
    radius_km: int = 25
    easy_apply_only: bool = False
    job_types: list[str] = field(default_factory=list)
    experience_levels: list[str] = field(default_factory=list)
    workplace_types: list[str] = field(default_factory=list)
    proxies: list[str] | None = None
    timeout_seconds: int = 300

    @staticmethod
    def is_remote_location(location: str) -> bool:
        return not (location or "").strip() or (location or "").strip().lower() in {
            "remote", "remote canada", "canada remote"
        }


@runtime_checkable
class DiscoveryProvider(Protocol):
    """Interface that every discovery provider must satisfy.

    Providers MUST NOT raise on partial failure — they should log the error
    and return whatever results were collected before the failure.
    """

    name: str
    supported_platforms: list[str]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        """Run discovery and return raw jobs.

        Must not raise on partial failure.  Return an empty list if the
        provider cannot reach any platform.
        """
        ...
