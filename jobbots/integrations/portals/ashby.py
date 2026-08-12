"""Ashby ATS portal adapter (Phase 3) — delegating only."""
from __future__ import annotations

from jobbots.integrations.portals._delegating import DelegatingATSAdapter


class AshbyAdapter(DelegatingATSAdapter):
    name = "ashby"
