"""Portal adapter registry — resolve portal × profile in one place.

The supervised-bot registry (``jobbots.core.supervised_bots``) stays the
single source of truth for bot identity (script, CDP port, browser profile,
JOB_PROFILE). This registry maps portal names to their adapter classes and
validates enablement against the profile manifests (``profiles/<owner>/
<name>/profile.yaml``).
"""
from __future__ import annotations

from typing import Any

from jobbots.integrations.portals._delegating import (
    DelegatingATSAdapter,
    DelegatingPortalAdapter,
)
from jobbots.integrations.portals.ashby import AshbyAdapter
from jobbots.integrations.portals.bamboohr import BamboohrAdapter
from jobbots.integrations.portals.base import PortalAdapter
from jobbots.integrations.portals.glassdoor import GlassdoorAdapter
from jobbots.integrations.portals.greenhouse import GreenhouseAdapter
from jobbots.integrations.portals.indeed import IndeedAdapter
from jobbots.integrations.portals.jobbank import JobBankAdapter
from jobbots.integrations.portals.lever import LeverAdapter
from jobbots.integrations.portals.linkedin import LinkedInAdapter
from jobbots.integrations.portals.workopolis import WorkopolisAdapter

PORTAL_ADAPTERS: dict[str, type[DelegatingPortalAdapter]] = {
    "indeed": IndeedAdapter,
    "glassdoor": GlassdoorAdapter,
    "workopolis": WorkopolisAdapter,
    "linkedin": LinkedInAdapter,
    "jobbank": JobBankAdapter,
    "greenhouse": GreenhouseAdapter,
    "ashby": AshbyAdapter,
    "lever": LeverAdapter,
    "bamboohr": BamboohrAdapter,
}

BROWSER_PORTALS = tuple(
    name for name, cls in PORTAL_ADAPTERS.items() if not cls.is_ats
)
ATS_PORTALS = tuple(name for name, cls in PORTAL_ADAPTERS.items() if cls.is_ats)


def available_portals() -> list[str]:
    return sorted(PORTAL_ADAPTERS)


def get_adapter(portal: str, *, profile: str | None = None) -> PortalAdapter:
    """Return the adapter for *portal*, optionally validated for *profile*.

    Profile validation consults the profile manifest's ``portals:`` list —
    the same enablement surface ``jobbots doctor`` checks.
    """
    key = str(portal).strip().lower()
    try:
        cls = PORTAL_ADAPTERS[key]
    except KeyError:
        raise KeyError(
            f"unknown portal {portal!r}; available: {', '.join(available_portals())}"
        ) from None
    if profile is not None:
        enabled = profile_portals(profile)
        if enabled and key not in enabled:
            raise ValueError(
                f"portal {key!r} is not enabled for profile {profile!r}: {enabled}"
            )
    return cls()


def profile_portals(name: str, *, owner: str = "Jane") -> list[str]:
    """Portal enablement list from the profile manifest (empty = unknown profile)."""
    from jobbots.core.profiles.loader import load_profile

    try:
        profile = load_profile(name, owner=owner)
    except FileNotFoundError:
        return []
    portals = profile.manifest.get("portals") or []
    return [str(p).strip().lower() for p in portals]


def supervised_bots(portal: str | None = None) -> list[dict[str, Any]]:
    """Supervised bot rows (script/CDP port/browser profile), optionally by portal.

    Delegates to the canonical registry — identity, ports, and profiles never
    drift between tools.
    """
    from jobbots.core.supervised_bots import supervised_bot_configs

    rows = list(supervised_bot_configs())
    if portal is not None:
        key = portal.strip().lower()
        rows = [r for r in rows if str(r.get("portal", "")).lower() == key]
    return rows
