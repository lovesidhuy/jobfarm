#!/usr/bin/env python3
"""Capture golden Q&A fixtures from the CURRENT (frozen) Q&A system.

Writes automation_monorepo/tests/fixtures/qa/{it,general}_questions.json and
edge_cases.json. The question set is curated below; expected outputs are
produced by running the live deterministic chain (hard policy → safe rules →
curated QA bank) with AI disabled. Once written, the fixtures are LOCKED —
``tests/test_qa_golden.py`` and ``jobbots qa check`` fail on any drift.

Re-run this script only when intentionally re-baselining (which requires
reviewing the diff like a Q&A behavior change).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from jobbots.core.qa.runner import FIXTURES_DIR, run_case  # noqa: E402

# ---------------------------------------------------------------------------
# Curated question set. Fields: id, profile, question, options, hint,
# job_context. Expected outputs are captured, never hand-written here.
# ---------------------------------------------------------------------------

IT_CASES = [
    # --- work authorization / eligibility (hard-locked identity) ---
    {"id": "it_auth_ca", "profile": "IT",
     "question": "Are you legally authorized to work in Canada?",
     "options": ["Yes", "No"]},
    {"id": "it_auth_ca_select", "profile": "IT",
     "question": "Are you authorized to work in Canada?",
     "options": ["Select an option", "Yes", "No"]},
    {"id": "it_sponsorship_ca", "profile": "IT",
     "question": "Will you now or in the future require sponsorship for employment visa status in Canada?",
     "options": ["Yes", "No"]},
    {"id": "it_auth_us", "profile": "IT",
     "question": "Are you authorized to work in the United States?",
     "options": ["Yes", "No"]},
    {"id": "it_sponsorship_us", "profile": "IT",
     "question": "Will you require sponsorship to work in the United States?",
     "options": ["Yes", "No"]},
    # --- demographics (hard-locked declines / identity) ---
    {"id": "it_gender", "profile": "IT",
     "question": "What is your gender?",
     "options": ["Male", "Female", "Decline to self-identify"]},
    {"id": "it_disability", "profile": "IT",
     "question": "Do you have a disability?",
     "options": ["Yes", "No", "I do not wish to answer"]},
    {"id": "it_veteran", "profile": "IT",
     "question": "Are you a veteran?",
     "options": ["Yes", "No", "I decline to answer"]},
    # --- numerics from the IT profile ---
    {"id": "it_years_experience", "profile": "IT",
     "question": "How many years of IT support experience do you have?"},
    {"id": "it_years_aws", "profile": "IT",
     "question": "How many years of experience do you have with AWS?"},
    {"id": "it_desired_salary", "profile": "IT",
     "question": "What is your desired salary?"},
    {"id": "it_current_ctc", "profile": "IT",
     "question": "What is your current CTC?"},
    {"id": "it_notice_period", "profile": "IT",
     "question": "What is your notice period (in days)?"},
    # --- identity / contact facts (safe rules) ---
    {"id": "it_first_name", "profile": "IT", "question": "First name"},
    {"id": "it_last_name", "profile": "IT", "question": "Last name"},
    {"id": "it_email", "profile": "IT", "question": "Email address"},
    {"id": "it_phone", "profile": "IT", "question": "Phone number"},
    {"id": "it_city", "profile": "IT", "question": "What city do you live in?"},
    {"id": "it_linkedin", "profile": "IT", "question": "LinkedIn profile URL"},
    {"id": "it_website", "profile": "IT", "question": "Portfolio website"},
    # --- education ---
    {"id": "it_education_level", "profile": "IT",
     "question": "Highest level of education",
     "options": ["High School", "Bachelor's Degree", "Master's Degree", "Other"]},
    {"id": "it_graduated", "profile": "IT",
     "question": "Have you completed your degree?",
     "options": ["Yes", "No"]},
    # --- skill yes/no screening (capability pattern) ---
    {"id": "it_skill_linux", "profile": "IT",
     "question": "Do you have experience with Linux system administration?",
     "options": ["Yes", "No"]},
    {"id": "it_skill_geology_no", "profile": "IT",
     "question": "Do you have a Bachelor's degree in Geology or a closely related geoscience discipline?",
     "options": ["Select an option", "Yes", "No"]},
]

GENERAL_CASES = [
    {"id": "gen_auth_ca", "profile": "GENERAL",
     "question": "Are you legally authorized to work in Canada?",
     "options": ["Yes", "No"]},
    {"id": "gen_sponsorship_ca", "profile": "GENERAL",
     "question": "Will you now or in the future require sponsorship for employment visa status in Canada?",
     "options": ["Yes", "No"]},
    {"id": "gen_auth_us", "profile": "GENERAL",
     "question": "Are you authorized to work in the United States?",
     "options": ["Yes", "No"]},
    {"id": "gen_gender", "profile": "GENERAL",
     "question": "What is your gender?",
     "options": ["Male", "Female", "Decline to self-identify"]},
    {"id": "gen_years_customer_service", "profile": "GENERAL",
     "question": "How many years of customer service experience do you have?"},
    {"id": "gen_first_name", "profile": "GENERAL", "question": "First name"},
    {"id": "gen_email", "profile": "GENERAL", "question": "Email address"},
    {"id": "gen_city", "profile": "GENERAL", "question": "What city do you live in?"},
    {"id": "gen_education_level", "profile": "GENERAL",
     "question": "Highest level of education",
     "options": ["High School", "Bachelor's Degree", "Master's Degree", "Other"]},
    {"id": "gen_skill_crm", "profile": "GENERAL",
     "question": "Do you have experience with CRM software?",
     "options": ["Yes", "No"]},
]

EDGE_CASES = [
    # Salary conversions documented in config/*/questions.py
    {"id": "edge_ctc_lakhs", "profile": "IT",
     "question": "What is your current CTC in lakhs per annum?"},
    {"id": "edge_salary_monthly", "profile": "IT",
     "question": "What is your expected salary per month?"},
    {"id": "edge_notice_months", "profile": "IT",
     "question": "What is your notice period in months?"},
    {"id": "edge_notice_weeks", "profile": "IT",
     "question": "What is your notice period in weeks?"},
    # Yes/No mapping onto non-standard option labels
    {"id": "edge_auth_ca_french_options", "profile": "IT",
     "question": "Are you legally authorized to work in Canada?",
     "options": ["Oui", "Non"]},
    # Placeholder option must never be selected
    {"id": "edge_select_placeholder", "profile": "IT",
     "question": "Are you authorized to work in Canada?",
     "options": ["Select an option", "Yes", "No"]},
    # Unknown / AI-only question: deterministic layers must NOT invent an
    # answer; value is expected to be None (production calls AI fallback).
    {"id": "edge_unknown_ai_only", "profile": "IT",
     "question": "Describe a time you resolved a conflict with a difficult stakeholder."},
    # Empty question — must not crash, must not invent
    {"id": "edge_empty_question", "profile": "IT", "question": ""},
    # Gender identity lock: bank must never answer Female for this profile
    {"id": "edge_gender_female_only_options", "profile": "IT",
     "question": "What is your gender?",
     "options": ["Female", "Transgender female"]},
]


def capture(cases: list[dict], path: Path) -> None:
    out = []
    for case in cases:
        result = run_case(case)
        entry = dict(case)
        entry["expected"] = {k: v for k, v in result.items() if k != "warning"}
        if "warning" in result:
            entry["note"] = result["warning"]
        out.append(entry)
        print(f"  {case['id']}: value={result['value']!r} source={result['source']!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cases": out}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(out)} cases)")


def main() -> int:
    print("== IT profile ==")
    capture(IT_CASES, FIXTURES_DIR / "it_questions.json")
    print("== General profile ==")
    capture(GENERAL_CASES, FIXTURES_DIR / "general_questions.json")
    print("== Edge cases ==")
    capture(EDGE_CASES, FIXTURES_DIR / "edge_cases.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
