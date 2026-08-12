"""Job Bank portal adapter.

Discovery keeps its proven scraper lane.  The official application route is
authenticated ``Direct Apply`` through the same queue and a dedicated NST
profile.  Email-based Job Bank application is retired.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

from jobbots.integrations.portals._delegating import DelegatingPortalAdapter


class JobBankAdapter(DelegatingPortalAdapter):
    name = "jobbank"

    def discover(self, search: dict[str, Any]) -> Iterable[dict[str, Any]]:
        return iter(())

    @staticmethod
    def canonical_module():
        """Return the official Job Bank application implementation."""
        return JobBankAdapter.direct_apply()

    @staticmethod
    def direct_apply():
        """Job Bank Direct Apply helpers (lazy canonical re-export)."""
        from jobbots.core import jobbank_direct_apply

        return jobbank_direct_apply
