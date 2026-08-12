"""
Shared login UI for Glassdoor + Indeed one-login flows.

Both sites show the same patterns: Google/Apple first, email + Continue → below,
optional 'Sign in with a code instead', then email OTP and post-code Sign in → / passkey.
"""

from __future__ import annotations

import re
import time

from jobbots.core.evasion.captcha_handler import (
    check_and_handle_captcha,
    try_recover_page,
)
from jobbots.core.utils import print_lg

try:
    from config.settings import run_in_background
except ImportError:
    run_in_background = False

_T_ACTION = 0.2
_T_NAV = 0.4

# One selector per string (no comma-splitting). Used so we never match email-step inputs.
OTP_SELECTORS_STRICT_LIST: tuple[str, ...] = (
    'input[name="__passcode"]',
    'input[autocomplete="one-time-code"]',
    "#passcode",
    'input[inputmode="numeric"][name="code"]',
    'input[inputmode="numeric"][aria-label*="code"]',
    'input[placeholder*="Enter code"]',
    'input[placeholder*="enter code"]',
)


def unified_login_captcha_checkpoint(page, sb, context: str):
    """
    Same bypass stack as the main bots: Cloudflare (Turnstile) + reCAPTCHA when present.
    Call after navigations / clicks that often surface challenges (Apple or email, etc.).
    """
    check_and_handle_captcha(page, sb, context, run_in_background=run_in_background)
    return try_recover_page(page)


def _page_has_strict_otp_field(page) -> bool:
    try:
        for sel in OTP_SELECTORS_STRICT_LIST:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
    except Exception:
        pass
    return False


def unified_wait_for_passcode_ui(page, sb, otp_sel: str, log_prefix: str, timeout_s: float = 28.0):
    """
    Wait for OTP / passcode UI while periodically clearing Cloudflare or other challenges
    that replace the form during the wait.

    Only recognizes fields matched by OTP_SELECTORS_STRICT_LIST so we never treat a generic
    text/email box as the code field (which used to trigger IMAP before Continue).
    """
    deadline = time.time() + timeout_s
    ctx = log_prefix.strip("[]")
    while time.time() < deadline:
        try:
            page = try_recover_page(page)
        except Exception:
            pass
        page = unified_login_captcha_checkpoint(page, sb, f"{ctx} OTP / passcode wait")
        try:
            if _page_has_strict_otp_field(page):
                break
            
            # If we see the email field, the continue button failed or Cloudflare kicked us back.
            # We should not consider this a successful wait if the email field is prominent and OTP is not.
            from jobbots.core.portals.unified_indeed_glassdoor_login import EMAIL_SELECTORS_UNIFIED
            if page.locator(EMAIL_SELECTORS_UNIFIED).count() > 0 and page.locator(EMAIL_SELECTORS_UNIFIED).first.is_visible():
                print_lg(f"{log_prefix} Wait aborted: kicked back to email screen.")
                break
        except Exception:
            pass
        time.sleep(1.5)
    return page

EMAIL_SELECTORS_UNIFIED = (
    'input[type="email"], input[name="__email"], input[name="email"], '
    'input[id*="email"], input[autocomplete="username"], input[autocomplete="email"], '
    'input[data-testid*="email"], input[placeholder*="Email"], input[placeholder*="email"], '
    'input[aria-label*="Email"], input[aria-label*="email"]'
)

# Broader fallbacks for fill() only after strict selectors fail (still avoids unqualified text inputs).
OTP_SELECTORS_UNIFIED = (
    "input[name='__passcode'], input[autocomplete='one-time-code'], #passcode, "
    "input[inputmode='numeric'], input[type='tel'][name='code'], input[name='code'], "
    "input[aria-label*='code' i], input[aria-label*='Code'], "
    "input[placeholder*='code' i], input[placeholder*='Enter code' i]"
)


def unified_scroll_email_form_into_view(page, log_prefix: str) -> None:
    for needle in (
        "Email address",
        "All fields marked",
        "Create an account or sign in",
        "One login for jobs",
        "Glassdoor and Indeed",
        "One login to help you get hired",
        "anonymous reviews across Glassdoor and Indeed",
    ):
        try:
            loc = page.get_by_text(needle, exact=False)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.scroll_into_view_if_needed(timeout=5000)
                time.sleep(_T_ACTION)
                return
        except Exception:
            continue


