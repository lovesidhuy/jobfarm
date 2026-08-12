"""Email verification code mixin — handles IMAP-based verification flows.

Greenhouse and some other ATS platforms send verification codes via email.
This mixin polls IMAP for the code and fills it into the verification form.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any


class VerificationMixin:
    """Mixin providing email verification code handling for ATS adapters."""

    page: Any  # Playwright Page — set by adapter

    def _log(self, msg: str) -> None:
        try:
            from jobbots.core.utils import print_lg  # type: ignore
            print_lg(msg)
        except Exception:
            print(msg)

    @staticmethod
    def _fill_input(el: Any, value: str) -> bool:
        if value is None or value == "":
            return False
        try:
            el.fill("")
            el.fill(str(value))
            return True
        except Exception:
            try:
                el.evaluate(
                    """(node, val) => {
                        node.focus();
                        node.value = val;
                        node.dispatchEvent(new Event('input', {bubbles:true}));
                        node.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    str(value),
                )
                return True
            except Exception:
                return False

    @staticmethod
    def _visible(el: Any) -> bool:
        try:
            return bool(el and el.is_visible())
        except Exception:
            return False

    def _fill_verification_code(self, code: str) -> bool:
        """Fill a verification code into OTP inputs (single-digit boxes or single input)."""
        if not code:
            return False
        code = code.strip()

        # Try single-digit OTP boxes first
        chars = [c for c in code]
        otp_inputs = self.page.query_selector_all(
            "input[maxlength='1'], input[autocomplete='one-time-code']"
        )
        if otp_inputs and len(otp_inputs) >= len(chars) and chars:
            for i, char in enumerate(chars[:len(otp_inputs)]):
                try:
                    otp_inputs[i].fill(char)
                    time.sleep(0.05)
                except Exception:
                    pass
            return True

        # Try single verification code input (accepts alphanumeric code)
        for sel in (
            "input[name='verification_code']",
            "input[name='code']",
            "input[name='otp']",
            "input[id*='code' i]",
            "input[id*='otp' i]",
            "input[id*='verif' i]",
            "input[placeholder*='code' i]",
            "input[aria-label*='code' i]",
        ):
            try:
                el = self.page.query_selector(sel)
                if el and self._visible(el):
                    if self._fill_input(el, code):
                        return True
            except Exception:
                continue

        return False

    def _complete_email_verification(self, profile: dict[str, Any],
                                     *, not_before: float = 0) -> tuple[bool, str]:
        """Poll IMAP for a verification code and fill it.

        Args:
            profile: Applicant profile (uses email for IMAP credentials).
            not_before: Unix timestamp — only accept emails after this time.

        Returns (success, reason).
        """
        email_addr = (profile.get("email") or "").strip()
        if not email_addr:
            return False, "No email in profile for verification"

        self._log(f"Polling IMAP for verification code sent to {email_addr}...")

        try:
            from jobbots.core.imap_reader import get_latest_greenhouse_code
            from jobbots.core.secret_manager import get_secret
            mail_it = (get_secret("IMAP_EMAIL_IT") or "").strip().lower()
            mail_gen = (get_secret("IMAP_EMAIL_GENERAL") or "").strip().lower()
            mail_gh_lever = (
                get_secret("ATS_GREENHOUSE_LEVER_EMAIL")
                or os.getenv("ATS_GREENHOUSE_LEVER_EMAIL")
                or "user@example.com"
            ).strip().lower()
            
            target = email_addr.lower()
            if target == mail_gh_lever:
                # Prefer the dedicated Greenhouse/Lever app password. If vault
                # only has the primary IT app password and the mailbox is still
                # readable with it (common when one Gmail owns both aliases),
                # fall through so ATS verification is not hard-dead.
                app_password = (
                    get_secret("ATS_GREENHOUSE_LEVER_IMAP_APP_PASSWORD")
                    or get_secret("IMAP_APP_PASSWORD_GREENHOUSE_LEVER")
                    or os.getenv("ATS_GREENHOUSE_LEVER_IMAP_APP_PASSWORD")
                    or os.getenv("IMAP_APP_PASSWORD_GREENHOUSE_LEVER")
                    or get_secret("IMAP_APP_PASSWORD_IT")
                    or get_secret("IMAP_APP_PASSWORD")
                    or os.getenv("IMAP_APP_PASSWORD_IT")
                    or os.getenv("IMAP_APP_PASSWORD")
                    or ""
                )
                if app_password and not (
                    get_secret("ATS_GREENHOUSE_LEVER_IMAP_APP_PASSWORD")
                    or get_secret("IMAP_APP_PASSWORD_GREENHOUSE_LEVER")
                    or os.getenv("ATS_GREENHOUSE_LEVER_IMAP_APP_PASSWORD")
                ):
                    self._log(
                        "ATS Greenhouse/Lever IMAP: using IMAP_APP_PASSWORD_IT "
                        "fallback (set ATS_GREENHOUSE_LEVER_IMAP_APP_PASSWORD for dedicated mailbox)"
                    )
            elif target == mail_it:
                app_password = get_secret("IMAP_APP_PASSWORD_IT") or get_secret("IMAP_APP_PASSWORD")
            elif target == mail_gen:
                app_password = (
                    get_secret("IMAP_APP_PASSWORD_GENERAL")
                    or get_secret("IMAP_APP_PASSWORD")
                )
            else:
                app_password = get_secret("IMAP_APP_PASSWORD_IT") or get_secret("IMAP_APP_PASSWORD")
        except Exception as exc:
            self._log(f"IMAP modules not available: {exc}")
            return False, f"IMAP modules not available: {exc}"

        if not app_password:
            self._log("IMAP app password not configured in vault/env")
            return False, "IMAP app password not configured"

        try:
            code = get_latest_greenhouse_code(
                email_addr,
                app_password,
                not_before=not_before,
            )
        except Exception as exc:
            self._log(f"IMAP poll error: {exc}")
            return False, f"IMAP poll failed: {exc}"

        if not code:
            return False, "No verification code received via IMAP"

        self._log(f"Filling verification code from IMAP ({code[:2]}******)")
        if not self._fill_verification_code(code):
            return False, f"Verification code received but inputs not found ({code[:2]}******)"

        # Submit again after code entry
        for sel in (
            "button:has-text('Submit application')",
            "button:has-text('Verify')",
            "button:has-text('Submit')",
            "button[type='submit']",
            "input[type='submit']",
        ):
            try:
                btn = self.page.query_selector(sel)
                if btn and self._visible(btn):
                    btn.click(force=True, timeout=4000)
                    break
            except Exception:
                continue
        else:
            # Some forms auto-submit on last digit; try Enter
            try:
                self.page.keyboard.press("Enter")
            except Exception:
                pass

        time.sleep(1.2)
        return True, "Verification code submitted via IMAP"
