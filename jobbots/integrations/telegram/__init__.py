"""Telegram integration — lazy facade over ``jobbots.core.alerts``.

``send_alert`` is the exact ``send_telegram_alert`` function object from the
canonical core (identity, not a wrapper).
"""
from __future__ import annotations

from typing import Any

__all__ = ["send_alert"]


def __getattr__(name: str) -> Any:
    if name == "send_alert":
        from jobbots.core.alerts import send_telegram_alert

        return send_telegram_alert
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
