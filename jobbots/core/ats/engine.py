"""ApplicationEngine — orchestrates the full application flow.

Takes a job URL + applicant profile, detects the ATS platform, and
drives the adapter through the complete lifecycle.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from .base import ATSAdapter
from .registry import detect_adapter, detect_adapter_from_page, detect_platform
from .types import ApplicationResult, AdapterContext
from .mixins.questions import _reset_ai_budget, _get_ai_calls_used


# Legacy helpers kept for engine-level spam/dead-job checks. Adapter success
# classification lives in ``confirmation.py`` (page-primary policy).
_SUCCESS_RE = re.compile(
    r"(application (?:has been |was )?submitted|thanks for applying|"
    r"thank you for (?:your )?application|received your application|"
    r"application received|you applied|"
    r"your application (?:has been|was)|submission received|submitted successfully)",
    re.IGNORECASE,
)

_ALREADY_RE = re.compile(
    r"(already applied|you('ve| have) already submitted|duplicate application)",
    re.IGNORECASE,
)

_VERIFY_RE = re.compile(
    r"(verification code|security code was sent|sent a code|code sent|enter the code|enter code|one-time code|security code sent|confirm you.?re a human|captcha)",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    try:
        from jobbots.core.utils import print_lg  # type: ignore
        print_lg(msg)
    except Exception:
        print(msg)


_DEDICATED_EMAIL_PLATFORMS = frozenset({"greenhouse", "lever"})
_GREENHOUSE_LEVER_EMAIL = "user@example.com"


def _load_profile(platform: str = "") -> dict[str, Any]:
    """Load applicant fields, with a dedicated mailbox for Greenhouse/Lever."""
    from jobbots.core.shared_modules.form_answers import load_profile
    data = load_profile()
    if (platform or "").strip().lower() in _DEDICATED_EMAIL_PLATFORMS:
        # Keep Greenhouse and Lever verification codes out of the primary
        # Indeed/LinkedIn mailbox. This intentionally overrides ATS_EMAIL too.
        data["email"] = (
            os.getenv("ATS_GREENHOUSE_LEVER_EMAIL", _GREENHOUSE_LEVER_EMAIL).strip()
            or _GREENHOUSE_LEVER_EMAIL
        )
        return data
    # Prefer IMAP-readable mailbox for verification codes.
    try:
        from jobbots.core.secret_manager import get_secret
        imap_email = (get_secret("IMAP_EMAIL_IT") or get_secret("IMAP_EMAIL") or "").strip()
    except Exception:
        imap_email = (os.getenv("IMAP_EMAIL_IT") or os.getenv("IMAP_EMAIL") or "").strip()
    force = (os.getenv("ATS_EMAIL") or "").strip()
    if force:
        data["email"] = force
    elif imap_email and imap_email.lower() != (data.get("email") or "").lower():
        data["email"] = imap_email
    return data


def _page_text(page: Any, limit: int = 24000) -> str:
    """Extract visible text from the page."""
    try:
        return (page.evaluate(
            f"() => (document.body?.innerText || '').slice(0, {limit})"
        ) or "")
    except Exception:
        return ""


_DEAD_JOB_RE = re.compile(
    r"(sorry,?\s*we couldn.?t find anything here|"
    r"this (?:job|position|posting) (?:is )?(?:no longer available|has been (?:closed|removed|filled)|closed)|"
    r"job (?:posting )?(?:not found|has been removed|no longer exists)|"
    r"the job you.?re looking for (?:isn.?t|is not) available|"
    r"404 error|page not found|"
    r"this role is no longer open|"
    r"position has been filled)",
    re.I,
)

_SPAM_BLOCK_RE = re.compile(
    r"(flagged as possible spam|we couldn.?t submit your application|"
    r"submission was flagged|possible spam|"
    r"your application (?:could not|cannot) be submitted|"
    r"blocked (?:your|this) (?:application|submission))",
    re.I,
)


def _detect_dead_job(page: Any, url: str = "", *, allow_empty: bool = False) -> str | None:
    """Return a human reason when the job/application page is gone.

    ``allow_empty`` is only for pre-fill checks.  After submit the DOM can
    briefly be blank during navigation; that must not abort a retry loop.
    """
    url_low = (url or getattr(page, "url", "") or "").lower()
    if "error=true" in url_low:
        return "Job page reports error=true (posting closed or invalid)"
    text = _page_text(page, 8000)
    if allow_empty and not (text or "").strip():
        # Completely blank Bamboo/ATS shells often mean the posting is gone.
        try:
            body_len = int(
                page.evaluate("() => (document.body?.innerText || '').trim().length") or 0
            )
        except Exception:
            body_len = len((text or "").strip())
        if body_len == 0:
            # Only treat as dead when there is also no apply form chrome.
            try:
                has_form = bool(
                    page.query_selector(
                        "form, input[type='email'], input[name*='email' i], "
                        "button[type='submit'], input[type='submit']"
                    )
                )
            except Exception:
                has_form = False
            if not has_form:
                return "Job page rendered empty (likely closed or blocked)"
    m = _DEAD_JOB_RE.search(text or "")
    if m:
        return f"Job posting unavailable: {m.group(0)[:80]}"
    return None


def _detect_spam_or_block(page: Any) -> str | None:
    text = _page_text(page, 12000)
    m = _SPAM_BLOCK_RE.search(text or "")
    if m:
        return f"Application blocked by ATS anti-spam: {m.group(0)[:100]}"
    return None


class ApplicationEngine:
    """Orchestrates the complete ATS application flow."""

    def __init__(self, page: Any, *, title: str = "", company: str = "",
                 job_context: str = "", dry_run: bool = False):
        self.page = page
        self.job_title = title
        self.job_company = company
        self.job_context = job_context
        self.dry_run = dry_run
        self.profile: dict[str, Any] = {}
        self.adapter: ATSAdapter | None = None
        self.ctx = AdapterContext(page=page)

    def _run_primary_autofill(self) -> int:
        """Run native In-DOM Autofill Engine as primary filler for ATS application forms.

        Extracts DOM fields, resolves answers via profile/QA bank/DeepSeek AI in-memory,
        and injects values with React/Vue synthetic events. Zero external server dependencies.
        """
        from jobbots.core.ats.dom_autofill import DOMAutofillEngine

        job_ctx = f"Role: {self.job_title} at {self.job_company} ({self.job_context})"
        _log("  [ENGINE] [PRIMARY] Running native In-DOM Autofill Engine...")
        try:
            stats = DOMAutofillEngine.autofill(
                page=self.page,
                profile=self.profile,
                job_context=job_ctx,
            )
            _log(f"  [ENGINE] [PRIMARY] Native In-DOM autofill populated {stats.filled}/{stats.total} fields")
            return stats.filled
        except Exception as exc:
            _log(f"  [ENGINE] Native In-DOM autofill error (falling back to adapter): {exc}")
            return 0

    def run(self, url: str) -> ApplicationResult:
        """Execute the full application flow for a job URL."""
        _reset_ai_budget()
        self.ctx.job_url = url
        self.ctx.start_time = time.time()

        # 1. Detect platform before choosing the application mailbox.
        platform = detect_platform(url)
        if not platform:
            return ApplicationResult(
                success=False, result_url=url,
                reason=f"Unsupported ATS platform: {url}",
                ats_platform="unknown",
            )

        adapter_cls = detect_adapter(url)
        if not adapter_cls:
            return ApplicationResult(
                success=False, result_url=url,
                reason=f"No adapter registered for platform: {platform}",
                ats_platform=platform,
            )

        # 2. Load the profile after platform detection. Greenhouse and Lever
        # use their dedicated address; all other adapters retain current rules.
        self.profile = _load_profile(platform)
        self.ctx.profile = self.profile
        self.adapter = adapter_cls()
        self.ctx.job_title = self.job_title
        self.ctx.job_company = self.job_company
        self.ctx.job_context = self.job_context

        _log(f"  [ENGINE] Platform={platform} URL={url[:120]}")

        # 3. Navigate to the job URL (adapter initialize handles in-page nav).
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(1.0)
        except Exception as exc:
            _log(f"  [ENGINE] navigation warning: {exc}")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass

        # 3b. Initialize (wrapper-page / iframe / apply-form navigation)
        try:
            self.adapter.initialize(
                self.page, self.profile,
                job_title=self.job_title,
                job_company=self.job_company,
                job_context=self.job_context,
            )
        except Exception as exc:
            return ApplicationResult(
                success=False, result_url=self.page.url or url,
                reason=f"Initialization failed: {exc}",
                ats_platform=platform,
            )

        # 3c. Bail early on closed / 404 / empty postings (Lever, GH error=true, Bamboo).
        dead = _detect_dead_job(self.page, self.page.url or url, allow_empty=True)
        if dead:
            _log(f"  [ENGINE] dead job early exit: {dead}")
            return ApplicationResult(
                success=False, result_url=self.page.url or url,
                reason=dead,
                ats_platform=platform,
                elapsed_seconds=self.ctx.elapsed,
            )

        try:
            if not self.adapter.authenticate():
                return ApplicationResult(
                    success=False, result_url=self.page.url or url,
                    reason="Authentication failed",
                    ats_platform=platform,
                )
        except Exception as exc:
            _log(f"  [ENGINE] authenticate error (continuing): {exc}")

        # 5. Solve CAPTCHA on load
        # On-load CAPTCHAs on Greenhouse, Lever, Ashby, BambooHR should not gate
        # form filling; tokens expire before submit. Defer solving to step 8c.
        _skip_load_captcha = platform in ("bamboohr", "greenhouse", "lever", "ashby")
        if _skip_load_captcha:
            _log(f"  [ENGINE] Skipping on-load CAPTCHA for {platform} (deferred to pre-submit)")
        else:
            try:
                if not self.adapter.solve_captcha():
                    if self._page_has_captcha() and not self.dry_run:
                        return ApplicationResult(
                            success=False, result_url=self.page.url or url,
                            reason="CAPTCHA required on load — auto-solve failed and human wait timed out",
                            ats_platform=platform,
                        )
            except Exception as exc:
                _log(f"  [ENGINE] captcha error (continuing): {exc}")

        # 5b. Primary Handler: Native In-DOM Autofill Engine
        ext_filled = self._run_primary_autofill()
        if ext_filled:
            self.ctx.stats.filled += ext_filled

        # 6. Fallback & Gap Filling: Upload documents
        try:
            upload_result = self.adapter.upload_documents() or {}
            if upload_result.get("resume"):
                self.ctx.stats.file_uploaded += 1
            if upload_result.get("cover"):
                self.ctx.stats.file_uploaded += 1
        except Exception as exc:
            _log(f"  [ENGINE] upload error: {exc}")

        # 7. Fallback & Gap Filling: Fill standard fields
        try:
            fill_stats = self.adapter.fill_application()
            self.ctx.stats.merge(fill_stats)
        except Exception as exc:
            _log(f"  [ENGINE] fill_application error: {exc}")

        # 8. Fallback & Gap Filling: Answer questions
        try:
            _log("  [ENGINE] Calling adapter.answer_questions() ...")
            answered = self.adapter.answer_questions()
            _log(f"  [ENGINE] adapter.answer_questions() returned {answered}")
            self.ctx.stats.filled += answered
        except Exception as exc:
            _log(f"  [ENGINE] answer_questions error: {exc}")

        # 8b. Run repair_required_fields BEFORE CAPTCHA solve so DOM changes
        # (country/province dropdown commits) don't invalidate the token.
        try:
            repair = getattr(self.adapter, "repair_required_fields", None)
            if callable(repair):
                n = int(repair() or 0)
                if n:
                    _log(f"  [ENGINE] repair_required_fields filled={n} (pre-submit)")
        except Exception as exc:
            _log(f"  [ENGINE] repair_required_fields error (pre-submit): {exc}")

        # 8c. CAPTCHAs (reCAPTCHA / Turnstile / hCaptcha) often appear after fill / before submit.
        # Solve AFTER all field repairs to keep the token fresh for submit.
        try:
            if self._page_has_captcha() or getattr(self.adapter, "_page_has_captcha", lambda: False)():
                _log("  [ENGINE] CAPTCHA present pre-submit — attempting solve...")
                if self.adapter.solve_captcha():
                    self.ctx.captcha_solved = True
                else:
                    _log("  [ENGINE] Pre-submit CAPTCHA solve skipped/failed — proceeding to submit attempt...")
        except Exception as exc:
            _log(f"  [ENGINE] pre-submit captcha error (continuing): {exc}")

        # 9. Submit
        if self.dry_run:
            _log(f"  [ENGINE] [DRY-RUN] Bypassing submission for {url[:100]}")
            return ApplicationResult(
                success=True,
                result_url=self.page.url or url,
                reason="[DRY-RUN] Filled form fields successfully without submitting",
                ats_platform=platform,
                elapsed_seconds=self.ctx.elapsed,
                fields_filled=self.ctx.stats.filled,
                fields_skipped=self.ctx.stats.skipped,
                ai_calls_used=_get_ai_calls_used(),
            )

        result = self._submit_and_confirm(platform, url)
        result.elapsed_seconds = self.ctx.elapsed
        result.fields_filled = self.ctx.stats.filled
        result.fields_skipped = self.ctx.stats.skipped
        result.ai_calls_used = _get_ai_calls_used()
        result.captcha_solved = self.ctx.captcha_solved
        result.verification_method = self.ctx.verification_method
        return result

    def run_on_page(self) -> ApplicationResult:
        """Execute the ATS application flow on an *already-open* Playwright page."""
        _reset_ai_budget()
        url = self.page.url or ""
        self.ctx.job_url = url
        self.ctx.start_time = time.time()

        detected_adapter = detect_adapter_from_page(self.page)
        if not detected_adapter:
            return ApplicationResult(
                success=False,
                result_url=url,
                reason="Could not detect ATS platform on currently open page",
                ats_platform="unknown",
            )

        if isinstance(detected_adapter, type):
            platform = getattr(detected_adapter, "platform_name", "unknown")
            try:
                self.adapter = detected_adapter(log_fn=_log)
            except TypeError:
                self.adapter = detected_adapter()
        elif hasattr(detected_adapter, "platform_name"):
            platform = detected_adapter.platform_name
            self.adapter = detected_adapter
        elif callable(detected_adapter):
            self.adapter = detected_adapter()
            platform = getattr(self.adapter, "platform_name", "unknown")
        else:
            return ApplicationResult(
                success=False,
                result_url=url,
                reason="Detected ATS adapter is invalid",
                ats_platform="unknown",
            )

        self.ctx.platform = platform
        self.profile = _load_profile(platform)
        self.ctx.profile = self.profile

        self.adapter.initialize(self.page, self.profile, dry_run=self.dry_run)

        if hasattr(self.adapter, "is_application_page") and not self.adapter.is_application_page():
            return ApplicationResult(
                success=False,
                result_url=url,
                reason=f"Current page is not recognized as a {platform} application form",
                ats_platform=platform,
            )

        try:
            if not self.adapter.authenticate():
                pass  # Non-fatal
        except Exception:
            pass

        try:
            self.adapter.solve_captcha()
        except Exception:
            pass

        # Primary Handler: Native In-DOM Autofill Engine
        ext_filled = self._run_primary_autofill()
        if ext_filled:
            self.ctx.stats.filled += ext_filled

        # Fallback & Gap Filling: Upload documents
        try:
            upload_result = self.adapter.upload_documents() or {}
            if upload_result.get("resume"):
                self.ctx.stats.file_uploaded += 1
            if upload_result.get("cover"):
                self.ctx.stats.file_uploaded += 1
        except Exception as exc:
            _log(f"  [ENGINE] upload error: {exc}")

        # Fallback & Gap Filling: Fill standard fields
        try:
            fill_stats = self.adapter.fill_application()
            self.ctx.stats.merge(fill_stats)
        except Exception as exc:
            _log(f"  [ENGINE] fill error: {exc}")

        # Fallback & Gap Filling: Answer questions
        try:
            answered = self.adapter.answer_questions()
            self.ctx.stats.filled += answered
        except Exception as exc:
            _log(f"  [ENGINE] questions error: {exc}")

        # Pre-submit CAPTCHA solve attempt
        try:
            if self._page_has_captcha():
                if self.adapter.solve_captcha():
                    self.ctx.captcha_solved = True
                else:
                    _log("  [ENGINE] Pre-submit CAPTCHA solve skipped/failed — proceeding to submit...")
        except Exception as exc:
            _log(f"  [ENGINE] pre-submit captcha error: {exc}")

        if self.dry_run:
            _log(f"  [ENGINE] [DRY-RUN] Bypassing submission on page for {url[:100]}")
            return ApplicationResult(
                success=True,
                result_url=self.page.url or url,
                reason="[DRY-RUN] Filled form fields successfully without submitting",
                ats_platform=platform,
                elapsed_seconds=self.ctx.elapsed,
                fields_filled=self.ctx.stats.filled,
                fields_skipped=self.ctx.stats.skipped,
                ai_calls_used=_get_ai_calls_used(),
            )

        result = self._submit_and_confirm(platform, url)
        result.elapsed_seconds = self.ctx.elapsed
        result.fields_filled = self.ctx.stats.filled
        result.fields_skipped = self.ctx.stats.skipped
        result.ai_calls_used = _get_ai_calls_used()
        result.captcha_solved = self.ctx.captcha_solved
        result.verification_method = self.ctx.verification_method
        return result

    def _submit_and_confirm(self, platform: str, url: str) -> ApplicationResult:
        """Click submit, recover from validation/CAPTCHA, and wait for confirmation.

        Overnight shortlist failures showed Bamboo stuck on uncommitted
        Country/Province and Ashby forms that stayed after a soft submit.
        One re-fill + re-submit pass recovers most of those cases.
        """
        max_attempts = 3
        last_reason = "Failed to click submit button"

        for attempt in range(1, max_attempts + 1):
            # Pre-submit: adapter-specific required field repair (Bamboo country).
            try:
                repair = getattr(self.adapter, "repair_required_fields", None)
                if callable(repair):
                    n = int(repair() or 0)
                    if n:
                        _log(f"  [ENGINE] repair_required_fields filled={n} (attempt {attempt})")
            except Exception as exc:
                _log(f"  [ENGINE] repair_required_fields error: {exc}")

            # Re-solve captcha ONLY on the first attempt.  BambooHR re-renders
            # the reCAPTCHA widget after DOM changes, so re-solving in retry
            # loops burns CapMonster credits and times out.  The pre-submit
            # solve in step 8c (or attempt 1 here) is sufficient.
            if attempt == 1:
                try:
                    if self._page_has_captcha() or getattr(self.adapter, "_page_has_captcha", lambda: False)():
                        _log(f"  [ENGINE] CAPTCHA present pre-submit (attempt {attempt}) — CapMonster")
                        if self.adapter.solve_captcha():
                            self.ctx.captcha_solved = True
                            time.sleep(0.6)
                except Exception as exc:
                    _log(f"  [ENGINE] pre-submit captcha (attempt {attempt}): {exc}")
            elif self._page_has_captcha():
                _log(f"  [ENGINE] CAPTCHA present (attempt {attempt}) — skipping re-solve to avoid token invalidation")

            submit_ok = False
            try:
                submit_ok = bool(self.adapter.submit())
            except Exception as exc:
                _log(f"  [ENGINE] submit error (attempt {attempt}): {exc}")
                last_reason = f"Submit error: {exc}"

            if not submit_ok:
                captcha_hint = ""
                try:
                    if self._page_has_captcha():
                        captcha_hint = " (CAPTCHA still present — CapMonster did not clear reCAPTCHA v2)"
                except Exception:
                    pass
                dead = _detect_dead_job(self.page, self.page.url or url)
                if dead:
                    return ApplicationResult(
                        success=False, result_url=self.page.url or url,
                        reason=dead, ats_platform=platform,
                    )
                last_reason = f"Failed to click submit button{captcha_hint}"
                # One more pass of field repair before giving up on click.
                if attempt < max_attempts:
                    try:
                        self.adapter.answer_questions()
                    except Exception:
                        pass
                    continue
                try:  # capture the unresolved question area before dropping
                    from jobbots.core.apply_diagnostics import capture_unhandled_question
                    capture_unhandled_question(
                        self.page, portal=platform,
                        job_id=str(getattr(self.ctx, "job_id", "") or ""),
                        reason=last_reason,
                    )
                except Exception:
                    pass
                return ApplicationResult(
                    success=False, result_url=self.page.url or url,
                    reason=last_reason, ats_platform=platform,
                )


            # Intermediate attempts poll briefly; final attempt uses full timeout.
            polls = 50 if attempt >= max_attempts else 12
            result = self._wait_for_confirmation(platform, url, max_polls=polls)
            if result.success:
                return result

            spam = _detect_spam_or_block(self.page)
            if spam:
                return ApplicationResult(
                    success=False, result_url=self.page.url or url,
                    reason=spam, ats_platform=platform,
                )

            dead = _detect_dead_job(self.page, self.page.url or url)
            if dead:
                return ApplicationResult(
                    success=False, result_url=self.page.url or url,
                    reason=dead, ats_platform=platform,
                )

            # Validation / soft-fail: form still open with errors.
            errors: list[str] = []
            try:
                errors = list(getattr(self.adapter, "_validation_errors", lambda: [])() or [])
            except Exception:
                errors = []
            last_reason = result.reason or "Submit clicked but no page confirmation detected"
            if errors:
                last_reason = f"{last_reason}; validation: {'; '.join(errors[:4])}"
                _log(f"  [ENGINE] post-submit validation (attempt {attempt}): {errors[:4]}")

            if attempt >= max_attempts:
                try:  # capture the unresolved question area before dropping
                    from jobbots.core.apply_diagnostics import capture_unhandled_question
                    capture_unhandled_question(
                        self.page, portal=platform,
                        job_id=str(getattr(self.ctx, "job_id", "") or ""),
                        reason=last_reason,
                    )
                except Exception:
                    pass
                return ApplicationResult(
                    success=False, result_url=self.page.url or url,
                    reason=last_reason, ats_platform=platform,
                )

            _log(f"  [ENGINE] no page confirmation (attempt {attempt}) — re-fill + resubmit")

            try:
                self.adapter.fill_application()
            except Exception as exc:
                _log(f"  [ENGINE] re-fill error: {exc}")
            try:
                self.adapter.answer_questions()
            except Exception as exc:
                _log(f"  [ENGINE] re-answer error: {exc}")
            try:
                repair = getattr(self.adapter, "repair_required_fields", None)
                if callable(repair):
                    repair()
            except Exception:
                pass
            try:
                reupload = getattr(self.adapter, "reupload_resume_if_needed", None)
                if callable(reupload):
                    reupload()
            except Exception:
                pass

        return ApplicationResult(
            success=False, result_url=self.page.url or url,
            reason=last_reason, ats_platform=platform,
        )

    def _wait_for_confirmation(
        self, platform: str, url: str, *, max_polls: int = 50
    ) -> ApplicationResult:
        """Poll for **on-page** submission confirmation; email is secondary.

        Policy (2026-08):
          1. **Primary** — visible page confirmation (success banner, thank-you
             copy, confirmation URL). That alone is enough to mark applied.
          2. **Secondary** — application-receipt email (IMAP history/dedupe).
             We do **not** wait for a receipt email before succeeding.
          3. OTP / security-code email is only a *gate* for GH/Lever. After the
             code is entered we still require a page success signal.
        """
        submit_ts = time.time()
        verification_attempted = False
        polls = max(4, int(max_polls or 50))

        # Greenhouse/Lever often need >15s for thank-you/verification chrome.
        for i in range(polls):
            time.sleep(0.5)
            status = self.adapter.verify_submission()

            if status == "already_applied":
                if self.ctx.verification_method == "none":
                    self.ctx.verification_method = "page"
                return ApplicationResult(
                    success=True, result_url=self.page.url or url,
                    reason="Already applied to this job (page confirmation)",
                    ats_platform=platform,
                )
            if status == "submitted":
                if self.ctx.verification_method == "none":
                    self.ctx.verification_method = "page"
                evidence = str(getattr(self.adapter, "confirmation_evidence", "") or "").strip()
                return ApplicationResult(
                    success=True, result_url=self.page.url or url,
                    reason=(
                        f"{platform.title()} application submitted (page confirmation"
                        + (f": {evidence}" if evidence else "")
                        + ")"
                    ),
                    ats_platform=platform,
                )
            if status == "verification_required" and not verification_attempted:
                verification_attempted = True
                self.ctx.verification_method = "email_code"
                try:
                    ok_v, reason_v = self.adapter._complete_email_verification(
                        self.profile, not_before=submit_ts
                    )
                except Exception:
                    ok_v, reason_v = False, "Verification handler not available"
                if ok_v:
                    # Code entered + resubmit clicked — still require page success.
                    # OTP email is a gate only; page confirmation remains primary.
                    for _ in range(24):
                        time.sleep(0.5)
                        st2 = self.adapter.verify_submission()
                        if st2 == "submitted":
                            evidence = str(getattr(self.adapter, "confirmation_evidence", "") or "").strip()
                            return ApplicationResult(
                                success=True, result_url=self.page.url or url,
                                reason=(
                                    f"{platform.title()} application submitted "
                                    f"(page confirmation after IMAP OTP"
                                    + (f": {evidence}" if evidence else "")
                                    + ")"
                                ),
                                ats_platform=platform,
                            )
                        if st2 == "already_applied":
                            return ApplicationResult(
                                success=True, result_url=self.page.url or url,
                                reason="Already applied to this job (page confirmation)",
                                ats_platform=platform,
                            )
                    return ApplicationResult(
                        success=False, result_url=self.page.url or url,
                        reason=(
                            f"{reason_v}; no page confirmation after OTP"
                            if reason_v
                            else "Verification code entered but no page confirmation detected"
                        ),
                        ats_platform=platform,
                    )
                return ApplicationResult(
                    success=False, result_url=self.page.url or url,
                    reason=reason_v or "Verification required (email code / captcha) — form filled",
                    ats_platform=platform,
                )

        # Final page check after polling timeout (still no email-receipt wait).
        status = self.adapter.verify_submission()
        if status in ("submitted", "already_applied"):
            if self.ctx.verification_method == "none":
                self.ctx.verification_method = "page"
            evidence = str(getattr(self.adapter, "confirmation_evidence", "") or "").strip()
            if status == "already_applied":
                return ApplicationResult(
                    success=True, result_url=self.page.url or url,
                    reason="Already applied to this job (page confirmation)",
                    ats_platform=platform,
                )
            return ApplicationResult(
                success=True, result_url=self.page.url or url,
                reason=(
                    f"{platform.title()} application submitted (page confirmation"
                    + (f": {evidence}" if evidence else "")
                    + ")"
                ),
                ats_platform=platform,
            )

        return ApplicationResult(
            success=False, result_url=self.page.url or url,
            reason="Submit clicked but no page confirmation detected",
            ats_platform=platform,
        )

    def _page_has_captcha(self) -> bool:
        """Return true only for a visible, actionable CAPTCHA widget."""
        try:
            visible = self.page.evaluate("""() => {
                const selectors = [
                  "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']",
                  "iframe[src*='challenges.cloudflare.com']", ".g-recaptcha",
                  ".h-captcha", ".cf-turnstile"
                ];
                return selectors.some(sel => Array.from(document.querySelectorAll(sel)).some(el => {
                  const style = window.getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0 && rect.width >= 24 && rect.height >= 24;
                }));
            }""")
            if visible:
                return True
        except Exception:
            pass
        try:
            html = (self.page.content() or "")[:8000].lower()
            return "i am not a robot" in html or "verify you are human" in html
        except Exception:
            return False
