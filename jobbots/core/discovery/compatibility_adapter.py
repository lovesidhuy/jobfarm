"""Compatibility adapter — maps ``NormalizedJob`` to the exact field set
expected by the existing downstream pipeline:

* ``core.shared_modules.job_queue_bridge.enqueue_approved_job()``
* ``core.job_queue.JobQueue.enqueue()``
* ``scripts/application_worker.py``  ``dispatch()``
* Existing MongoDB ``application_queue`` documents

Phase 2 behaviour and APIs remain unchanged; this adapter is the *only*
bridge between the new discovery engine and the existing queue contract.
"""
from __future__ import annotations

from jobbots.core.discovery.contracts import NormalizedJob, QueueRecord


def to_queue_record(job: NormalizedJob, profile: str) -> QueueRecord:
    """Convert a normalised job to the queue-record shape consumed by
    ``enqueue_approved_job()``.

    Parameters
    ----------
    job:
        Normalised and classified job from the discovery engine.
    profile:
        Job profile (``"it"`` or ``"general"``).  Determines
        ``resume_policy``.
    """
    if job.apply_type == "EASY_APPLY":
        method = "easy_apply"
        status = "queued"
    elif job.apply_type == "COMPANY_APPLY":
        method = "company_site"
        status = "queued"
    else:
        method = "unknown"
        status = "unverified"

    return QueueRecord(
        portal=job.source_platform,
        profile=profile.lower(),
        source_job_id=job.source_job_id,
        title=job.job_title,
        company=job.company_name,
        location=job.location,
        url=job.listing_url,
        description=job.description,
        gate_score=None,  # filled by screen_job_with_ai()
        gate_reason="",  # filled by screen_job_with_ai()
        resume_policy="tailored" if profile.lower() == "it" else "default",
        initial_status=status,
        application_method=method,
    )


def queue_record_to_enqueue_kwargs(rec: QueueRecord) -> dict:
    """Serialise a ``QueueRecord`` to the keyword arguments accepted by
    ``enqueue_approved_job()``.

    This is the contract boundary — if
    ``enqueue_approved_job()`` ever changes its signature, only this
    function needs updating.
    """
    return dict(
        portal=rec.portal,
        profile=rec.profile,
        job_id=rec.source_job_id,
        title=rec.title,
        company=rec.company,
        location=rec.location,
        url=rec.url,
        description=rec.description,
        gate_score=rec.gate_score,
        gate_reason=rec.gate_reason,
        resume_policy=rec.resume_policy,
        initial_status=rec.initial_status,
        application_method=rec.application_method,
        region=rec.region,
        company_ai_approved=rec.company_ai_approved,
    )
