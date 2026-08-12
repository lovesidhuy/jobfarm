"""Base abstract class for ATS adapters.

Each ATS platform implements this interface.  The ApplicationEngine
orchestrates the lifecycle: detect → initialize → authenticate →
uploadDocuments → fillApplication → answerQuestions → solveCaptcha →
submit → verifySubmission.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import ApplicationResult, FillStats


class ATSAdapter(ABC):
    """Abstract base for every ATS platform adapter."""

    platform_name: str = ""  # "greenhouse", "lever", "ashby", "bamboohr"

    # ── detection ─────────────────────────────────────────────────────
    @classmethod
    @abstractmethod
    def detect(cls, url: str) -> bool:
        """Return True if *url* belongs to this ATS platform."""
        ...

    @classmethod
    def detect_from_page(cls, page: Any) -> bool:
        """Optional: detect from an already-loaded page (iframes, DOM markers)."""
        try:
            return cls.detect(getattr(page, "url", "") or "")
        except Exception:
            return False

    # ── lifecycle hooks ───────────────────────────────────────────────
    @abstractmethod
    def initialize(self, page: Any, profile: dict[str, Any],
                   *, job_title: str = "", job_company: str = "",
                   job_context: str = "") -> None:
        """Store references; navigate to the application form if needed."""
        ...

    def is_application_page(self) -> bool:
        """Return True if the current page contains an application form."""
        return True

    def authenticate(self) -> bool:
        """Handle soft-auth (login, email verification).  Return True if ready."""
        return True

    @abstractmethod
    def upload_documents(self, **kwargs: Any) -> dict[str, bool]:
        """Upload resume + cover letter.  Return {"resume": bool, "cover": bool}."""
        ...

    @abstractmethod
    def fill_application(self) -> FillStats:
        """Fill standard fields (name, email, phone, etc.)."""
        ...

    @abstractmethod
    def answer_questions(self) -> int:
        """Answer custom / non-standard questions.  Return count answered."""
        ...

    def solve_captcha(self) -> bool:
        """Solve CAPTCHA if present.  Return True if none or solved."""
        return True

    @abstractmethod
    def submit(self) -> bool:
        """Click the submit button.  Return True on click success."""
        ...

    @abstractmethod
    def verify_submission(self) -> str | None:
        """Check the **page** for success/already-applied/verification states.

        Page confirmation is primary. Application-receipt email is secondary
        and must not be required once the page shows success.

        Returns one of:
          "submitted"  — confirmed success on the page
          "already_applied" — duplicate detected on the page
          "verification_required" — OTP / captcha gate (still need page success after)
          None — still on form, unknown state
        """
        ...

    # ── helpers available to every adapter ────────────────────────────
    def _log(self, msg: str) -> None:
        try:
            from jobbots.core.utils import print_lg  # type: ignore
            print_lg(f"  [{self.platform_name.upper()}] {msg}")
        except Exception:
            print(f"  [{self.platform_name.upper()}] {msg}")

    def safe_fill(self, page: Any, selector: str, value: str, timeout: int = 3000) -> bool:
        """Safely attempt to fill an input element with exception handling."""
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=timeout):
                loc.fill(value)
                return True
        except Exception as exc:
            self._log(f"safe_fill failed for '{selector}': {exc}")
        return False

    def safe_click(self, page: Any, selector: str, timeout: int = 3000) -> bool:
        """Safely attempt to click an element with exception handling."""
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                return True
        except Exception as exc:
            self._log(f"safe_click failed for '{selector}': {exc}")
        return False

    def is_element_visible(self, page: Any, selector: str, timeout: int = 1000) -> bool:
        """Check if an element is visible on page without throwing exceptions."""
        try:
            return bool(page.locator(selector).first.is_visible(timeout=timeout))
        except Exception:
            return False
