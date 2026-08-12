"""Workopolis portal adapter (Phase 3) — delegating only."""
from __future__ import annotations

from jobbots.integrations.portals._delegating import DelegatingPortalAdapter


class WorkopolisAdapter(DelegatingPortalAdapter):
    name = "workopolis"
