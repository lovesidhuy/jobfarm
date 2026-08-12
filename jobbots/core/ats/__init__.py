"""Modular ATS application engine.

Public API:

  * ``detect_adapter(url)`` — return the adapter class for a job URL
  * ``detect_platform(url)`` — return the platform name ("greenhouse", ...)
  * ``detect_adapter_from_page(page)`` — detect from a loaded page
  * ``apply_job(page, url, title=..., company=...)`` — full flow from URL
  * ``apply_on_page(page, title=..., company=...)`` — flow on an open page
  * ``ApplicationEngine`` — orchestrator class for custom flows
  * ``ATSAdapter`` — base class for adding new platform adapters

Supported platforms: Greenhouse, Lever, Ashby, BambooHR.

Confirmation policy (see ``confirmation.py``):
  * **Primary** — on-page success after Submit (banner / thank-you / URL).
  * **Secondary** — application-receipt email (dedupe/history only).
"""
from __future__ import annotations

from typing import Any

from .base import ATSAdapter
from .types import ApplicationResult, AdapterContext, FillStats, QuestionContext
from .registry import (
    register,
    detect_adapter,
    detect_platform,
    detect_adapter_from_page,
    is_supported_url,
    supported_platforms,
)
from .engine import ApplicationEngine

__all__ = [
    "ATSAdapter",
    "ApplicationResult",
    "AdapterContext",
    "FillStats",
    "QuestionContext",
    "ApplicationEngine",
    "register",
    "detect_adapter",
    "detect_platform",
    "detect_adapter_from_page",
    "is_supported_url",
    "supported_platforms",
    "apply_job",
    "apply_on_page",
]


import os

def apply_job(
    page: Any,
    url: str,
    *,
    title: str = "",
    company: str = "",
    job_context: str = "",
    dry_run: bool | None = None,
) -> ApplicationResult:
    """Run the complete application flow for a job *url*.

    Detects the ATS platform, drives the adapter through initialize →
    authenticate → upload → fill → answer → captcha → submit → verify.

    Returns an :class:`ApplicationResult`.  Use ``result.as_tuple()`` for
    the legacy ``(ok, result_url, reason)`` triple.
    """
    if dry_run is None:
        dry_run = os.getenv("ATS_DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    engine = ApplicationEngine(
        page,
        title=title,
        company=company,
        job_context=job_context or " ".join(x for x in (title, company) if x).strip(),
        dry_run=dry_run,
    )
    return engine.run(url)


def apply_on_page(
    page: Any,
    *,
    title: str = "",
    company: str = "",
    job_context: str = "",
    dry_run: bool | None = None,
) -> ApplicationResult:
    """Run the application flow on an already-open page.

    The platform is detected from the current page URL, embedded iframes,
    or DOM markers.
    """
    if dry_run is None:
        dry_run = os.getenv("ATS_DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    engine = ApplicationEngine(
        page,
        title=title,
        company=company,
        job_context=job_context or " ".join(x for x in (title, company) if x).strip(),
        dry_run=dry_run,
    )
    return engine.run_on_page()
