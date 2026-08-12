"""Indeed job applier package — split from indeed_bot.py."""
from __future__ import annotations

import importlib

_SUBMODULES = (
    "ai",
    "persistence",
    "session",
    "search",
    "gates",
    "navigation",
    "form_steps",
    "questions",
    "smartapply",
    "apply",
    "loop",
)

from ._bootstrap import *  # noqa: F401,F403

_submods = [importlib.import_module(f".{n}", package=__name__) for n in _SUBMODULES]

_merged: dict = {}
from . import _bootstrap as _bootstrap_mod
_merged.update(_bootstrap_mod.__dict__)
for _m in _submods:
    for _k, _v in _m.__dict__.items():
        if _k == "__builtins__":
            continue
        _merged[_k] = _v

for _m in (_bootstrap_mod, *_submods):
    _m.__dict__.update(_merged)

for _k, _v in _merged.items():
    if _k.startswith("__") and _k not in ("__doc__",):
        continue
    globals()[_k] = _v
