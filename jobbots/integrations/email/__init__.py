"""Email integration — lazy facade over the canonical email modules.

``imap_reader`` (applied-confirmation inbox parsing). Job Bank email apply is
retired; its old module remains available only for historical inspection.
(Job Bank application emails + screening answers) live once in the core.
"""
from __future__ import annotations

import importlib
from typing import Any

_MODULES = ("imap_reader",)

__all__ = list(_MODULES)


def __getattr__(name: str) -> Any:
    if name in _MODULES:
        return importlib.import_module(f"jobbots.core.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
