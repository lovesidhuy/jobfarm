"""Post-submit confirmation policy for ATS adapters.

**Primary signal: on-page confirmation** after Submit.
  * Explicit success copy (banner / thank-you / "submitted successfully")
  * Confirmation / thank-you URL tokens
  * Already-applied copy on the page

**Secondary signal: email** (IMAP receipts, application-received mail).
  * Useful for discovery dedupe and history sync.
  * **Not required** to mark an application ``applied`` when the page
    already shows a clear confirmation.
  * OTP / security-code emails remain a *gate* (``verification_required``)
    for Greenhouse/Lever only — after the code is entered we still need
    a page success signal.

**Never treat as success:**
  * Submit button / form disappearance alone (Ashby SPA / Bamboo CAPTCHA
    false positives, 2026-08-08).
  * Soft marketing on the job page ("thanks for your interest") without
    application-received language.
"""
from __future__ import annotations

import re
from typing import Any

# ── URL tokens that mean the browser left the form for a receipt page ─
SUCCESS_URL_TOKENS: tuple[str, ...] = (
    "confirmation",
    "/thanks",
    "thank_you",
    "thank-you",
    "application-complete",
    "application_complete",
    "application-submitted",
    "submitted=1",
    "success=true",
)

# Explicit success copy observed on live boards (Ashby / Bamboo / GH / Lever).
# Prefer application-received language over bare "thank you".
SUCCESS_RE = re.compile(
    r"("
    # Demo-grade strings (user-verified 2026-08)
    r"your application was submitted successfully|"
    r"your application was successfully submitted|"
    r"application was successfully submitted|"
    r"application (?:has been |was )?successfully submitted|"
    r"application (?:has been |was )?submitted(?: successfully)?|"
    r"successfully submitted(?: your)? application|"
    r"application submitted|"
    # Thank-you variants that name the application
    r"thanks for applying|"
    r"thank you for applying|"
    r"thank you for (?:your )?application|"
    r"thanks for your application|"
    r"thank you[,!.]?\s+your application|"
    # Receipt language
    r"we(?:'ve| have) received your application|"
    r"we've received your application|"
    r"we received your application|"
    r"your application (?:has been|is|was) (?:received|submitted)|"
    r"application received|"
    r"submission (?:was |has been )?received|"
    r"successfully applied|"
    r"application is complete|"
    r"application (?:was|has been) (?:successfully )?(?:sent|submitted)|"
    r"your application is on its way|"
    r"application sent|"
    r"application completed|"
    r"application recorded|"
    r"profile has been created|"
    r"information has been sent|"
    r"we(?:'ll| will) (?:be in touch|review your application)"
    r")",
    re.I,
)

ALREADY_RE = re.compile(
    r"(already applied|you('ve| have) already submitted|duplicate application|"
    r"application already exists)",
    re.I,
)

# Soft marketing on open job pages — must NOT alone count as submitted.
SOFT_THANKS_RE = re.compile(
    r"(thanks for your interest|thank you for your interest|"
    r"look forward to (?:hearing|reviewing)|we look forward)",
    re.I,
)

ERROR_RE = re.compile(
    r"(there (?:was|is) (?:a |an )?(?:error|problem)|please fix|required field|"
    r"could not submit|unable to submit|try again|is required|"
    r"please complete|fill out this field|must be completed|"
    r"flagged as possible spam|we couldn.?t submit|"
    r"submission was flagged|possible spam)",
    re.I,
)

VERIFY_RE = re.compile(
    r"(verification code|security code was sent|sent a code|code sent|"
    r"enter the .{0,12}code|enter code|one-time code|security code sent|"
    r"confirm you.?re a human|verify you.?re human|captcha)",
    re.I,
)

# Visible banner / heading selectors used after submit (Playwright).
SUCCESS_SELECTORS: tuple[str, ...] = (
    "h1:has-text('Thank you')",
    "h2:has-text('Thank you')",
    "h1:has-text('Thank You')",
    "h2:has-text('Thank You')",
    "h1:has-text('Application submitted')",
    "h2:has-text('Application submitted')",
    "h1:has-text('Success')",
    "h2:has-text('Success')",
    "text=Your application was successfully submitted",
    "text=Your application was submitted successfully",
    "text=successfully submitted",
    "text=We received your application",
    "[role='status']:has-text('Thank you')",
    "[role='status']:has-text('successfully submitted')",
    "[role='status']:has-text('submitted successfully')",
    "[role='alert']:has-text('Thank you')",
    "[role='alert']:has-text('successfully submitted')",
    "[data-testid*='success' i]",
    "[class*='success' i]:has-text('Success')",
    "[class*='success' i]:has-text('submitted')",
    ".application-confirmation",
    ".thanks",
    "[class*='thanks' i]",
    "[data-qa*='thanks' i]",
    ".main-header-text:has-text('Application submitted')",
)


