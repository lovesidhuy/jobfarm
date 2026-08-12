"""Indeed SmartApply date-field rules (bare Date *, signature, start, DOB)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _load_date_helpers():
    """Load pure helpers from questions.py without full Indeed bootstrap."""
    text = Path(__file__).resolve().parents[2].joinpath(
        "jobbots/core/shared_modules/indeed/questions.py"
    ).read_text()
    start = text.index("def _safe_date_answer_for_question")
    end = text.index("\ndef _is_interview_availability_question")
    # include format + fill helpers for import completeness if present
    chunk = text[start:end]
    # Also pull format helper which sits after the original return of date answer.
    # After our edit, _date_format_variants is between date answer and interview helper?
    # Actually order is: safe_date, date_format, fill_date, interview...
    # So end index of interview is wrong if format is in between.
    # Find from safe_date through fill_date end.
    start = text.index("def _safe_date_answer_for_question")
    end = text.index("\ndef _is_interview_availability_question")
    # Wait - fill is before interview now. Re-find.
    # Current order after edit:
    # _safe_date_answer_for_question
    # _date_format_variants
    # _fill_date_control
    # _is_interview_availability_question
    end = text.index("\ndef _is_interview_availability_question")
    ns = {"re": re}
    exec(text[start:end], ns)
    return ns["_safe_date_answer_for_question"], ns["_date_format_variants"]


@pytest.fixture(scope="module")
def date_helpers():
    return _load_date_helpers()


def test_bare_date_label_is_today(date_helpers):
    safe, _ = date_helpers
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for hint in ("date", "Date", "Date *", "the date", "input-q_xyz-date Date"):
        assert safe(hint, start, today) == today, hint


def test_start_availability_is_tomorrow(date_helpers):
    safe, _ = date_helpers
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert safe("desired start date", start, today) == start
    assert safe("available to start", start, today) == start
    assert safe("start date", start, today) == start


def test_dob_and_history_dates_are_skipped(date_helpers):
    safe, _ = date_helpers
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for hint in ("date of birth", "birthdate", "until", "end date", "date completed"):
        assert safe(hint, start, today) is None, hint


def test_date_format_variants_include_iso(date_helpers):
    _, variants = date_helpers
    today = datetime.now().strftime("%Y-%m-%d")
    out = variants(today)
    assert out[0] == today
    assert f"{today[5:7]}/{today[8:10]}/{today[0:4]}" in out
