"""Golden regression tests for the FROZEN Q&A system.

Replays automation_monorepo/tests/fixtures/qa/*.json through the live
deterministic Q&A chain (hard policy → safe rules → curated QA bank, AI
disabled) for both the IT and General profiles. Any refactor that changes an
answer, an answer source, a policy classification, or the unknown-question
contract fails here.

Fixtures were captured from the production code by
``scripts/capture_qa_golden.py``. Do not edit expected values by hand —
re-baselining is a Q&A behavior change and needs explicit review.

Must pass without browser, NST, network, or AI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["FORM_ANSWERS_DISABLE_AI"] = "1"

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from jobbots.core.qa.runner import load_fixtures, run_case  # noqa: E402

_CASES = load_fixtures()
_FIELDS = ("value", "source", "category", "intent", "ai_allowed")


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_golden_case(case):
    actual = run_case(case)
    expected = case["expected"]
    for field in _FIELDS:
        if field not in expected:
            continue
        assert actual[field] == expected[field], (
            f"[{case['_fixture']}:{case['id']}] {field}: "
            f"expected {expected[field]!r}, got {actual[field]!r}"
        )


def test_fixtures_cover_both_profiles():
    profiles = {str(c.get("profile", "")).upper() for c in _CASES}
    assert "IT" in profiles and "GENERAL" in profiles


def test_fixtures_cover_critical_categories():
    """Guard rail: the golden set must keep covering these behaviors."""
    ids = {c["id"] for c in _CASES}
    for needle in ("auth_ca", "sponsorship", "gender", "years", "salary"):
        assert any(needle in i for i in ids), f"no golden case covers {needle!r}"


def test_unknown_question_stays_unanswered_without_ai():
    """Deterministic layers must never invent answers to unknown questions."""
    unknown = [c for c in _CASES if c["id"] == "edge_unknown_ai_only"]
    assert unknown, "edge_unknown_ai_only fixture missing"
    actual = run_case(unknown[0])
    assert actual["value"] is None
    assert actual["ai_allowed"] is True  # production escalates to AI fallback
