"""CAPTCHA Handler Module — Delegation shim to submodules."""

from __future__ import annotations

from jobbots.core.evasion._config import *  # noqa: F403,F401
from jobbots.core.evasion._focus import *  # noqa: F403,F401
from jobbots.core.evasion._capmonster import *  # noqa: F403,F401
from jobbots.core.evasion._capsolver import *  # noqa: F403,F401
from jobbots.core.evasion._detection import *  # noqa: F403,F401
from jobbots.core.evasion._handlers import *  # noqa: F403,F401

# Re-export underscored private helper for master tree compatibility shims
from jobbots.core.evasion._detection import _is_page_alive  # noqa: F401
