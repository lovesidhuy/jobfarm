"""Golden-fixture runner for the FROZEN Q&A system.

Used by:
  - ``automation_monorepo/scripts/capture_qa_golden.py``  (writes fixtures)
  - ``automation_monorepo/tests/test_qa_golden.py``       (replays fixtures)
  - ``jobbots qa check``                                  (shadow-mode harness)

Rules enforced here:
  * AI is always disabled (deterministic layers 1-3 + deterministic fallbacks
    only). LLM answers are non-reproducible and therefore not goldens.
  * The runner never mutates the real environment — JOB_PROFILE / BOT_NAME /
    FORM_ANSWERS_DISABLE_AI are swapped per case and restored afterwards.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jobbots.paths import MONOREPO_ROOT, ensure_monorepo_on_path

FIXTURES_DIR = MONOREPO_ROOT / "tests" / "fixtures" / "qa"
FIXTURE_FILES = ("it_questions.json", "general_questions.json", "edge_cases.json")

_ENV_KEYS = ("JOB_PROFILE", "BOT_NAME", "FORM_ANSWERS_DISABLE_AI")


def load_fixtures(fixtures_dir: Path | None = None) -> list[dict[str, Any]]:
    base = fixtures_dir or FIXTURES_DIR
    cases: list[dict[str, Any]] = []
    for fname in FIXTURE_FILES:
        path = base / fname
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case in payload.get("cases", []):
            case = dict(case)
            case["_fixture"] = fname
            cases.append(case)
    return cases


def run_case(case: dict[str, Any], *, fixtures_dir: Path | None = None) -> dict[str, Any]:
    """Run one fixture through the frozen deterministic Q&A chain.

    Returns a result dict with the same shape the fixture's ``expected``
    block uses, plus a ``warning`` field when the question is only answerable
    by the AI layer (expected value None with ai_allowed=True).
    """
    ensure_monorepo_on_path()

    profile = str(case.get("profile") or "").strip().upper()
    if profile not in {"IT", "GENERAL"}:
        raise ValueError(f"case {case.get('id')!r}: profile must be IT or GENERAL")

    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        os.environ["JOB_PROFILE"] = profile.title() if profile == "GENERAL" else "IT"
        os.environ["BOT_NAME"] = "indeed_it" if profile == "IT" else "indeed_general"
        os.environ["FORM_ANSWERS_DISABLE_AI"] = "1"

        from jobbots.core.llm_backend.answer_policy import classify
        from jobbots.core.shared_modules import form_answers


        # The QA bank caches per-process; force a reload keyed to this profile.
        form_answers._ensure_master_modules_on_path()
        try:
            from modules import qa_answer_bank  # type: ignore

            qa_answer_bank._load_answer_bank.cache_clear()
        except Exception:
            pass

        question = str(case.get("question") or "")
        hint = str(case.get("hint") or "")
        options = case.get("options") or None
        job_context = str(case.get("job_context") or "")

        ans = form_answers.resolve_answer(
            question,
            hint=hint,
            options=options,
            job_context=job_context,
            allow_ai=False,
        )
        decision = classify(question, options=options)

        result: dict[str, Any] = {
            "value": (ans.value if ans else None),
            "source": (ans.source if ans else None),
            "category": decision.category,
            "intent": decision.intent,
            "ai_allowed": bool(decision.ai_allowed),
        }
        if result["value"] is None and result["ai_allowed"]:
            result["warning"] = (
                "unanswered by deterministic layers; production would call the "
                "AI fallback (disabled in golden runs)"
            )
        return result
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def replay(fixtures_dir: Path | None = None) -> list[dict[str, Any]]:
    """Replay every fixture and return per-case comparison reports."""
    reports: list[dict[str, Any]] = []
    for case in load_fixtures(fixtures_dir):
        actual = run_case(case, fixtures_dir=fixtures_dir)
        expected = case.get("expected") or {}
        mismatches = {
            field: {"expected": expected.get(field), "actual": actual.get(field)}
            for field in ("value", "source", "category", "intent", "ai_allowed")
            if field in expected and expected.get(field) != actual.get(field)
        }
        reports.append(
            {
                "id": case.get("id"),
                "fixture": case.get("_fixture"),
                "profile": case.get("profile"),
                "ok": not mismatches,
                "mismatches": mismatches,
                "warning": actual.get("warning"),
            }
        )
    return reports


def main() -> int:
    reports = replay()
    failed = [r for r in reports if not r["ok"]]
    warned = [r for r in reports if r.get("warning")]
    for r in failed:
        print(f"FAIL {r['fixture']}:{r['id']} [{r['profile']}]")
        for field, diff in r["mismatches"].items():
            print(f"  {field}: expected={diff['expected']!r} actual={diff['actual']!r}")
    print(
        f"\n{len(reports)} golden cases: {len(reports) - len(failed)} passed, "
        f"{len(failed)} failed, {len(warned)} ai-only (expected None)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
