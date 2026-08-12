"""Thin delegate over ``automation_monorepo/orchestrator.py`` and ``supervisor.py``.

Phase 1 runs the legacy scripts as subprocesses with the repository's virtualenv
python so behavior (dotenv merge, env passthrough, Telegram alerts, phase
order) is *bit-identical* to running them directly. Later phases replace the
subprocess hop with in-process calls behind the same functions.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from jobbots.paths import MONOREPO_ROOT, REPO_ROOT, ensure_monorepo_on_path


def bot_python() -> Path:
    """Resolve the python that runs bots (prefers repo .venv), no side effects."""
    ensure_monorepo_on_path()
    from core.supervisor_runtime import resolve_bot_python

    return Path(resolve_bot_python(MONOREPO_ROOT))


def run_orchestrator_stage(
    stage: str,
    *,
    workers: int = 1,
    once: bool = False,
    auto: bool = False,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run ``orchestrator.py --stage <stage>``; returns the exit code."""
    cmd = [str(bot_python()), str(MONOREPO_ROOT / "orchestrator.py"), "--stage", stage]
    if auto:
        cmd.append("--auto")
    if once:
        cmd.append("--once")
    if stage == "apply" and workers != 1:
        cmd += ["--workers", str(workers)]
    cmd += list(extra_args or [])
    proc = subprocess.run(cmd, cwd=str(MONOREPO_ROOT), env=env or os.environ.copy())
    return proc.returncode


def run_supervisor(
    stage: str,
    *,
    portal: str | None = None,
    workers: int = 1,
    once: bool = False,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run ``supervisor.py --stage <stage>``; returns the exit code."""
    cmd = [str(bot_python()), str(MONOREPO_ROOT / "supervisor.py"), "--stage", stage]
    if portal:
        cmd += ["--portal", portal]
    if stage == "apply" and workers != 1:
        cmd += ["--workers", str(workers)]
    if once:
        cmd.append("--once")
    cmd += list(extra_args or [])
    proc = subprocess.run(cmd, cwd=str(MONOREPO_ROOT), env=env or os.environ.copy())
    return proc.returncode


def run_onboard(extra_args: list[str] | None = None) -> int:
    """Interactive first-time login setup (wraps ``onboard.py``)."""
    cmd = [str(bot_python()), str(MONOREPO_ROOT / "onboard.py")]
    cmd += list(extra_args or [])
    proc = subprocess.run(cmd, cwd=str(MONOREPO_ROOT))
    return proc.returncode
