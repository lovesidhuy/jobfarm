import json, os
from pathlib import Path


def resolve_direct_queue_result(stats_dict: dict, *, verify_mode: bool) -> tuple[str, str, str]:
    """Map one direct-links job's counts to ``(status, application_method, reason)``.

    Phase-II terminal-state lockdown consumed by the application worker. Direct
    mode processes exactly one job (``INDEED_MAX_APPLICATION_OUTCOMES=1``), so the
    counts reflect that single job:

      * bookmarked (company-site / verify-external) → ``bookmarked`` +
        ``company_site`` — a saved lead, never a real submission.
      * on-platform submission (SmartApply/Easy Apply, no external tab) →
        ``applied`` + ``easy_apply``.
      * external redirect that still counted as applied → ``bookmarked`` +
        ``company_site`` (not a genuine on-platform submission).
      * no clear outcome for a lease-and-verify job → ``manual_review`` so it
        never retries forever; otherwise ``failed`` (worker decides retry/dead).
    """
    applied = int(stats_dict.get("applied_count", 0) or 0)
    failed = int(stats_dict.get("failed_count", 0) or 0)
    external = int(stats_dict.get("external_count", 0) or 0)
    bookmarked = int(stats_dict.get("bookmarked_count", 0) or 0)
    last_reason = (stats_dict.get("last_reason") or "").strip()

    if bookmarked > 0:
        return "bookmarked", "company_site", last_reason or "Company-site lead saved (bookmarked)"
    if applied > 0:
        reason_l = last_reason.lower()
        # Honest taxonomy: already-applied is not a *new* application.
        if "already applied" in reason_l:
            return "already_applied", "easy_apply", last_reason or "Already applied to this job"
        if any(k in reason_l for k in ("greenhouse", "lever application", "lever/")) or (
            "lever" in reason_l and "submitted" in reason_l
        ):
            return "applied", "company_site", last_reason or "Greenhouse/Lever application submitted"
        if external > 0:
            return "bookmarked", "company_site", "External/company-site apply — saved as lead"
        return "applied", "easy_apply", last_reason or "Indeed Easy Apply/SmartApply submitted"
    # Cover-letter policy skip (SmartApply) — terminal skip, not a failure thrash.
    reason_l = last_reason.lower()
    if "cover letter" in reason_l:
        return "skipped", "easy_apply", last_reason or "Skipped: cover letter screen"
    if failed > 0:
        if "cover letter" in reason_l:
            return "skipped", "easy_apply", last_reason or "Skipped: cover letter screen"
        if "already applied" in reason_l:
            return "already_applied", "easy_apply", last_reason or "Already applied to this job"
        return "failed", "", last_reason or "Indeed direct queue application failed"
    if verify_mode:
        return "manual_review", "unverified", "No Easy Apply/SmartApply resolved after visit — manual review"
    return "failed", "", "Indeed direct queue job produced no application outcome"


def write_queue_result(status, *, result_url="", reason="", application_method=""):
    """Persist a Phase-II job outcome for the application worker to consume.

    ``status`` is the terminal signal the worker maps to a queue transition:
      ``applied``          — new Indeed Easy Apply / SmartApply submit
      ``already_applied``  — portal shows prior application (not a new win)
      ``skipped``          — policy skip (e.g. cover letter screen)
      ``bookmarked``       — external/company-site lead saved (never submitted)
      ``manual_review``    — apply type could not be resolved on the page
      ``failed``           — recoverable failure (worker decides retry vs. dead)

    ``application_method`` is the *resolved* method after visiting the page
    (``easy_apply`` or ``company_site``). It lets the worker persist the truth
    for jobs that were queued as ``unverified`` (Metro-Vancouver lease-and-verify).
    """
    path=os.getenv("JOB_QUEUE_RESULT_FILE","").strip()
    if not path: return
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    payload={"status":status,"result_url":result_url,"reason":reason}
    if application_method:
        payload["application_method"]=application_method
    tmp=target.with_suffix(target.suffix+".tmp")
    tmp.write_text(json.dumps(payload),encoding="utf-8")
    tmp.replace(target)
