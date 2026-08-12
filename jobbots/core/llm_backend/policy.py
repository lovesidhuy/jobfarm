"""
Bot-policy decision helpers driven by `config/bot.yaml`.

Centralizes the gate / save / glassdoor branching so each bot's runtime stays
identical and only the YAML differs. Pure functions — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import BotConfig


@dataclass(frozen=True)
class GateOutcome:
    verdict: str                    # "worth_applying" | "skip" | "unknown"
    score: Optional[float] = None
    reasoning: str = ""

    @property
    def worth(self) -> bool:
        return self.verdict == "worth_applying"


def should_apply(cfg: BotConfig, gate: Optional[GateOutcome]) -> bool:
    """Apply iff gate is disabled OR gate verdict says worth applying."""
    if not cfg.gate_enabled:
        return True
    return bool(gate and gate.worth)


def should_save(cfg: BotConfig, gate: Optional[GateOutcome], *, mode: str) -> bool:
    """`mode`: "easy" | "external".

    Save policy table:
                       gate_disabled        gate_worth        gate_skip
    on_external=True   save_on_external     save_on_external  False
    on_external=False  False                False             False
    on_easy_apply same logic vs mode=easy.
    """
    if mode == "external":
        if not cfg.save_on_external:
            return False
    elif mode == "easy":
        if not cfg.save_on_easy_apply:
            return False
    else:
        return False
    if not cfg.gate_enabled:
        return True
    return bool(gate and gate.worth)


def glassdoor_enabled(cfg: BotConfig) -> bool:
    return cfg.glassdoor_enabled