def unified_try_apple_or_email_path(page, log_prefix: str) -> None:
    """Glassdoor+Indeed: 'Continue with Apple or email' opens the email path."""
    try:
        loc = page.get_by_role(
            "button",
            name=re.compile(r"continue\s+with\s+apple\s+or\s+email", re.I),
        )
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.click(timeout=10000)
            time.sleep(_T_NAV)
            print_lg(f"{log_prefix} Clicked Continue with Apple or email.")
            return
    except Exception:
        pass
    try:
        loc = page.get_by_text(re.compile(r"Continue with Apple or email", re.I))
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.click(timeout=10000)
            time.sleep(_T_NAV)
            print_lg(f"{log_prefix} Clicked Continue with Apple or email (text).")
    except Exception:
        pass


def unified_try_sign_in_here_link(page, log_prefix: str) -> None:
    """Optional: 'sign in here' for existing Glassdoor-only account."""
    try:
        loc = page.get_by_text(re.compile(r"sign\s+in\s+here", re.I))
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.click(timeout=8000)
            time.sleep(_T_NAV)
            print_lg(f"{log_prefix} Clicked sign in here.")
    except Exception:
        pass


def unified_ensure_email_filled(page, email: str, email_css: str, log_prefix: str) -> None:
    """Re-type email if the box was cleared (e.g. after 'Sign in with a code instead' navigation)."""
    try:
        el = page.query_selector(email_css)
        if not el or not el.is_visible():
            return
        try:
            current = (el.input_value() or "").strip()
        except Exception:
            current = ""
        if current.lower() != email.strip().lower():
            el.click(timeout=3000)
            el.fill(email, timeout=10000)
            time.sleep(_T_ACTION)
            print_lg(f"{log_prefix} Email field restored before Continue ({current!r} → filled).")
    except Exception as exc:
        print_lg(f"{log_prefix} Email ensure skipped: {exc}")


def unified_click_email_continue(page, log_prefix: str) -> None:
    try:
        for label in ("Continue →", "Continue"):
            grp = page.get_by_role("button", name=label)
            n = grp.count()
            for i in range(n):
                try:
                    btn = grp.nth(i)
                    if not btn.is_visible():
                        continue
                    txt = (btn.inner_text() or "").lower().strip()
                    if "google" in txt or "apple" in txt:
                        continue
                    btn.click(timeout=10000)
                    print_lg(f"{log_prefix} Clicked email Continue ({label!r}).")
                    return
                except Exception:
                    continue
    except Exception:
        pass
    try:
        sub = page.locator('form:has(input[type="email"]) button[type="submit"]').first
        if sub.count() > 0 and sub.is_visible():
            sub.click(timeout=10000)
            print_lg(f"{log_prefix} Clicked email <form> submit button.")
            return
    except Exception:
        pass
    try:
        sub = page.locator('form:has(input[type="email"]) input[type="submit"]').first
        if sub.count() > 0 and sub.is_visible():
            sub.click(timeout=10000)
            print_lg(f"{log_prefix} Clicked email <form> submit input.")
            return
    except Exception:
        pass
    try:
        narrow = page.locator("button").filter(has_text=re.compile(r"^\s*continue\s*→?\s*$", re.I))
        for i in range(narrow.count()):
            try:
                b = narrow.nth(i)
                if not b.is_visible():
                    continue
                t = (b.inner_text() or "").lower()
                if "google" in t or "apple" in t:
                    continue
                b.click(timeout=10000)
                print_lg(f"{log_prefix} Clicked Continue (pattern match).")
                return
            except Exception:
                continue
    except Exception:
        pass
    try:
        page.keyboard.press("Enter")
        print_lg(f"{log_prefix} Submitted email via Enter (no Continue button matched).")
    except Exception:
        pass


# Indeed / one-login: after email + Continue, Google shows "Welcome back" — OTP is only
# reached after "Sign in with a code instead" (or "Sign in via a code instead").
_CODE_INSTEAD_NAME = re.compile(
    r"sign\s+in\s+(with|via)\s+a\s+code\s+instead",
    re.I,
)


def unified_on_post_email_continue_progressed(page) -> bool:
    """True if we're past the first email + Continue step (interstitial, code link, or OTP)."""
    if _page_has_strict_otp_field(page):
        return True
    try:
        hint = page.get_by_text(
            re.compile(r"check\s+your\s+email\s+for\s+a\s+code", re.I)
        )
        if hint.count() > 0 and hint.first.is_visible():
            return True
    except Exception:
        pass
    try:
        loc = page.get_by_text(_CODE_INSTEAD_NAME)
        if loc.count() > 0 and loc.first.is_visible():
            return True
    except Exception:
        pass
    for pattern in (
        r"welcome\s+back",
        r"powered\s+by\s+google",
        r"continue\s+as\s+",
    ):
        try:
            h = page.get_by_text(re.compile(pattern, re.I))
            if h.count() > 0 and h.first.is_visible():
                return True
        except Exception:
            pass
    return False


