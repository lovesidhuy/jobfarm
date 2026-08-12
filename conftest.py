"""Repo-root pytest bootstrap.

Makes both import roots available no matter where pytest is invoked from:
  - repo root            (for the ``jobbots`` package)
  - automation_monorepo  (for the legacy ``core`` / ``config`` / ``bots`` trees)

Additive only — does not change any test or runtime behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# automation_monorepo first so its ``core``/``config``/``scripts`` packages win
# (mirrors CI's PYTHONPATH=automation_monorepo); repo root second for ``jobbots``.
for _p in (_ROOT / "automation_monorepo", _ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import importlib.util

class _ModulesAliasFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "modules" or fullname.startswith("modules."):
            canonical = "jobbots.core.shared_modules" + fullname[7:]
            try:
                return importlib.util.find_spec(canonical)
            except Exception:
                return None
        return None

sys.meta_path.insert(0, _ModulesAliasFinder())
