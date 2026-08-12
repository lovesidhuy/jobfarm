"""Browser integration — lazy facade over ``jobbots.core.browser``.

NST Browser profile lifecycle, leasing, safety, and Chrome/CDP launch exist
once in the canonical core; this facade gives the integrations layer a stable
import path without importing Selenium/Playwright at facade-import time.
"""
from __future__ import annotations

import importlib
from typing import Any

_MODULES = (
    "open_chrome",
    "profile_lease",
    "nst_profile_safety",
    "nst_accounts",
    "clickers_and_finders",
)

__all__ = list(_MODULES)


def __getattr__(name: str) -> Any:
    if name in _MODULES:
        return importlib.import_module(f"jobbots.core.browser.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