def url_looks_like_confirmation(url: str) -> bool:
    """True when the location itself is a post-submit receipt route."""
    low = (url or "").lower()
    if not low:
        return False
    # Bare ".../application" is the Ashby form route — not confirmation.
    if re.search(r"/application(?:/|$|\?)", low):
        if not any(tok in low for tok in SUCCESS_URL_TOKENS):
            return False
    return any(tok in low for tok in SUCCESS_URL_TOKENS) or bool(
        re.search(r"(?:^|[/?#&=])(?:thanks|confirmation)(?:$|[/?#&=])", low)
    )


def _visible(el: Any) -> bool:
    try:
        return bool(el and el.is_visible())
    except Exception:
        return False


def _scan_success_dom(page: Any, text: str) -> str | None:
    """Return evidence string if a success banner/heading is visible."""
    if page is None:
        return None
    for sel in SUCCESS_SELECTORS:
        try:
            el = page.query_selector(sel)
            if not el or not _visible(el):
                continue
            try:
                label = (el.inner_text() or sel or "")[:120]
            except Exception:
                label = sel
            # Require real submit language somewhere (label or page text).
            if ERROR_RE.search(text or ""):
                continue
            if (
                SUCCESS_RE.search(label)
                or SUCCESS_RE.search(text or "")
                or re.search(
                    r"successfully submitted|submitted successfully|"
                    r"application (?:was |has been )?submitted|"
                    r"thank you|thanks for (?:your )?application|"
                    r"we received your application",
                    label,
                    re.I,
                )
            ):
                return f"success banner: {label!r}"
        except Exception:
            continue
    return None


def classify_page_confirmation(
    url: str,
    text: str,
    *,
    page: Any = None,
    platform: str = "",
    on_form_route: bool | None = None,
) -> tuple[str | None, str]:
    """Classify post-submit page state from URL + visible text (+ optional DOM).

    Returns ``(status, evidence)`` where status is one of:
      * ``"submitted"``
      * ``"already_applied"``
      * ``"verification_required"``
      * ``None`` — still unknown / still on form

    Never promotes form-gone alone to success.
    """
    url = url or ""
    text = text or ""
    plat = (platform or "").strip().lower()
    evidence = ""

    # Hard failure chrome first.
    if re.search(
        r"(flagged as possible spam|we couldn.?t submit your application|"
        r"submission was flagged|possible spam)",
        text,
        re.I,
    ):
        return None, ""

    if ALREADY_RE.search(text):
        return "already_applied", "already-applied copy on page"

    success_url = url_looks_like_confirmation(url)
    # Ashby form route without success tokens is never a confirmation URL.
    if on_form_route is None and plat == "ashby":
        on_form_route = bool(re.search(r"/application(?:/|$|\?)", url.lower())) and not success_url

    success_match = SUCCESS_RE.search(text)
    if success_match and not ERROR_RE.search(text):
        # Reject soft-only marketing when the only hit is weak interest language.
        snippet = success_match.group(0)
        if SOFT_THANKS_RE.fullmatch(snippet.strip()) and not success_url:
            pass
        else:
            evidence = f"success text: {snippet[:80]!r}"
            return "submitted", evidence

    if VERIFY_RE.search(text):
        return "verification_required", ""

    # OTP inputs still on the page (Greenhouse).
    if page is not None:
        try:
            if page.query_selector(
                "input[maxlength='1'], input[autocomplete='one-time-code'], "
                "input[name*='verification' i], input[name*='security' i], "
                "input[name*='otp' i], input[id*='verification' i], "
                "input[id*='security' i], input[id*='otp' i]"
            ):
                return "verification_required", ""
        except Exception:
            pass

    banner = _scan_success_dom(page, text)
    if banner:
        return "submitted", banner

    if success_url and not ERROR_RE.search(text):
        return "submitted", f"confirmation URL: {url}"

    # Still on application form route → not done (Ashby SPA stays on /application
    # until a real success banner appears; that path is handled above).
    if on_form_route:
        return None, ""

    # Explicit policy: do NOT treat submit-button disappearance as success.
    return None, ""


def evidence_for_bamboo_copy(text: str) -> str:
    """Human-readable Bamboo evidence labels used in queue reasons."""
    if re.search(r"your application was submitted successfully", text or "", re.I):
        return "visible text: Your application was submitted successfully"
    if re.search(r"we(?:'ve| have)? received your application", text or "", re.I):
        return "visible text: We received your application"
    return "visible application-success text"
