"""Shared types for ATS adapters."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApplicationResult:
    """Structured result returned after an application attempt."""
    success: bool
    result_url: str = ""
    reason: str = ""
    ats_platform: str = ""
    elapsed_seconds: float = 0.0
    fields_filled: int = 0
    fields_skipped: int = 0
    ai_calls_used: int = 0
    captcha_solved: bool = False
    # How success was proven: "page" (primary), "email_code" (OTP gate then page),
    # "captcha", "human", or "none". Application-receipt email is secondary and
    # is not required once the confirmation page is visible.
    verification_method: str = "none"

    def as_tuple(self) -> tuple[bool, str, str]:
        """Backward-compat with existing (ok, url, reason) callers."""
        return self.success, self.result_url, self.reason


@dataclass
class QuestionContext:
    """Everything we know about a single form question/field."""
    element: Any = None           # Playwright ElementHandle
    question_text: str = ""
    field_type: str = ""          # "text"|"textarea"|"select"|"radio"|"checkbox"|"combobox"|"file"
    options: list[str] = field(default_factory=list)
    required: bool = False
    section: str = ""             # "contact"|"education"|"eeo"|"custom"|"resume"
    field_id: str = ""
    field_name: str = ""


@dataclass
class FillStats:
    """Running counters for one application attempt."""
    filled: int = 0
    skipped: int = 0
    combobox: int = 0
    radio: int = 0
    checkbox: int = 0
    select: int = 0
    textarea: int = 0
    text: int = 0
    file_uploaded: int = 0

    def merge(self, other: "FillStats") -> None:
        for k in ("filled", "skipped", "combobox", "radio", "checkbox",
                  "select", "textarea", "text", "file_uploaded"):
            setattr(self, k, getattr(self, k, 0) + getattr(other, k, 0))


@dataclass
class AdapterContext:
    """Shared mutable state passed between engine phases."""
    page: Any = None              # Playwright Page
    profile: dict[str, Any] = field(default_factory=dict)
    job_title: str = ""
    job_company: str = ""
    job_context: str = ""
    job_url: str = ""
    ai_calls_used: int = 0
    captcha_solved: bool = False
    # "page" | "email_code" | "captcha" | "human" | "none"
    verification_method: str = "none"
    start_time: float = field(default_factory=time.time)
    stats: FillStats = field(default_factory=FillStats)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time
