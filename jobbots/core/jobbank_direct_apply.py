"""Authenticated Job Bank Direct Apply lane.

Job Bank's official application surface is ``Direct Apply`` inside a signed-in
account.  It is a short first-party flow:

``posting -> Direct Apply -> instructions -> resume -> confirmation``.

The Direct Apply lane deliberately reuses a pre-existing ``jobbank_it`` NST
profile.  It never creates a browser profile and it is disabled until an
operator explicitly enables it after logging the profile in.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from jobbots.paths import MONOREPO_ROOT


DIRECT_APPLY_METHOD = "direct_apply"
_JOBBANK_HOST = "https://www.jobbank.gc.ca"
_ALREADY_APPLIED_MARKERS = (
    "you have successfully applied for this job through job bank",
    "you were previously matched to this job, and you marked it as applied",
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def direct_apply_enabled() -> bool:
    """Whether real Direct Apply submission is explicitly enabled."""
    return _truthy(os.getenv("JOBBANK_DIRECT_APPLY_ENABLED"))


def application_already_submitted_text(text: str) -> bool:
    """Recognize Job Bank's signed-in already-applied state."""
    normalized = " ".join(str(text or "").casefold().split())
    return any(marker in normalized for marker in _ALREADY_APPLIED_MARKERS)


def source_job_id(url: str) -> str:
    """Return the stable Job Bank posting id from either posting/apply URL."""
    match = re.search(r"/(?:jobposting|directapply)/(\d+)", url or "", re.I)
    if match:
        return match.group(1)
    return re.sub(r"[^a-zA-Z0-9]+", "-", url or "jobbank-unknown").strip("-")[:96]


def direct_apply_url(posting_url: str, discovered_url: str = "") -> str:
    """Prefer the live Direct Apply link; otherwise derive it from posting id."""
    for value in (discovered_url, posting_url):
        value = (value or "").strip()
        if "/directapply/" in value.lower():
            return urljoin(_JOBBANK_HOST, value)
    job_id = source_job_id(posting_url)
    return f"{_JOBBANK_HOST}/jobsearch/directapply/{job_id}"


def enqueue_direct_apply_job(
    *,
    posting_url: str,
    direct_url: str,
    title: str,
    company: str,
    location: str = "",
    description: str = "",
    profile: str = "it",
) -> tuple[str, bool]:
    """Put one discovered Direct Apply posting into the normal queue.

    The global apply-portal switch controls when these jobs can be leased by
    production workers.
    """
    from jobbots.core.job_queue import JobQueue

    posting_url = urljoin(_JOBBANK_HOST, posting_url)
    apply_url = direct_apply_url(posting_url, direct_url)
    job_id = source_job_id(posting_url or apply_url)
    return JobQueue().enqueue(
        portal="jobbank",
        profile=(profile or "it").strip().lower(),
        source_job_id=job_id,
        title=(title or "Job Bank role").strip(),
        company=(company or "Employer").strip(),
        url=posting_url,
        location=(location or "").strip(),
        description=(description or "")[:12000],
        gate_score=80,
        gate_reason="Job Bank Direct Apply listing",
        resume_policy="default",
        priority=120,
        metadata={
            "application_method": DIRECT_APPLY_METHOD,
            "direct_apply_url": apply_url,
            "source": "jobbank",
            "discovered_by": "jobbank_browser",
            "resume_required": True,
        },
    )


def _has_text(page: Any, text: str) -> bool:
    try:
        return page.get_by_text(text, exact=False).count() > 0
    except Exception:
        try:
            return text.lower() in (page.locator("body").inner_text(timeout=4000) or "").lower()
        except Exception:
            return False


def _already_applied(page: Any) -> bool:
    try:
        return application_already_submitted_text(page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return False


def _screenshot(page: Any, job: dict[str, Any], label: str) -> str:
    """Best-effort evidence for failures without changing the outcome path."""
    try:
        raw_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(job.get("source_job_id") or "jobbank"))
        out = MONOREPO_ROOT / "outputs" / "jobbank_direct_apply"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{raw_id}_{label}.png"
        page.screenshot(path=str(path), full_page=True, timeout=12000)
        return str(path)
    except Exception:
        return ""


