"""Portal adapters — the stable five-operation surface for every portal.

    from jobbots.integrations.portals import get_adapter
    adapter = get_adapter("indeed", profile="it")
    for raw in adapter.discover(search): ...
    lead = adapter.normalize_job(raw)
    decision = adapter.screen(lead, profile="it")
    result = adapter.apply(lead, profile="it")        # production worker path
    check = adapter.verify(lead, result)

Protocols live in ``base.py``; delegation machinery in ``_delegating.py``;
the registry maps portal names and validates profile enablement.
"""
from __future__ import annotations

from jobbots.integrations.portals.base import (
    ApplyResult,
    JobLead,
    PortalAdapter,
    ScreenDecision,
    Verification,
)
from jobbots.integrations.portals.registry import (
    ATS_PORTALS,
    BROWSER_PORTALS,
    PORTAL_ADAPTERS,
    available_portals,
    get_adapter,
    profile_portals,
    supervised_bots,
)

__all__ = [
    "ApplyResult",
    "JobLead",
    "PortalAdapter",
    "ScreenDecision",
    "Verification",
    "ATS_PORTALS",
    "BROWSER_PORTALS",
    "PORTAL_ADAPTERS",
    "available_portals",
    "get_adapter",
    "profile_portals",
    "supervised_bots",
]
