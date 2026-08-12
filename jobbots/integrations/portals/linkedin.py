"""LinkedIn portal adapter (Phase 3) — delegating only.

Discovery uses the JobSpy LinkedIn provider (Easy Apply filtered pass);
applications dispatch through the worker, which stamps LINKEDIN_DIRECT_JOB_URL
for the LinkedIn bot — identical to production.
"""
from __future__ import annotations

from jobbots.integrations.portals._delegating import DelegatingPortalAdapter


class LinkedInAdapter(DelegatingPortalAdapter):
    name = "linkedin"