def unified_recover_email_after_challenge(
    page, email: str, email_css: str, log_prefix: str
) -> None:
    """Re-scroll and re-type email after Cloudflare, reload, or CDP page swap."""
    unified_scroll_email_form_into_view(page, log_prefix)
    unified_ensure_email_filled(page, email, email_css, log_prefix)
    try:
        lbl = page.get_by_label(re.compile(r"email\s*address", re.I))
        if lbl.count() > 0 and lbl.first.is_visible():
            try:
                cur = (lbl.first.input_value() or "").strip()
            except Exception:
                cur = ""
            if cur.lower() != email.strip().lower():
                lbl.first.fill(email, timeout=10000)
                time.sleep(_T_ACTION)
                print_lg(f"{log_prefix} Re-filled email via label after challenge.")
    except Exception:
        pass


def unified_email_continue_after_cloudflare_retry(
    page, sb, email: str, email_css: str, log_prefix: str
):
    """
    After the first email Continue, Cloudflare can reload the form and clear the field
    (or the first Continue never navigates). Clear any challenge, re-fill, and press
    Continue once more if we're still stuck on the email step.
    """
    ctx = log_prefix.strip("[]")
    page = try_recover_page(page)
    check_and_handle_captcha(
        page,
        sb,
        f"{ctx} post-Continue recover",
        run_in_background=run_in_background,
    )
    page = try_recover_page(page)
    unified_recover_email_after_challenge(page, email, email_css, log_prefix)
    if unified_on_post_email_continue_progressed(page):
        return page
    try:
        el = page.query_selector(email_css)
        if not el or not el.is_visible():
            return page
        current = (el.input_value() or "").strip()
        if current.lower() != email.strip().lower():
            return page
        print_lg(
            f"{log_prefix} Still on email step after Cloudflare — clicking Continue again."
        )
        unified_click_email_continue(page, log_prefix)
        page = unified_login_captcha_checkpoint(
            page, sb, f"{ctx} after Continue (CF retry)"
        )
        page = try_recover_page(page)
        unified_recover_email_after_challenge(page, email, email_css, log_prefix)
    except Exception as exc:
        print_lg(f"{log_prefix} Cloudflare Continue retry skipped: {exc}")
    return page


