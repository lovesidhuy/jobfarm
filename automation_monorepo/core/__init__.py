"""Compatibility shim: the canonical shared core now lives in ``jobbots.core``.

Phase 2 of the refactor moved ``automation_monorepo/core/*`` to
``jobbots/core/*`` (git history preserved). This shim keeps every legacy
import path working with **module-object identity**:

    import core.job_queue                      # -> jobbots.core.job_queue
    from core.shared_modules import form_answers
    core.shared_modules.form_answers is jobbots.core.shared_modules.form_answers  # True

How it works: a meta-path finder intercepts any ``core[.X]`` import and
returns the canonically-imported ``jobbots.core[.X]`` module object, so
module state exists exactly once (no duplicate-execution split state).
``import core`` itself is rebound to ``jobbots.core`` at the bottom of this
module, so ``core is jobbots.core`` also holds.

Requirements: the repo root must be importable for ``jobbots`` — this shim
inserts it from ``__file__`` if missing, so legacy runtimes that only put
``automation_monorepo`` on ``sys.path`` (CI, master-bot bridges) keep working.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_OLD = "core"
_NEW = "jobbots.core"


class _CoreAliasLoader(importlib.abc.Loader):
    """Returns the canonical ``jobbots.core.X`` module for ``core.X``."""

    def create_module(self, spec):
        canonical_name = _NEW + spec.name[len(_OLD):]
        module = importlib.import_module(canonical_name)
        sys.modules[spec.name] = module  # register alias -> identical object
        return module

    def exec_module(self, module):  # canonical module is already executed
        return None


class _CoreAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == _OLD or fullname.startswith(_OLD + "."):
            return importlib.util.spec_from_loader(fullname, _CoreAliasLoader())
        return None


def _install_finder() -> None:
    for finder in sys.meta_path:
        if isinstance(finder, _CoreAliasFinder):
            return
    sys.meta_path.insert(0, _CoreAliasFinder())


_install_finder()

# Rebind ``core`` itself to the canonical package so ``import core`` is
# ``import jobbots.core`` (the import system returns sys.modules["core"]).
sys.modules[_OLD] = importlib.import_module(_NEW)
