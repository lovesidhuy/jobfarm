"""Shared portal bridge for discovery-mode queueing."""
from __future__ import annotations
import os
from jobbots.core.job_queue import JobQueue

def discovery_mode():
    return os.getenv("JOBBOT_MODE","apply").strip().lower() in {"discover","discovery","search"}

def enqueue_approved_job(*,portal,profile,job_id,title,company,location,url,description,
                         gate_score=None,gate_reason="",resume_policy="default",initial_status="queued",
                         application_method="easy_apply",region="",company_ai_approved=False):
    # Universal IT queue-entry safeguard. Every portal and both legacy/new
    # discovery paths use this bridge, so non-IT listings cannot bypass the
    # trained deterministic gate simply because they came from another bot.
    #
    # Phase I may already have batch-title-AI or company-site AI approved a
    # job that the local hard gate alone would reject as "unsure". Honor those
    # Phase-I decisions so enqueue does not silently drop them.
    if str(profile or "").strip().lower() == "it":
        reason_l = (gate_reason or "").lower()
        phase1_ai_ok = bool(company_ai_approved) or (
            "batch ai title approval" in reason_l
            or "batch ai company" in reason_l
            or reason_l.startswith("batch ai")
            or reason_l.startswith("fail-open:")
            or "fail-open:" in reason_l
        )
        if not phase1_ai_ok:
            from jobbots.core.discovery._gate_adapter import hard_screen_job
            passed, local_score, local_reason = hard_screen_job(
                title=title, company=company, description=description,
                location=location, easy_apply=(application_method == "easy_apply"),
            )
            if not passed:
                return None, False
            gate_score = local_score if gate_score is None else gate_score
            gate_reason = local_reason or gate_reason
    # ``region`` records the Phase I-B geo classification (e.g. METRO_VAN) so the
    # Phase II worker can defensively confirm safeguard #1: lease-and-verify
    # (``unverified``) is only ever executed for Metro-Vancouver jobs.
    metadata={"bot_name":os.getenv("BOT_NAME", ""),"discovered_by":portal,"application_method":application_method}
    if region:
        metadata["region"]=region
    if company_ai_approved:
        metadata["company_ai_approved"]=True
    result = JobQueue().enqueue(portal=portal,profile=profile,source_job_id=str(job_id),title=title,
      company=company,location=location,url=url,description=description,gate_score=gate_score,
      gate_reason=gate_reason,resume_policy=resume_policy,initial_status=initial_status,
      metadata=metadata)
    try:
        from jobbots.core.training_capture import record_training_event
        queued_id, created = result
        record_training_event(
            "job_discovered" if created else "job_rediscovered",
            portal=portal, profile=profile, job_id=queued_id, source_job_id=str(job_id),
            job_url=url, title=title, company=company, location=location,
            discovery_decision="approved", gate_score=gate_score,
            gate_reason=gate_reason, application_method=application_method,
            created=bool(created), discovery_source=portal,
        )
    except Exception:
        pass
    return result