def _click(page: Any, selector: str, *, timeout: int = 15000) -> None:
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=timeout)
    locator.click(timeout=timeout)


def _wait_for_direct_step(page: Any, *, timeout: int = 30000) -> None:
    """Wait until Job Bank has rendered the next Direct Apply step."""
    page.wait_for_function(
        """() => Boolean(
            document.querySelector('#docUploadSPForm') ||
            document.querySelector('form[action*="directapply-resume-coverletter"]') ||
            document.querySelector('form[action*="directapply-screening-questions"]')
        )""",
        timeout=timeout,
    )


def _wait_for_direct_navigation(page: Any, pattern: str) -> None:
    """Wait for a Job Bank step navigation, with a DOM fallback."""
    try:
        page.wait_for_url(pattern, timeout=30000)
    except Exception:
        _wait_for_direct_step(page)
    else:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass


def _advance_direct_step(page: Any, selector: str, pattern: str) -> None:
    """Click a non-final Direct Apply step, retrying one stalled JS click."""
    try:
        _click(page, selector)
        _wait_for_direct_navigation(page, pattern)
        return
    except Exception:
        # Job Bank's JSF link can remain visible when the first click races
        # page initialization. A single retry is safe because this helper is
        # only used for Continue controls, never the final Submit button.
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=10000)
        locator.click(timeout=15000)
        _wait_for_direct_navigation(page, pattern)


def _option_match(answer: str, options: list[str]) -> str:
    """Map an answer-model response to one of the visible form options."""
    raw = (answer or "").strip()
    lowered = raw.casefold()
    for option in options:
        label = (option or "").strip()
        if label and lowered == label.casefold():
            return label
    for option in options:
        label = (option or "").strip()
        if label and re.search(rf"\b{re.escape(label.casefold())}\b", lowered):
            return label
    return ""


def _select_stored_document(select: Any, *, preferred_name: str = "") -> str:
    """Select a saved Job Bank document, preferring a configured filename."""
    options = select.locator("option").all()
    candidates: list[tuple[str, str]] = []
    for option in options:
        value = (option.get_attribute("value") or "").strip()
        label = (option.inner_text() or "").strip()
        if value and label and label.casefold() not in {"select a resume", "select a cover letter"}:
            candidates.append((value, label))
    if not candidates:
        return ""
    preferred = (preferred_name or "").strip().casefold()
    chosen = next(
        ((value, label) for value, label in candidates if preferred and preferred in label.casefold()),
        candidates[0],
    )
    select.select_option(value=chosen[0])
    return chosen[1]


def _answer_jobbank_screening(page: Any, job: dict[str, Any]) -> tuple[bool, str]:
    """Answer required Direct Apply screening fields using the shared answer bank.

    Only answers that match a currently rendered option are selected.  This
    avoids guessing or submitting an unanswered required question.
    """
    try:
        from jobbots.core.shared_modules.form_answers import resolve_text
    except Exception as exc:
        return False, f"jobbank_screening_answer_engine_unavailable:{exc}"

    fieldsets = page.locator("form fieldset")
    if fieldsets.count() == 0:
        return False, "jobbank_screening_form_missing"
    context = "\n".join(
        value for value in (
            str(job.get("title") or ""),
            str(job.get("company") or ""),
            str(job.get("description") or ""),
        ) if value
    )[:10000]
    for index in range(fieldsets.count()):
        fieldset = fieldsets.nth(index)
        question = (fieldset.locator("legend").inner_text(timeout=5000) or "").strip()
        question = re.sub(r"^\s*\*\s*", "", question).strip()
        if not question:
            continue
        try:
            answer = str(resolve_text(
                question,
                hint="Job Bank Canada Direct Apply screening question",
                job_context=context,
                allow_ai=True,
            ) or "").strip()
        except Exception as exc:
            return False, f"jobbank_screening_answer_failed:q{index + 1}:{exc}"

        radios = fieldset.locator("input[type='radio']")
        if radios.count() > 0:
            labels = [label.strip() for label in fieldset.locator("label").all_inner_texts()]
            chosen = _option_match(answer, labels)
            if not chosen:
                return False, f"jobbank_screening_unmatched_answer:q{index + 1}"
            for radio_index in range(radios.count()):
                if radio_index < len(labels) and labels[radio_index].casefold() == chosen.casefold():
                    radios.nth(radio_index).check(timeout=10000)
                    break
            continue

        selects = fieldset.locator("select")
        if selects.count() > 0:
            options = [option.strip() for option in selects.first.locator("option").all_inner_texts()]
            chosen = _option_match(answer, [option for option in options if option.casefold() not in {"", "select an option"}])
            if not chosen:
                return False, f"jobbank_screening_unmatched_answer:q{index + 1}"
            selects.first.select_option(label=chosen)
            continue

        text_input = fieldset.locator("textarea, input:not([type='hidden']):not([type='radio']):not([type='checkbox'])").first
        if text_input.count() > 0 and answer:
            text_input.fill(answer)
            continue
        return False, f"jobbank_screening_unsupported_field:q{index + 1}"
    return True, "jobbank_screening_answered"


