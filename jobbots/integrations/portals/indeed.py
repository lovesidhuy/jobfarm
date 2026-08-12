"""Indeed portal adapter (Phase 3).

Portal-specific surface only; every operation delegates to the canonical core
(see ``_delegating.py``). The master Indeed bots remain the execution engine
behind ``application_worker.dispatch`` — unchanged.
"""
from __future__ import annotations

from jobbots.integrations.portals._delegating import DelegatingPortalAdapter


class IndeedAdapter(DelegatingPortalAdapter):
    name = "indeed"
