"""CAPTCHA detection and solving mixin.

Wraps the existing CapMonster integration.  Detects reCAPTCHA v2/v3,
hCaptcha, and Cloudflare Turnstile.  Falls back to human-wait when
auto-solve fails.
"""
from __future__ import annotations

import os
import time
from typing import Any


class CaptchaMixin:
    """Mixin providing CAPTCHA detection and solving for ATS adapters."""

    page: Any  # Playwright Page — set by adapter

    def _log(self, msg: str) -> None:
        try:
            from jobbots.core.utils import print_lg  # type: ignore
            print_lg(msg)
        except Exception:
            print(msg)

    # ── detection ─────────────────────────────────────────────────────

    def _page_has_captcha(self) -> bool:
        """Return true only for an actionable, visible CAPTCHA challenge.

        Many ATS forms embed an invisible reCAPTCHA iframe as background
        telemetry.  Treating that marker alone as a challenge prevents all
        field filling, even when the form can submit normally.  A CAPTCHA is
        actionable only when its widget is visibly rendered (or its markup
        explicitly asks the applicant to verify they are human).
        """
        try:
            visible = self.page.evaluate("""() => {
                const selectors = [
                  "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']",
                  "iframe[src*='challenges.cloudflare.com']", ".g-recaptcha",
                  ".h-captcha", ".cf-turnstile"
                ];
                const isVisible = el => {
                  const style = window.getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0 && rect.width >= 24 && rect.height >= 24;
                };
                return selectors.some(sel => Array.from(document.querySelectorAll(sel)).some(isVisible));
            }""")
            if visible:
                return True
        except Exception:
            pass
        # Compatibility path for lightweight page implementations.  Keep the
        # same visibility requirement when locator APIs are available.
        try:
            for sel in (
                "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']",
                "iframe[src*='challenges.cloudflare.com']", ".g-recaptcha",
                ".h-captcha", ".cf-turnstile",
            ):
                el = self.page.query_selector(sel)
                if el and (not hasattr(el, "is_visible") or el.is_visible()):
                    return True
        except Exception:
            pass
        try:
            html = (self.page.content() or "")[:8000].lower()
            return "i am not a robot" in html or "verify you are human" in html
        except Exception:
            return False

    def _detect_captcha_type(self) -> str | None:
        """Return captcha type string or None."""
        self._log("Detecting CAPTCHA type...")
        try:
            # Wait up to 3 seconds for the CAPTCHA element/iframe to be present in the DOM
            try:
                self.page.wait_for_selector(
                    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], .g-recaptcha, .h-captcha, .cf-turnstile",
                    timeout=3000
                )
            except Exception:
                pass

            # Playwright's evaluate path is the most complete check, but a
            # selector check is also needed for lightweight/embedded page
            # implementations that expose DOM queries without JS evaluation.
            if self.page.query_selector("iframe[src*='recaptcha']"):
                self._log("Found recaptcha_v2 iframe via query_selector")
                return "recaptcha_v2"
            if self.page.query_selector(".g-recaptcha"):
                self._log("Found .g-recaptcha via query_selector")
                return "recaptcha_v2"
            if self.page.query_selector("iframe[src*='hcaptcha']"):
                self._log("Found hcaptcha iframe via query_selector")
                return "hcaptcha"
            if self.page.query_selector("iframe[src*='challenges.cloudflare.com']"):
                self._log("Found turnstile iframe via query_selector")
                return "turnstile"

            has_recaptcha = self.page.evaluate("""() => {
                const v2 = document.querySelector('.g-recaptcha');
                const v3 = document.querySelector('[data-sitekey]');
                const scripts = Array.from(document.querySelectorAll('script')).map(s => s.src || '');
                return {
                    v2: !!v2,
                    v3: !!v3,
                    hasV2Script: scripts.some(s => s.includes('recaptcha/api') && !s.includes('enterprise')),
                    hasV3Script: scripts.some(s => s.includes('recaptcha/api.js?render=')),
                };
            }""") or {}
            has_hcaptcha = self.page.evaluate("""() => {
                return {
                    widget: !!document.querySelector('#hcaptcha-widget'),
                    iframe: !!document.querySelector('iframe[src*="hcaptcha"]'),
                    div: !!document.querySelector('[class*="h-captcha"]'),
                };
            }""") or {}
            has_turnstile = self.page.evaluate("""() => {
                return {
                    checkbox: !!document.querySelector('.cf-turnstile[data-sitekey]'),
                    inline: !!document.querySelector('iframe[src*="challenges.cloudflare.com"]'),
                };
            }""") or {}

            if has_recaptcha.get('v2') or has_recaptcha.get('hasV2Script'):
                self._log("Found recaptcha_v2 via JS evaluation")
                return 'recaptcha_v2'
            if has_recaptcha.get('v3') or has_recaptcha.get('hasV3Script'):
                self._log("Found recaptcha_v3 via JS evaluation")
                return 'recaptcha_v3'
            if has_hcaptcha.get('widget') or has_hcaptcha.get('iframe') or has_hcaptcha.get('div'):
                self._log("Found hcaptcha via JS evaluation")
                return 'hcaptcha'
            if has_turnstile.get('checkbox') or has_turnstile.get('inline'):
                self._log("Found turnstile via JS evaluation")
                return 'turnstile'
        except Exception as e:
            self._log(f"Error in _detect_captcha_type: {e}")
            import traceback
            traceback.print_exc()
        self._log("No CAPTCHA detected")
        return None

    # ── solving ───────────────────────────────────────────────────────

    def solve_captcha(self) -> bool:
        """Detect and solve CAPTCHA.  Returns True if none present or solved."""
        captcha_type = self._detect_captcha_type()
        if not captcha_type:
            return True

        self._log(f"CAPTCHA detected: {captcha_type}")
        if self._solve_with_capmonster(captcha_type):
            # Mark adapter context if present (engine tracks captcha_solved).
            try:
                if hasattr(self, "ctx") and self.ctx is not None:
                    self.ctx.captcha_solved = True
            except Exception:
                pass
            return True

        # Unattended ATS farm: do not block 90s for a human unless explicitly enabled.
        if self._human_captcha_wait_enabled() and self._wait_for_human_captcha():
            return True

        return False

    def _human_captcha_wait_enabled(self) -> bool:
        """Human CAPTCHA wait is off by default for production ATS (CapMonster only)."""
        raw = (
            os.getenv("ATS_CAPTCHA_ALLOW_HUMAN_WAIT")
            or os.getenv("CAPTCHA_ALLOW_MANUAL_FALLBACK")
            or "0"
        )
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _solve_with_capsolver(self, captcha_type: str) -> bool:
        """Send CAPTCHA to CapSolver API and inject solution.

        Supports reCAPTCHA v2 / Enterprise, Turnstile, and hCaptcha.
        """
        try:
            from jobbots.core.evasion._capsolver import (
                solve_hcaptcha_with_capsolver,
                solve_recaptcha_with_capsolver,
                solve_turnstile_with_capsolver,
            )
            if captcha_type.startswith("recaptcha"):
                ok = solve_recaptcha_with_capsolver(self.page, timeout=120)
                self._log(f"CapSolver reCAPTCHA solve → {ok}")
                return bool(ok)
            if captcha_type == "hcaptcha":
                ok = solve_hcaptcha_with_capsolver(self.page, timeout=120)
                self._log(f"CapSolver hCaptcha solve → {ok}")
                return bool(ok)
            if captcha_type == "turnstile":
                ok = solve_turnstile_with_capsolver(self.page, timeout=120)
                self._log(f"CapSolver Turnstile solve → {ok}")
                return bool(ok)
            self._log(f"Unsupported CAPTCHA type for CapSolver: {captcha_type}")
        except ImportError:
            self._log("CapSolver module not available")
        except Exception as exc:
            self._log(f"CapSolver solve error: {exc}")

        return False

    def _solve_with_capmonster(self, captcha_type: str) -> bool:
        """Solve CAPTCHA via primary active solver (CapSolver) with CapMonster fallback if active."""
        # Check CapSolver first (active provider)
        use_capsolver = os.getenv("USE_CAPSOLVER", "1") not in {"0", "false", "no", "off"}
        if use_capsolver:
            ok = self._solve_with_capsolver(captcha_type)
            if ok:
                return True

        # CapMonster fallback (only if explicitly enabled)
        use_capmonster = os.getenv("USE_CAPMONSTER", "0") in {"1", "true", "yes", "on"}
        if not use_capmonster:
            return False

        try:
            from jobbots.core.evasion._capmonster import (
                solve_hcaptcha_with_capmonster,
                solve_recaptcha_with_capmonster,
                solve_turnstile_with_capmonster,
            )
            if captcha_type.startswith("recaptcha"):
                ok = solve_recaptcha_with_capmonster(self.page, timeout=120)
                self._log(f"CapMonster reCAPTCHA solve → {ok}")
                return bool(ok)
            if captcha_type == "hcaptcha":
                ok = solve_hcaptcha_with_capmonster(self.page, timeout=120)
                self._log(f"CapMonster hCaptcha solve → {ok}")
                return bool(ok)
            if captcha_type == "turnstile":
                ok = solve_turnstile_with_capmonster(self.page, timeout=120)
                self._log(f"CapMonster Turnstile solve → {ok}")
                return bool(ok)
            self._log(f"Unsupported CAPTCHA type for CapMonster: {captcha_type}")
        except Exception as exc:
            self._log(f"CapMonster solve error: {exc}")

        return False

    def _wait_for_human_captcha(self, timeout_s: int | None = None) -> bool:
        """Block until a visible CAPTCHA is cleared by a human (or timeout).

        Returns True if cleared, False on timeout.
        """
        if timeout_s is None:
            try:
                timeout_s = int(os.getenv("ATS_CAPTCHA_WAIT_TIMEOUT", "30") or "30")
            except ValueError:
                timeout_s = 30

        if not self._page_has_captcha():
            return True

        self._log(f"Waiting up to {timeout_s}s for human CAPTCHA solve...")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(2)
            if not self._page_has_captcha():
                self._log("CAPTCHA cleared (human)")
                return True
        self._log("Human CAPTCHA wait timed out")
        return False
