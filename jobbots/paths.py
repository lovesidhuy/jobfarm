"""Repository path resolution for the jobbots package.

Single place that knows where the legacy (current-production) trees live.
Later refactor phases update these constants as code moves behind stable
interfaces; everything else in the package imports from here.
"""
from __future__ import annotations

import sys
from pathlib import Path

# jobbots/paths.py -> jobbots/ -> <repo root>
REPO_ROOT = Path(__file__).resolve().parent.parent
MONOREPO_ROOT = REPO_ROOT / "automation_monorepo"
MASTER_ROOT = REPO_ROOT / "data"
PROFILES_ROOT = REPO_ROOT / "profiles"
MASTER_IT = REPO_ROOT / "data" / "qa_banks"
MASTER_GENERAL = REPO_ROOT / "data" / "qa_banks"
QA_BANKS_ROOT = REPO_ROOT / "data" / "qa_banks"




def ensure_monorepo_on_path() -> Path:
    """Put automation_monorepo on sys.path so ``core.*`` / ``config.*`` import.

    Idempotent. Does not modify anything outside sys.path.
    """
    s = str(MONOREPO_ROOT)
    if s not in sys.path:
        sys.path.insert(0, s)
    return MONOREPO_ROOT


def dotenv_path() -> Path:
    return MONOREPO_ROOT / ".env"
