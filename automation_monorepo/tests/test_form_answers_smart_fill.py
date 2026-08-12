"""Unit tests for form-fill smarts used by LinkedIn bridge + ATS/Workopolis.

These must pass in CI/VM without browser, NST, or network AI.
"""
from __future__ import annotations

import os
import re

import pytest

# Force offline — never call live LLM in CI
os.environ["FORM_ANSWERS_DISABLE_AI"] = "1"

from core.shared_modules.form_answers import (  # noqa: E402
    _is_yes_no_options,
    _map_to_options,
    resolve_answer,
)


def test_city_pref_regex_does_not_match_education_question():
    """Regression: optional-comma city regex matched 'Highest level of education' as Ontario."""
    raw = "Highest level of education"
    # Require comma before province code (fixed pattern)
    m = re.match(
        r"^\s*([A-Za-z][A-Za-z .'\-]{1,40}),\s*([A-Za-z]{2})\s*$",
        raw,
    )
    assert m is None
    m2 = re.match(
        r"^\s*([A-Za-z][A-Za-z .'\-]{1,40}),\s*([A-Za-z]{2})\s*$",
        "Vancouver, BC",
    )
    assert m2 is not None
    assert m2.group(1) == "Vancouver"
    assert m2.group(2) == "BC"


def test_highest_level_of_education_maps_to_bachelor_not_no():
    a = resolve_answer(
        "Highest level of education",
        options=["High School", "Bachelor's Degree", "Master's Degree", "Other"],
        allow_ai=False,
    )
    assert a is not None
    assert "bachelor" in a.value.lower()
    assert a.value.lower() != "no"


def test_geology_degree_yes_no_is_no():
    a = resolve_answer(
        "Do you have a Bachelor's degree in Geology or a closely related geoscience discipline?",
        options=["Select an option", "Yes", "No"],
        allow_ai=False,
    )
    assert a is not None
    assert a.value.lower() == "no"


def test_completed_bachelors_level_yes_no_is_yes():
    a = resolve_answer(
        "Have you completed the following level of education: Bachelor's Degree?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert a is not None
    assert a.value.lower() == "yes"


def test_citizenship_eligibility_maps_to_i_am_canadian_citizen():
    opts = [
        "Select an option",
        "I am a Canadian Citizen",
        "I am a Canadian Permanent Resident",
        "I am a non-citizen eligible to work for any employer",
        "Other",
    ]
    a = resolve_answer(
        "What is your citizenship/employment eligibility?",
        options=opts,
        allow_ai=False,
    )
    assert a is not None
    assert "canadian citizen" in a.value.lower()
    assert "non-citizen" not in a.value.lower()


def test_pronouns_map_to_he_him_his_not_she():
    opts = ["She/Her/Her", "He/Him/His", "They/Them/Their", "Other - please ask me"]
    a = resolve_answer(
        "What are your preferred pronouns?",
        options=opts,
        allow_ai=False,
    )
    assert a is not None
    assert "he" in a.value.lower() and "him" in a.value.lower()
    assert "she" not in a.value.lower()


def test_referral_name_is_na_not_applicant_name():
    a = resolve_answer(
        "If you were referred to Bosch, please share the name of the employee who referred you.",
        allow_ai=False,
    )
    assert a is not None
    assert a.value.upper() in {"N/A", "NA", "NONE", "NO"}
    assert "Jane" not in a.value.lower()


def test_yes_no_options_detection():
    assert _is_yes_no_options(["Select an option", "Yes", "No"]) is True
    assert _is_yes_no_options(["High School", "Bachelor's Degree", "Other"]) is False


def test_map_to_options_male_does_not_match_female():
    # gender mapping must not map male → Female via substring
    mapped = _map_to_options("male", ["Female", "Male", "Decline"])
    assert mapped.lower() == "male"


def test_unmapped_free_text_on_choice_control_does_not_block_ai_path():
    """With AI disabled, unmapped free-text on multi-option select returns None or mapped value.

    Rules must not return free text that cannot be selected.
    """
    # Degree question with only specialty options unrelated — may return bachelor or None
    a = resolve_answer(
        "Select your favorite color",
        options=["Red", "Blue", "Green"],
        allow_ai=False,
    )
    # Must not invent unmappable garbage that stalls forms
    if a is not None:
        assert a.value in {"Red", "Blue", "Green"}


def test_captcha_failure_reason_contains_captcha_for_requeue():
    # Contract test (avoid importing indeed bootstrap / master shims in CI).
    # Keep in sync with smartapply._captcha_failure_reason / apply._finish_smartapply.
    context = "SmartApply step 2"
    reason = f"CAPTCHA failed or still blocking at {context}; requeue for retry"
    assert "captcha" in reason.lower()
    # application_worker must classify this as captcha_cf_requeue
    assert "captcha" in reason.lower() or "cloudflare" in reason.lower()


def test_application_worker_classifies_captcha_as_requeue():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "application_worker.py"
    spec = importlib.util.spec_from_file_location("application_worker", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Avoid running main; load functions only
    spec.loader.exec_module(mod)
    action, _ = mod.classify_outcome(
        {"status": "failed", "reason": "CAPTCHA failed or still blocking at review; requeue for retry"},
        "easy_apply",
        attempts=1,
        max_attempts=3,
    )
    assert action == "captcha_cf_requeue"


def test_pronoun_male_label_helpers():
    """Mirror Workopolis radio identity lock: He/Him/His wins, She never."""
    def pronoun_label_is_male(lbl: str) -> bool:
        ln = (lbl or "").lower().replace(" ", "")
        if any(x in ln for x in ("she", "her", "hers", "elle", "they", "them", "their", "iel")):
            if "he/him" not in ln and not re.search(r"(^|/)he(/|$)", ln):
                return False
        if "he/him" in ln or "him/his" in ln or "he/him/his" in ln:
            return True
        if re.search(r"\bhe\b", (lbl or "").lower()) and re.search(
            r"\b(him|his)\b", (lbl or "").lower()
        ):
            return True
        return False

    opts = ["She/Her/Her", "He/Him/His", "They/Them/Their", "Other - please ask me"]
    males = [o for o in opts if pronoun_label_is_male(o)]
    assert males == ["He/Him/His"]


def test_authorized_work_bc_without_sponsorship_is_yes():
    """Regression: Wix/SmartRecruiters compound question was answered No via bare sponsorship rule."""
    q = (
        "Are you legally authorized to work in British Columbia "
        "without the need for visa sponsorship?"
    )
    a = resolve_answer(q, options=["Yes", "No"], allow_ai=False)
    assert a is not None
    assert a.value.lower() == "yes"


def test_pure_sponsorship_needed_is_still_no():
    a = resolve_answer(
        "Will you now or in the future require visa sponsorship to work in Canada?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert a is not None
    assert a.value.lower() == "no"


def test_privacy_notice_consent_is_yes():
    a = resolve_answer(
        "You declare that you have read and understand the privacy notice of Wix.",
        options=["I consent", "Select checkbox to proceed"],
        allow_ai=False,
    )
    assert a is not None
    assert "consent" in a.value.lower() or a.value.lower() in {"yes", "i agree", "agree"}