def _finish_session(browser: Any, pw: Any) -> None:
    keep = _truthy(os.getenv("KEEP_BROWSER")) or _truthy(os.getenv("NSTBROWSER_KEEP_ALIVE"))
    if keep:
        # Detach Playwright but leave the pre-opened NST profile running for
        # the next leased Job Bank row. This is important for daily NST quota.
        try:
            pw.stop()
        except Exception:
            pass
        return
    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass


def apply_jobbank_direct_queue_job(job: dict[str, Any], *, dry_run: bool = False) -> tuple[bool, str, str]:
    """Submit one authenticated Job Bank Direct Apply job.

    Returns ``(ok, reason, evidence_url)``.  A Job Bank confirmation page is
    the success criterion; reaching a submit button alone is never success.
    """
    if not direct_apply_enabled():
        return False, "jobbank_direct_apply_disabled", ""

    meta = dict(job.get("metadata") or {})
    posting_url = (job.get("url") or "").strip()
    apply_url = direct_apply_url(posting_url, str(meta.get("direct_apply_url") or ""))
    if dry_run:
        return True, "dry_run", apply_url

    # This is a browser portal, not the legacy SMTP lane.  The worker has
    # already stamped a real, existing jobbank_it NST profile into the env.
    os.environ["BOT_NAME"] = "jobbank_it"
    os.environ["JOB_PROFILE"] = "IT"
    from jobbots.core.browser.open_chrome import createBrowserSession

    browser = pw = None
    page = None
    try:
        _sb, page, _context, browser, pw = createBrowserSession(bot_name="jobbank_it")
        page.goto(posting_url or apply_url, wait_until="domcontentloaded", timeout=60000)
        if _has_text(page, "Your application was submitted to the employer") or _already_applied(page):
            return True, "already_confirmed", page.url

        # The captured posting has #btn-direct-apply.  Keep the href fallback
        # for the basic HTML/mobile rendering Job Bank sometimes serves.
        direct = page.locator("#btn-direct-apply, a[href*='/jobsearch/directapply/']").first
        if direct.count() > 0:
            href = (direct.get_attribute("href") or "").strip()
            if href:
                apply_url = urljoin(_JOBBANK_HOST, href)
            direct.click(timeout=15000)
            _wait_for_direct_navigation(page, "**/jobsearch/directapply/*")
        else:
            page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)

        page.wait_for_timeout(300)
        try:
            _wait_for_direct_step(page)
        except Exception:
            # Login/error pages have no Direct Apply form; let the normal
            # detection and evidence path below report the useful state.
            pass
        if _has_text(page, "Sign in") or _has_text(page, "Sign-in"):
            shot = _screenshot(page, job, "login_required")
            return False, f"jobbank_login_required{':' + shot if shot else ''}", page.url
        if _has_text(page, "Your application was submitted to the employer") or _already_applied(page):
            return True, "already_confirmed", page.url

        # Step 1: Application instructions -> Continue.
        if _has_text(page, "Application instructions - Direct Apply"):
            instruction_continue = "form[action*='/jobsearch/directapply/'] a.btn.btn-primary"
            if page.locator(instruction_continue).count() > 0:
                _advance_direct_step(page, instruction_continue, "**/jobsearch/directapply-*")
            else:
                _advance_direct_step(page, "a.btn.btn-primary, button.btn.btn-primary", "**/jobsearch/directapply-*")

        # Some postings add a screening-question step before the saved resume.
        # Continue through it, but never bypass an unanswered required field.
        for _ in range(3):
            if page.locator("#docUploadSPForm, form[action*='directapply-resume-coverletter']").count() > 0:
                break
            if "directapply-screening-questions" not in (page.url or "").lower() and not _has_text(
                page, "Screening questions - Direct Apply"
            ):
                break
            ok, screening_reason = _answer_jobbank_screening(page, job)
            if not ok:
                shot = _screenshot(page, job, "screening_failed")
                return False, f"{screening_reason}{':' + shot if shot else ''}", page.url
            continue_selector = (
                "form[action*='directapply-screening-questions'] "
                "input[type='submit'][value='Continue'], "
                "form[action*='directapply-screening-questions'] button[type='submit']"
            )
            _advance_direct_step(page, continue_selector, "**/jobsearch/directapply-*")

        page.wait_for_selector("#docUploadSPForm, form[action*='directapply-resume-coverletter']", timeout=30000)

        # Prefer the resume already stored in the signed-in Job Bank account.
        resume_select = page.locator("#docUploadSPForm\\:select_resume, select[name*='select_resume']").first
        if resume_select.count() > 0:
            resume_name = _select_stored_document(
                resume_select,
                preferred_name=os.getenv("JOBBANK_RESUME_NAME", "ls_resume.pdf"),
            )
            if not resume_name:
                shot = _screenshot(page, job, "resume_missing")
                return False, f"jobbank_resume_missing{':' + shot if shot else ''}", page.url

        # Some postings require a cover letter in addition to the resume.  Job
        # Bank stores these in the same signed-in account, so use the saved IT
        # document rather than trying to upload a new file or submit empty.
        cover_select = page.locator(
            "#docUploadSPForm\\:select_coverLetter, select[name*='select_coverLetter']"
        ).first
        if cover_select.count() > 0:
            cover_name = _select_stored_document(
                cover_select,
                preferred_name=os.getenv("JOBBANK_COVER_LETTER_NAME", "lscoop_coverletter.pdf"),
            )
            if not cover_name:
                shot = _screenshot(page, job, "cover_letter_missing")
                return False, f"jobbank_cover_letter_missing{':' + shot if shot else ''}", page.url

        # Sharing the application email is part of the captured Direct Apply
        # flow.  It is explicit and can be disabled for a particular run.
        different_email = page.locator(
            "#docUploadSPForm\\:input-diffemailcheck, input[name*='input-diffemailcheck']"
        ).first
        if different_email.count() and different_email.is_checked():
            # Never switch the application contact address away from the
            # Job Bank account email unless an operator explicitly changes
            # this policy in a future, separate flow.
            different_email.uncheck(timeout=10000)
        if _truthy(os.getenv("JOBBANK_SHARE_EMAIL", "1")):
            share = page.locator("#docUploadSPForm\\:input-shareemail, input[name*='input-shareemail']").first
            if share.count() > 0 and not share.is_checked():
                share.check(timeout=10000)

        submit = "#docUploadSPForm\\:btnSubmit, button[name*='btnSubmit'], input[name*='btnSubmit']"
        _click(page, submit)
        page.wait_for_timeout(800)
        page.wait_for_function(
            "() => document.body && document.body.innerText.includes('Your application was submitted to the employer')",
            timeout=30000,
        )
        return True, "jobbank_confirmation", page.url
    except Exception as exc:
        shot = _screenshot(page, job, "failed") if page is not None else ""
        detail = f"direct_apply_failed:{type(exc).__name__}:{exc}"
        return False, f"{detail}{':' + shot if shot else ''}", apply_url
    finally:
        if browser is not None or pw is not None:
            _finish_session(browser, pw)
