"""jobbots.core — the canonical shared core.

Queueing, retries, AI calls, Telegram, logging, browser lifecycle, discovery,
screening, portal shared modules, and persistence live here exactly once
(moved from ``automation_monorepo/core`` in Phase 2, git history preserved).
The legacy ``core.*`` import path keeps working through the alias shim at
``automation_monorepo/core/__init__.py`` — ``core.X is jobbots.core.X``.
"""

