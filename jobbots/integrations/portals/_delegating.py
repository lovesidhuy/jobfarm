"""Delegating portal adapter base (Phase 3).

Every operation delegates to the existing, production-proven implementation —
this module contains **no** portal logic of its own:

    discover()      -> jobbots.core.discovery planner providers (RawJob payloads)
    normalize_job() -> jobbots.core.discovery.normalizer.normalize_raw_job
    screen()        -> jobbots.core.discovery._gate_adapter.hard_screen_job
    apply()         -> scripts.application_worker.dispatch (the queue worker that
                       runs the same master bots / ATS engine as production)
    verify()        -> jobbots.core.shared_modules.queue_result terminal mapping

Heavy imports are deliberately lazy so importing an adapter never pulls in
browser/Mongo/AI dependencies.
"""
from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

from jobbots.integrations.portals.base import (
    ApplyResult,
    JobLead,
    ScreenDecision,
    Verification,
)


class DelegatingPortalAdapter:
    """Canonical adapter: portal-specific surface, zero duplicated logic."""

    name: str = ""
    #: Planner portal keys used for discovery (defaults to [name]).
    discovery_portals: tuple[str, ...] | None = None
    #: True for API/ATS portals (greenhouse/ashby/lever/bamboohr).
    is_ats: bool = False

    # ------------------------------------------------------------------
    # discover → core.discovery providers
    # ------------------------------------------------------------------
    def discover(self, search: dict[str, Any]) -> Iterable[dict[str, Any]]:
        from jobbots.core.discovery.planner import _build_providers
        from jobbots.core.discovery.providers.base import DiscoveryRequest

        fields = {f.name for f in dataclasses.fields(DiscoveryRequest)}
        kwargs = {k: v for k, v in dict(search).items() if k in fields}
        kwargs.setdefault("profile", "it")
        kwargs.setdefault("search_terms", [])
        kwargs.setdefault("locations", [])
        request = DiscoveryRequest(**kwargs)

        portals = list(self.discovery_portals or (self.name,))
        for provider in _build_providers(portals):
            for raw in provider.discover(request) or []:
                yield raw

    # ------------------------------------------------------------------
    # normalize_job → core.discovery.normalizer
    # ------------------------------------------------------------------
    def normalize_job(self, raw: dict[str, Any]) -> JobLead:
        from jobbots.core.discovery.contracts import RawJob
        from jobbots.core.discovery.normalizer import normalize_raw_job

        if isinstance(raw, RawJob):
            raw_job = raw
        else:
            fields = {f.name for f in dataclasses.fields(RawJob)}
            raw_job = RawJob(**{k: v for k, v in dict(raw).items() if k in fields})
        norm = normalize_raw_job(raw_job, discovery_engine=f"{self.name}_adapter")
        return JobLead(
            portal=norm.source_platform or self.name,
            source_job_id=norm.source_job_id,
            title=norm.job_title,
            company=norm.company_name,
            url=norm.listing_url,
            location=norm.location or "",
            description=norm.description or "",
            profile="",
            date_posted=norm.date_posted,
            metadata={
                "apply_type": getattr(norm, "apply_type", "") or "",
                "apply_type_source": getattr(norm, "apply_type_source", "") or "",
                "apply_type_confirmed": bool(getattr(norm, "apply_type_confirmed", False)),
                "destination_url": norm.destination_url,
                "discovery_engine": norm.discovery_engine,
            },
        )

    # ------------------------------------------------------------------
    # screen → core.discovery._gate_adapter (frozen Indeed gate functions)
    # ------------------------------------------------------------------
    def screen(self, lead: JobLead, *, profile: str) -> ScreenDecision:
        from jobbots.core.discovery._gate_adapter import hard_screen_job

        easy_apply = str(lead.metadata.get("apply_type") or "").upper() == "EASY_APPLY"
        passed, score, reason = hard_screen_job(
            title=lead.title,
            company=lead.company,
            description=lead.description,
            location=lead.location,
            easy_apply=easy_apply,
            profile=profile,
        )
        return ScreenDecision(
            qualified=bool(passed),
            score=float(score) if score is not None else None,
            reason=reason or "",
            resume_policy="tailored" if profile.strip().lower() == "it" else "default",
        )

    # ------------------------------------------------------------------
    # apply → scripts.application_worker.dispatch (production queue worker)
    # ------------------------------------------------------------------
    def queue_payload(self, lead: JobLead, *, profile: str) -> dict[str, Any]:
        """Build the exact job dict shape the application worker consumes."""
        method = str(lead.metadata.get("application_method") or "").strip()
        if not method:
            apply_type = str(lead.metadata.get("apply_type") or "").upper()
            method = "easy_apply" if apply_type == "EASY_APPLY" else "unverified"
        return {
            "id": str(lead.metadata.get("queue_id") or lead.source_job_id),
            "portal": lead.portal or self.name,
            "profile": profile,
            "source_job_id": lead.source_job_id,
            "title": lead.title,
            "company": lead.company,
            "url": lead.url,
            "description": lead.description,
            "attempts": 0,
            "metadata": {
                "application_method": method,
                **{
                    k: v
                    for k, v in lead.metadata.items()
                    if k not in {"application_method", "queue_id"}
                },
            },
        }

    def apply(self, lead: JobLead, *, profile: str) -> ApplyResult:
        from jobbots.paths import ensure_monorepo_on_path

        ensure_monorepo_on_path()
        from scripts.application_worker import dispatch  # production worker

        job = self.queue_payload(lead, profile=profile)
        fd, raw_path = tempfile.mkstemp(
            prefix=f"jobbots_{self.name}_", suffix="_result.json"
        )
        result_path = Path(raw_path)
        try:
            import os

            os.close(fd)
            result_path.unlink(missing_ok=True)
            code, err = dispatch(job, result_path)
        except Exception:
            result_path.unlink(missing_ok=True)
            raise

        payload: dict[str, Any] = {}
        if result_path.is_file():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        status = str(payload.get("status") or ("failed" if code else "applied"))
        return ApplyResult(
            status=status,
            result_url=str(payload.get("result_url") or ""),
            reason=str(payload.get("reason") or err or ""),
            detail={
                "exit_code": code,
                "application_method": payload.get("application_method")
                or job["metadata"]["application_method"],
                "result_file": str(result_path),
            },
        )

    # ------------------------------------------------------------------
    # verify → core.shared_modules.queue_result terminal-state mapping
    # ------------------------------------------------------------------
    def verify(self, lead: JobLead, result: ApplyResult) -> Verification:
        method = str(result.detail.get("application_method") or "")
        verified = result.status == "applied"
        evidence = result.result_url or result.reason
        stats = result.detail.get("stats")
        if isinstance(stats, dict):
            from jobbots.core.shared_modules.queue_result import (
                resolve_direct_queue_result,
            )

            status, resolved_method, _reason = resolve_direct_queue_result(
                stats, verify_mode=(method == "unverified")
            )
            verified = status == "applied"
            method = resolved_method or method
        return Verification(verified=verified, method=method, evidence=evidence)


class DelegatingATSAdapter(DelegatingPortalAdapter):
    """ATS portal (greenhouse/ashby/lever/bamboohr): same five operations.

    Discovery of ATS leads flows through the discovery providers (ATS board
    API / crossmatch / SERP) and is filtered to this platform; applications go
    through the same worker dispatch, which routes ATS URLs to the
    ``core.ats`` engine — identical to production.
    """

    is_ats = True
    discovery_portals = ("ats", "google")

    def discover(self, search: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for raw in super().discover(search):
            platform = getattr(raw, "source_platform", "") or str(
                raw.get("source_platform", "") if isinstance(raw, dict) else ""
            )
            if platform == self.name:
                yield raw

    # -- ATS-specific introspection (delegates to core.ats registry) --------
    @classmethod
    def detect(cls, url: str | None) -> str | None:
        from jobbots.core.ats.registry import detect_platform

        return detect_platform(url)

    @classmethod
    def ats_adapter_class(cls):
        """The registered ``core.ats`` adapter class for this platform."""
        from jobbots.core.ats import adapters  # noqa: F401  (registers all)
        from jobbots.core.ats.registry import _ADAPTERS

        return _ADAPTERS.get(cls.name)