def unified_wait_for_post_email_interstitial_or_otp(
    page, log_prefix: str, timeout_s: float = 22.0
) -> None:
    """
    After submitting email + Continue, wait for either the strict OTP field (rare shortcut)
    or the Google interstitial that exposes 'Sign in with a code instead'.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if _page_has_strict_otp_field(page):
                print_lg(f"{log_prefix} OTP field already visible after Continue (skip interstitial wait).")
                return
            loc = page.get_by_text(_CODE_INSTEAD_NAME)
            if loc.count() > 0:
                try:
                    if loc.first.is_visible():
                        return
                except Exception:
                    pass
            # "Welcome back" / Google copy often appears slightly before the link is clickable
            for pattern in (
                r"welcome\s+back",
                r"powered\s+by\s+google",
                r"continue\s+as\s+",
            ):
                try:
                    hint = page.get_by_text(re.compile(pattern, re.I))
                    if hint.count() > 0 and hint.first.is_visible():
                        return
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.45)
    print_lg(f"{log_prefix} Post-email interstitial wait ended ({timeout_s}s) — proceeding.")


def unified_switch_to_sign_in_with_code_instead(page, log_prefix: str) -> bool:
    """
    Prefer clicking 'Sign in with a code instead' when it exists. A hidden or irrelevant
    email <input> in the DOM must not skip that click — otherwise email gets filled on the
    wrong step and disappears when the real code flow loads.

    Call once on the initial form if that link is shown *before* email, and again after
    email + Continue when Indeed shows the Google 'Welcome back' interstitial.
    """
    probe_selectors = (
        'input[type="email"], input[name="__email"], input[name="email"], '
        'input[autocomplete="email"]'
    )
    clicked = False
    try:
        loc = page.get_by_role("link", name=_CODE_INSTEAD_NAME)
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.click(timeout=10000)
            clicked = True
    except Exception:
        pass
    if not clicked:
        try:
            loc = page.get_by_role("button", name=_CODE_INSTEAD_NAME)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=10000)
                clicked = True
        except Exception:
            pass
    if not clicked:
        try:
            loc = page.get_by_text(_CODE_INSTEAD_NAME)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=10000)
                clicked = True
        except Exception:
            pass
    if not clicked:
        try:
            loc = page.get_by_text("Sign in with a code instead", exact=True)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=10000)
                clicked = True
        except Exception:
            pass
    if not clicked:
        try:
            loc = page.locator("a:has-text('code instead')")
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=10000)
                clicked = True
        except Exception:
            pass
    if clicked:
        try:
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        time.sleep(_T_NAV)
        print_lg(f"{log_prefix} Chose 'Sign in with a code instead' (email / OTP path).")
        return True
    try:
        for sel in [s.strip() for s in probe_selectors.split(",") if s.strip()]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
    except Exception:
        pass
    return False


def unified_scroll_passcode_into_view(page, log_prefix: str) -> None:
    for needle in (
        "Enter code",
        "Check your email for a code",
        "one-time passcode",
    ):
        try:
            loc = page.get_by_text(needle, exact=False)
            if loc.count() > 0:
                loc.first.scroll_into_view_if_needed(timeout=5000)
                time.sleep(_T_ACTION)
                return
        except Exception:
            continue


def unified_complete_passcode_flow(page, sb, otp: str, otp_sel: str, log_prefix: str):
    page = unified_login_captcha_checkpoint(
        page, sb, f"{log_prefix.strip('[]')} before passcode"
    )
    unified_scroll_passcode_into_view(page, log_prefix)
    filled = False
    try:
        loc = page.get_by_label(re.compile(r"enter\s*code", re.I))
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.scroll_into_view_if_needed(timeout=5000)
            loc.first.fill(otp, timeout=10000)
            filled = True
            print_lg(f"{log_prefix} Filled passcode field (Enter code label).")
    except Exception as exc:
        print_lg(f"{log_prefix} Passcode label fill skipped: {exc}")
    if not filled:
        for sel in OTP_SELECTORS_STRICT_LIST:
            try:
                loc = page.locator(sel)
                if loc.count() < 1:
                    continue
                first = loc.first
                if not first.is_visible():
                    continue
                first.scroll_into_view_if_needed(timeout=5000)
                first.click(timeout=3000)
                first.fill(otp, timeout=10000)
                filled = True
                print_lg(f"{log_prefix} Filled passcode field (strict: {sel!r}).")
                break
            except Exception as exc:
                print_lg(f"{log_prefix} Passcode strict {sel!r} skipped: {exc}")
    if not filled:
        try:
            loc = page.locator(otp_sel).first
            if loc.count() > 0:
                loc.scroll_into_view_if_needed(timeout=5000)
                loc.click(timeout=3000)
                loc.fill(otp, timeout=10000)
                filled = True
                print_lg(f"{log_prefix} Filled passcode field (selectors).")
        except Exception as exc:
            print_lg(f"{log_prefix} Passcode selector fill skipped: {exc}")

    if not filled:
        print_lg(f"{log_prefix} ERROR: Could not find passcode field to fill.")
        return page

    try:
        page.keyboard.press("Enter")
    except Exception:
        pass
    time.sleep(1.2)

    for _ in range(4):
        try:
            page = try_recover_page(page)
        except Exception:
            pass
        try:
            page = unified_login_captcha_checkpoint(
                page, sb, f"{log_prefix.strip('[]')} passcode / post-login"
            )
        except Exception:
            pass
        try:
            not_now = page.get_by_text("Not now", exact=True)
            if not_now.count() > 0 and not_now.first.is_visible():
                not_now.first.click(timeout=6000)
                print_lg(f"{log_prefix} Dismissed passkey prompt (Not now).")
                time.sleep(0.8)
        except Exception:
            pass
        try:
            go = page.get_by_text("Sign in →", exact=True)
            if go.count() > 0 and go.first.is_visible():
                go.first.click(timeout=8000)
                print_lg(f"{log_prefix} Clicked Sign in →.")
                time.sleep(1.0)
        except Exception:
            pass
        try:
            btn = page.get_by_role("button", name=re.compile(r"^sign\s*in$", re.I))
            for i in range(btn.count()):
                try:
                    b = btn.nth(i)
                    if not b.is_visible():
                        continue
                    t = (b.inner_text() or "").lower()
                    if any(x in t for x in ("google", "apple", "passkey")):
                        continue
                    b.click(timeout=8000)
                    print_lg(f"{log_prefix} Clicked Sign in.")
                    time.sleep(1.0)
                    break
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.5)

    return page


def unified_fetch_otp_glassdoor_or_indeed(imap_email: str, imap_pwd: str) -> str:
    """Unified identity often emails from Indeed (login@indeed.com); try both domains."""
    from jobbots.core.imap_reader import get_latest_otp

    otp = get_latest_otp(imap_email, imap_pwd, "glassdoor.com")
    if otp:
        return otp
    return get_latest_otp(imap_email, imap_pwd, "indeed.com")
