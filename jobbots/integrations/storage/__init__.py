"""Storage integration — lazy facade over the canonical persistence modules.

Mongo application queue, applied-history store, session registry, and the
event log exist once in ``jobbots.core``.
"""
from __future__ import annotations

import importlib
from typing import Any

_MODULES = ("job_queue", "history_store", "session_registry", "event_log")

__all__ = list(_MODULES)


def __getattr__(name: str) -> Any:
    if name in _MODULES:
        return importlib.import_module(f"jobbots.core.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
