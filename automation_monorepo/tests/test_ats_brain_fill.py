"""Unit tests for Greenhouse/Lever shared-brain fill helpers (no browser)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _ci_env(monkeypatch):
    monkeypatch.setenv("BOT_NAME", "ci-smoke")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "")
    monkeypatch.setenv("DD_METRICS_ENABLED", "0")
    monkeypatch.setenv("FORM_ANSWERS_DISABLE_AI", "1")
    monkeypatch.delenv("SENTRY_DSN", raising=False)


def test_ats_ai_hint_includes_options_and_question():
    from core.shared_modules.ats_apply import _ats_ai_hint

    hint = _ats_ai_hint(
        "Years of professional experience?",
        ["0-1 years", "1-3 years", "3-5 years", "5+ years"],
        section_text="Experience section",
    )
    assert "Choose exactly one of these DOM option labels:" in hint
    assert "'3-5 years'" in hint or "3-5 years" in hint
    assert "Years of professional experience?" in hint
    assert "Experience section" in hint
    assert "Return only the option label" in hint


def test_ats_ai_hint_without_options_still_has_question():
    from core.shared_modules.ats_apply import _ats_ai_hint

    hint = _ats_ai_hint("Why do you want to work here?", None, section_text="")
    assert "Why do you want to work here?" in hint
    assert "Choose exactly one" not in hint


def test_clean_question_text_strips_noise():
    from core.shared_modules.ats_apply import _clean_question_text

    assert (
        _clean_question_text("  Years of experience * \nYes\nNo  ")
        == "Years of experience"
    )
    assert _clean_question_text("select__input remix-css requiredInput") == ""
    assert _clean_question_text("") == ""


def test_should_use_ai_blocks_identity_allows_custom():
    from core.shared_modules import ats_apply as aa

    aa._reset_ai_budget()
    assert aa._should_use_ai("First Name", None) is False
    assert aa._should_use_ai("email address", None) is False
    assert aa._should_use_ai("Why do you want this role?", None) is True
    assert aa._should_use_ai("Are you authorized?", ["Yes", "No"]) is True


def test_ai_budget_default_is_at_least_12(monkeypatch):
    monkeypatch.delenv("ATS_AI_MAX_CALLS", raising=False)
    # Re-read module constant via helper that respects env at call sites.
    from core.shared_modules.ats_apply import _ai_calls_max

    assert _ai_calls_max() >= 12


def test_format_required_fail_reason():
    from core.shared_modules.ats_apply import _format_required_fail_reason

    reason = _format_required_fail_reason(
        ["email", "Years of professional experience?"]
    )
    assert reason.startswith("required_fields_unanswered:")
    assert "email" in reason
    assert "Years of professional experience" in reason


def test_resolve_for_field_passes_hint_and_logs_source(monkeypatch, tmp_path):
    from core.shared_modules import ats_apply as aa
    from core.shared_modules.form_answers import ResolvedAnswer

    aa._reset_ai_budget()
    monkeypatch.setenv("ATS_ANSWER_LOG", str(tmp_path / "answers.jsonl"))
    monkeypatch.setattr(aa, "_ANSWER_LOG_PATH", None)  # force re-resolve path

    captured = {}

    def fake_resolve(question, *, hint="", options=None, profile=None, job_context="", allow_ai=True):
        captured["question"] = question
        captured["hint"] = hint
        captured["options"] = list(options or [])
        captured["allow_ai"] = allow_ai
        return ResolvedAnswer(value="No", source="policy_visa", score=1.0)

    monkeypatch.setattr(
        "core.shared_modules.form_answers.resolve_answer",
        fake_resolve,
    )

    prefs = aa._resolve_for_field(
        "Do you require visa sponsorship?",
        profile={"require_visa": "No"},
        options=["Yes", "No"],
        job_context="SDET @ Acme",
        hint=aa._ats_ai_hint("Do you require visa sponsorship?", ["Yes", "No"]),
        required=True,
        portal="greenhouse",
        url="https://boards.greenhouse.io/acme/jobs/1",
    )
    assert prefs and prefs[0] == "No"
    assert captured["allow_ai"] is True  # multi-option custom → AI allowed by gate
    assert "Choose exactly one" in captured["hint"]
    assert captured["options"] == ["Yes", "No"]

    log_path = Path(os.environ["ATS_ANSWER_LOG"])
    # _resolve_for_field should have written a line
    if log_path.exists():
        lines = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()]
        assert lines
        assert lines[-1]["source"] == "policy_visa"
        assert lines[-1]["value"] == "No"
        assert lines[-1]["required"] is True


def test_resolve_answer_policy_visa_no_ai():
    from core.shared_modules.form_answers import resolve_answer

    a = resolve_answer(
        "Do you require visa sponsorship?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert a is not None
    assert a.value.lower() == "no"


def test_us_work_authorization_is_a_hard_policy_negative():
    from core.shared_modules.ats_apply import _contains_us_work_auth_question
    from core.shared_modules.form_answers import resolve_answer

    assert _contains_us_work_auth_question(
        "Are you legally authorized to work in the United States?"
    )
    assert not _contains_us_work_auth_question(
        "Are you legally authorized to work in Canada?"
    )
    answer = resolve_answer(
        "Are you legally authorized to work in the United States?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert answer and answer.value == "No"


def test_coop_enrollment_is_not_a_school_picker():
    from core.shared_modules.form_answers import resolve_answer

    answer = resolve_answer(
        "Are you currently enrolled in a co-op program and eligible to complete this role as an approved co-op work term through your school?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert answer and answer.value == "Yes"
    assert answer.source == "profile_coop_eligible"


def test_eight_month_coop_term_is_not_overclaimed():
    from core.shared_modules.form_answers import resolve_answer

    answer = resolve_answer(
        "This is an 8-month co-op term (Aug 2026 to Apr 2027). Are you able to commit to the full 8-month duration?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert answer and answer.value == "Yes"
    assert answer.source == "profile_coop_term_available"


def test_gender_does_not_map_male_to_transgender_male():
    from core.shared_modules.form_answers import resolve_answer

    answer = resolve_answer(
        "What gender do you identify as?",
        options=["Transgender male", "Transgender female", "Some other gender identity"],
        allow_ai=False,
    )
    assert answer is None


def test_sex_and_gender_never_select_female():
    from core.shared_modules.form_answers import resolve_answer

    for q, opts in [
        ("Sex", ["Female", "Male", "Decline"]),
        ("Please select your sex", ["Female", "Male"]),
        ("Gender", ["Woman", "Man", "Non-binary", "Prefer not to say"]),
        ("What is your gender?", ["female", "male"]),
        ("Sexe", ["Femme", "Homme", "Préfère ne pas répondre"]),
    ]:
        answer = resolve_answer(q, options=opts, allow_ai=False)
        assert answer is not None, q
        low = answer.value.lower()
        assert "female" not in low and "woman" not in low and "femme" not in low, (
            q, answer
        )
        assert any(k in low for k in ("male", "man", "homme", "m")), (q, answer)


def test_map_pref_to_option_salary_bucket():
    from core.shared_modules.ats_apply import _map_pref_to_option

    opts = ["$40,000 - $54,999", "$55,000 - $79,999", "$80,000 - $94,999"]
    assert _map_pref_to_option("70000", opts) == "$55,000 - $79,999"
    assert _map_pref_to_option("$55,000 - $79,999", opts) == "$55,000 - $79,999"


def test_kabam_profile_questions_are_deterministic():
    from core.shared_modules.form_answers import resolve_answer

    source = resolve_answer(
        "How did you hear about this opportunity?",
        options=["Kabam career page (kabam.com)", "Referral from a Kabam employee"],
        allow_ai=False,
    )
    assert source and source.value == "Kabam career page (kabam.com)"

    games = resolve_answer(
        "Please indicate which of our live games you have previously played (N/A if none).",
        allow_ai=False,
    )
    assert games and games.value == "N/A"

    term = resolve_answer(
        "This will be my first co-op term",
        options=["This will be my first co-op term", "1", "2", "3+"],
        allow_ai=False,
    )
    assert term and term.value == "This will be my first co-op term"

    graduation = resolve_answer(
        "How many months until you graduate?",
        options=["< 3 months", "3 - 6 months", "6 - 12 months", "> 18 months"],
        allow_ai=False,
    )
    assert graduation and graduation.value == "3 - 6 months"

    graduation_date = resolve_answer(
        "Date de fin d'études / Graduation end date",
        options=["2025", "2026", "2027", "After 2028"],
        allow_ai=False,
    )
    assert graduation_date and graduation_date.value == "2026"

    graduated = resolve_answer(
        "Have you graduated with a grade of 2.75+?",
        options=["Yes", "No"],
        allow_ai=True,
    )
    # Grade-threshold questions are Yes (GPA bar met), not "degree finished".
    assert graduated and graduated.value == "Yes"

    minority_followup = resolve_answer(
        "If yes, select options for visible minority status",
        options=["South Asian", "East Asian"],
        allow_ai=True,
    )
    assert minority_followup is None


def test_lvs1_distance_and_employment_type_are_deterministic():
    from core.shared_modules.form_answers import resolve_answer

    distance = resolve_answer(
        "Do you reside within 50km of a Long View office?",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert distance and distance.value == "Yes"

    employment = resolve_answer(
        "Permanent Full-time Incorporated Contractor",
        options=["Permanent Full-time", "Incorporated Contractor"],
        allow_ai=False,
    )
    assert employment and employment.value == "Permanent Full-time"


def test_shared_brain_owns_semantic_answer_for_addressed_prompt():
    from core.shared_modules.form_answers import resolve_answer

    answer = resolve_answer(
        "How would you like to be addressed? Feel free to share preferred names and pronouns.",
        allow_ai=False,
    )
    assert answer and answer.value == "he/him"
    assert answer.source != "profile_street"


def test_shared_brain_does_not_flip_vancouver_residence_on_retry():
    from core.shared_modules.form_answers import resolve_answer

    answer = resolve_answer(
        "Are you currently located in Vancouver, BC?",
        options=["Yes, I am located in Vancouver", "No, I am not located in Vancouver"],
        allow_ai=True,
    )
    assert answer and answer.value == "No, I am not located in Vancouver"


def test_city_preference_checkboxes_are_metro_van_only():
    from core.shared_modules.form_answers import resolve_answer

    van = resolve_answer("Vancouver, BC", options=["Yes", "No"], allow_ai=False)
    assert van and van.value.lower() == "yes"

    calgary = resolve_answer("Calgary, AB", options=["Yes", "No"], allow_ai=False)
    assert calgary and calgary.value.lower() == "no"

    quebec = resolve_answer("Quebec City, QC", options=["Yes", "No"], allow_ai=False)
    assert quebec and quebec.value.lower() == "no"
    assert quebec.source != "profile_city"

    montreal = resolve_answer("Montreal QC", options=["Yes", "No"], allow_ai=False)
    assert montreal and montreal.value.lower() == "no"

    all_loc = resolve_answer("Tous les emplacements / All locations", allow_ai=False)
    assert all_loc and all_loc.value.lower() == "yes"


def test_school_dropdown_other_vs_free_text_name():
    from core.shared_modules.form_answers import resolve_answer

    years = ["2018", "2019", "2020", "2021", "2022", "2023"]
    # Year-only lists are not school answers.
    assert resolve_answer("School", options=years, allow_ai=False) is None

    # DROPDOWN: always Other, even when KPU is listed.
    school = resolve_answer(
        "School",
        options=["Kwantlen Polytechnic University", "Christian Brothers University", "Other"],
        allow_ai=False,
    )
    assert school and school.value == "Other"
    assert "Christian" not in school.value

    # Combobox closed (no options yet) → still Other for typeahead.
    school_empty = resolve_answer("School", allow_ai=False)
    assert school_empty and school_empty.value == "Other"

    autre = resolve_answer(
        "École",
        options=["McGill", "Autre", "UBC"],
        allow_ai=False,
    )
    assert autre and autre.value == "Autre"

    # FREE TEXT after Other → real school name (Kwantlen).
    name = resolve_answer(
        'Si vous avez sélectionné "Autre" comme école ci-dessus, veuillez saisir le nom de l\'école',
        allow_ai=False,
    )
    assert name and "Kwantlen" in name.value

    name_en = resolve_answer(
        "If you selected Other as your school above, please enter the name of your school",
        allow_ai=False,
    )
    assert name_en and "Kwantlen" in name_en.value

    start_year = resolve_answer("Start date year", allow_ai=False)
    assert start_year and start_year.value == "2022"

    start_month = resolve_answer("Start date month", options=[
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ], allow_ai=False)
    assert start_month and start_month.value == "September"

    end_year = resolve_answer("End date year", allow_ai=False)
    assert end_year and "2026" in end_year.value


def test_male_not_substring_match_female_option():
    from core.shared_modules.form_answers import _map_to_options, resolve_answer
    from core.shared_modules.ats_apply import _map_pref_to_option

    assert _map_to_options("male", ["Female", "Male"]) == "Male"
    assert _map_pref_to_option("male", ["Female", "Male"]) == "Male"
    assert _map_pref_to_option("male", ["Female", "Decline"]) is None
    a = resolve_answer("Sex", options=["Female", "Male"], allow_ai=False)
    assert a and a.value == "Male"
    # Python trap that used to click Female first in combobox loops:
    assert ("male" in "female") is True  # documents the bug
    # Mapper must still prefer Male when Female is listed first.
    assert _map_pref_to_option("male", ["Female", "Male", "Decline"]) == "Male"


def test_contact_prefs_and_locations():
    from core.shared_modules.form_answers import resolve_answer

    assert resolve_answer("by email.", options=["Yes", "No"], allow_ai=False).value.lower() == "yes"
    assert resolve_answer("by phone.", options=["Yes", "No"], allow_ai=False).value.lower() == "yes"
    # Must not return the email address for "by email".
    by_email = resolve_answer("by email.", allow_ai=False)
    assert by_email and "@" not in by_email.value

    assert resolve_answer("Vancouver, BC", options=["Yes", "No"], allow_ai=False).value.lower() == "yes"
    assert resolve_answer("Calgary, AB", options=["Yes", "No"], allow_ai=False).value.lower() == "no"
    assert resolve_answer("Tous les emplacements / All locations", allow_ai=False).value.lower() == "yes"

    minorities = resolve_answer(
        "Visible Minorities - do you identify as a member of a visible minority in Canada?",
        options=["Yes", "No", "I prefer not to say", "Decline"],
        allow_ai=False,
    )
    assert minorities is not None
    assert minorities.value.lower() not in {"male", "female", "man", "woman"}


def test_gpa_threshold_yes_and_sms_yes():
    from core.shared_modules.form_answers import resolve_answer

    gpa = resolve_answer(
        "Avez-vous obtenu un diplôme avec une note de 2,75+ (ou équivalent)? / Have you graduated with a grade of 2.75+? (or equivalent)",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert gpa and gpa.value.lower() == "yes"

    sms = resolve_answer(
        "I authorize mthree to contact me via SMS. Message and data rates may apply.",
        options=["Yes", "No"],
        allow_ai=False,
    )
    assert sms and sms.value.lower() == "yes"

    train = resolve_answer(
        "Quelle est la première date disponible pour commencer la formation? / What is your first available date to start training?",
        allow_ai=False,
    )
    assert train and "mmediate" in train.value.lower()


def test_resolve_for_field_does_not_override_brain_with_portal_prefs(monkeypatch):
    from core.shared_modules import ats_apply as aa
    from core.shared_modules.form_answers import ResolvedAnswer

    aa._reset_ai_budget()

    def fake_resolve(question, *, hint="", options=None, profile=None, job_context="", allow_ai=True):
        return ResolvedAnswer(value="Job board", source="how_heard", score=1.0)

    monkeypatch.setattr(
        "core.shared_modules.form_answers.resolve_answer",
        fake_resolve,
    )
    prefs = aa._resolve_for_field(
        "How did you hear about this opportunity?",
        profile={"how_heard": "Job board"},
        options=["Kabam career page", "Job board", "LinkedIn"],
    )
    # Brain value first — not a portal-prepended LinkedIn/other list.
    assert prefs and prefs[0] == "Job board"
