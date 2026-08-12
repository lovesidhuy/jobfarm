"""jobbots.core.qa — stable import facade over the FROZEN Q&A system.

The question-answering implementation is **not** moved or modified in any
refactor phase without separate approval. This module only re-exports the
existing callables so callers have one canonical import path:

    from jobbots.core.qa import resolve_answer, resolve_text, resolve_choice
    from jobbots.core.qa import policy_classify, PolicyValues, Decision

Answer precedence (unchanged, enforced by form_answers.resolve_answer):
  1. hard policy (identity / eligibility locks — never AI-overridden)
  2. deterministic profile / safe rules
  3. curated QA answer bank (per-profile JSON, selected via JOB_PROFILE/BOT_NAME)
  4. AI fallback with full profile dossier
"""
from __future__ import annotations

from jobbots.paths import ensure_monorepo_on_path

# The frozen Q&A modules live canonically in jobbots.core.* since Phase 2.
# ensure_monorepo_on_path() stays: form_answers lazily imports ``config.*``
# profile modules and the master-tree QA answer bank at call time.
ensure_monorepo_on_path()

from jobbots.core.llm_backend.answer_policy import (  # noqa: E402,F401
    Decision,
    PolicyValues,
    classify as policy_classify,
    map_intent_to_option,
)
from jobbots.core.shared_modules.form_answers import (  # noqa: E402,F401
    ResolvedAnswer,
    load_profile as load_qa_profile,
    resolve_answer,
    resolve_choice,
    resolve_text,
)


__all__ = [
    "Decision",
    "PolicyValues",
    "ResolvedAnswer",
    "map_intent_to_option",
    "policy_classify",
    "load_qa_profile",
    "resolve_answer",
    "resolve_choice",
    "resolve_text",
]
