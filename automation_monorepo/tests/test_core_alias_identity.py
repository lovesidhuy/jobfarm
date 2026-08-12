"""Phase 2 gate: legacy ``core.*`` imports resolve to the canonical
``jobbots.core.*`` modules with **object identity** — one module object, one
module state, regardless of which import path is used.

No browser, no network, no Mongo, no AI.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"

# Representative slice of the moved core: top-level modules, every
# subpackage, and the frozen Q&A chain. All are imported by the existing
# passing test suite, so they are import-safe in CI/local venvs.
_MOVED_MODULES = [
    "core",
    "core.job_queue",
    "core.session_registry",
    "core.supervised_bots",
    "core.secret_manager",
    "core.history_store",
    "core.event_log",
    "core.heartbeat",
    "core.jobbank_email",
    "core.llm_backend.answer_policy",
    "core.llm_backend.fallback",
    "core.shared_modules.form_answers",
    "core.shared_modules.company_throttle",
    "core.discovery.planner",
    "core.discovery.normalizer",
    "core.discovery.deduplicator",
    "core.ats.engine",
    "core.ats.registry",
    "core.observability.langfuse_tracing",
    "core.training_capture",
]


def _import_both(name: str):
    canonical = "jobbots." + name
    return importlib.import_module(name), importlib.import_module(canonical)


def test_legacy_import_is_canonical_import():
    for legacy in _MOVED_MODULES:
        old, new = _import_both(legacy)
        assert old is new, f"{legacy} is not identical to jobbots.{legacy}"


def test_no_duplicate_module_state():
    """A module imported under both names must share one sys.modules entry."""
    for legacy in _MOVED_MODULES[1:]:
        canonical = "jobbots." + legacy
        _import_both(legacy)
        assert sys.modules[legacy] is sys.modules[canonical]


def test_frozen_qa_function_identity_across_paths():
    from core.shared_modules.form_answers import resolve_answer as old_resolve
    from jobbots.core.shared_modules.form_answers import resolve_answer as new_resolve
    from core.llm_backend.answer_policy import classify as old_classify
    from jobbots.core.llm_backend.answer_policy import classify as new_classify

    assert old_resolve is new_resolve
    assert old_classify is new_classify


def test_shim_package_is_the_only_legacy_core_artifact():
    """automation_monorepo/core must contain only the shim (+ runtime logs dir).

    The logs/ dir holds untracked runtime output, so it may be absent on a
    fresh checkout (e.g. the VM) — only __init__.py is mandatory.
    """
    core_dir = _MONOREPO / "core"
    entries = {p.name for p in core_dir.iterdir() if not p.name.startswith("__pycache__")}
    assert "__init__.py" in entries, "alias shim missing"
    assert entries <= {"__init__.py", "logs"}, f"unexpected legacy core entries: {sorted(entries)}"



def test_canonical_tree_has_no_legacy_core_imports():
    """Nothing inside jobbots/core may import via the legacy ``core.`` path."""
    import re

    pattern = re.compile(r"^\s*(from|import)\s+core(\.|\s|$)", re.M)
    offenders = []
    for path in (_REPO / "jobbots" / "core").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert not offenders, f"legacy core.* imports inside canonical tree: {offenders}"
