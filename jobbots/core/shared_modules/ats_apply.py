"""Greenhouse / Lever / Ashby / BambooHR form fill + submit.

Backward-compatible facade over the modular adapter engine in
``core.ats``.  The 4500-line monolith was refactored into:

  * ``core/ats/base.py``      — ATSAdapter ABC
  * ``core/ats/registry.py``  — URL detection + adapter registry
  * ``core/ats/engine.py``    — ApplicationEngine orchestrator
  * ``core/ats/mixins/``      — upload, captcha, questions, fields, verification
  * ``core/ats/adapters/``    — greenhouse, lever, ashby, bamboohr

Public API preserved for existing callers:

  * ``apply_url(page, url, title=..., company=...)`` → ``(ok, url, reason)``
  * ``apply_on_page(page, title=..., company=...)`` → ``(ok, url, reason)``
  * ``is_greenhouse_or_lever_url(url)`` — now true for all 4 platforms
  * ``page_looks_like_ats_apply(page)``

Test-facing helpers are re-exported from ``core.ats.mixins.questions``
so ``tests/test_ats_brain_fill.py`` keeps working unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jobbots.core.ats import apply_job as _apply_job
from jobbots.core.ats import apply_on_page as _engine_apply_on_page
from jobbots.core.ats.registry import (
    detect_adapter_from_page as _detect_adapter_from_page,
    is_supported_url as _is_supported_url,
)
from jobbots.core.ats.mixins import questions as _questions
from jobbots.core.ats.mixins.questions import (
    _ai_calls_max,
    _ats_ai_hint,
    _clean_question_text,
    _contains_unverified_coop_requirement,
    _contains_us_work_auth_question,
    _format_required_fail_reason,
    _get_ai_calls_used,
    _log_answer_event,
    _map_pref_to_option,
    _reset_ai_budget,
    _should_use_ai,
)

# Legacy import kept alive: ``scripts/apply_ats_fixed.py`` imports this name.
try:
    from jobbots.core.shared_modules import form_answers as _form_answers
except Exception:  # pragma: no cover - defensive
    _form_answers = None  # type: ignore[assignment]

# Legacy test hook: tests monkeypatch ``_ANSWER_LOG_PATH = None`` on this
# module to force path re-resolution; we sync it into the questions module.
_ANSWER_LOG_PATH: Path | None = None

__all__ = [
    "apply_url",
    "apply_on_page",
    "is_greenhouse_or_lever_url",
    "is_supported_ats_url",
    "page_looks_like_ats_apply",
    "_ats_ai_hint",
    "_clean_question_text",
    "_reset_ai_budget",
    "_should_use_ai",
    "_ai_calls_max",
    "_get_ai_calls_used",
    "_resolve_for_field",
    "_map_pref_to_option",
    "_format_required_fail_reason",
    "_contains_us_work_auth_question",
    "_contains_unverified_coop_requirement",
    "_log_answer_event",
    "_form_answers",
]


def _log(msg: str) -> None:
    try:
        from jobbots.core.utils import print_lg  # type: ignore
        print_lg(msg)
    except Exception:
        print(msg)


def is_greenhouse_or_lever_url(url: str | None) -> bool:
    """True for every supported ATS URL.

    Kept under the legacy name for backward compatibility; now matches
    Greenhouse, Lever, Ashby, and BambooHR (plus grnh.se / gh.io aliases).
    """
    return _is_supported_url(url)


def is_supported_ats_url(url: str | None) -> bool:
    """Properly-named alias for :func:`is_greenhouse_or_lever_url`."""
    return _is_supported_url(url)


def page_looks_like_ats_apply(page) -> bool:
    """Detect an ATS application page via URL, iframes, or DOM markers."""
    try:
        if _detect_adapter_from_page(page) is not None:
            return True
    except Exception:
        pass
    # Legacy fallback: generic submit-application chrome.
    try:
        html = (page.content() or "")[:12000].lower()
    except Exception:
        return False
    return "submit application" in html


def _resolve_for_field(
    question: str,
    *,
    profile: dict[str, Any],
    options: list[str] | None = None,
    job_context: str = "",
    hint: str = "",
    required: bool = False,
    portal: str = "",
    url: str = "",
) -> list[str] | None:
    """Use shared Indeed IT brain (policy + bank + DeepSeek) for this field."""
    # Honor test monkeypatches of this module's _ANSWER_LOG_PATH.
    _questions._ANSWER_LOG_PATH = _ANSWER_LOG_PATH
    return _questions._resolve_for_field(
        question,
        profile=profile,
        options=options,
        job_context=job_context,
        hint=hint,
        required=required,
        portal=portal,
        url=url,
    )


def apply_url(
    page, url: str, *, title: str = "", company: str = "", dry_run: bool | None = None
) -> tuple[bool, str, str]:
    """Navigate to *url* then fill/submit.

    Returns ``(success, result_url, reason)``.
    """
    if not is_greenhouse_or_lever_url(url):
        return False, url, "URL is not a supported ATS (greenhouse/lever/ashby/bamboohr)"
    result = _apply_job(page, url, title=title, company=company, dry_run=dry_run)
    return result.as_tuple()


def apply_on_page(
    page, *, title: str = "", company: str = "", dry_run: bool | None = None
) -> tuple[bool, str, str]:
    """Fill + submit an already-open ATS page.

    Handles company wrapper pages that embed the real form in an iframe.

    Returns ``(success, result_url, reason)``.
    """
    result = _engine_apply_on_page(page, title=title, company=company, dry_run=dry_run)
    return result.as_tuple()
