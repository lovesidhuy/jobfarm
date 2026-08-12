"""Entry-point smoke tests: every runnable surface must compile and import.

Catches refactors that break an entry point's import path before production
does. No browser, no network, no Mongo, no AI.
"""
from __future__ import annotations

import py_compile
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"

_ENTRYPOINTS = [
    _MONOREPO / "orchestrator.py",
    _MONOREPO / "supervisor.py",
    _MONOREPO / "onboard.py",
    _MONOREPO / "telegram_bot.py",
    _MONOREPO / "bot_manager.py",
    *_MONOREPO.glob("bots/*.py"),
]

_JOBOTS_MODULES = [
    "jobbots",
    "jobbots.paths",
    "jobbots.app.cli",
    "jobbots.app.orchestrator",
    "jobbots.app.pipeline",
    "jobbots.core.qa",
    "jobbots.core.qa.runner",
    "jobbots.core.profiles",
    "jobbots.integrations.portals.base",
]


def test_legacy_entrypoints_compile():
    missing = [str(p) for p in _ENTRYPOINTS if not p.is_file()]
    assert not missing, f"missing entry points: {missing}"
    for path in _ENTRYPOINTS:
        py_compile.compile(str(path), doraise=True)


def test_jobbots_modules_compile():
    for path in (_REPO / "jobbots").rglob("*.py"):
        py_compile.compile(str(path), doraise=True)


def test_jobbots_modules_import():
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    import importlib

    for name in _JOBOTS_MODULES:
        importlib.import_module(name)


def test_qa_facade_exports_frozen_callables():
    """The facade must expose the exact same function objects as the frozen modules."""
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    if str(_MONOREPO) not in sys.path:
        sys.path.insert(0, str(_MONOREPO))

    import jobbots.core.qa as qa
    from core.llm_backend import answer_policy
    from core.shared_modules import form_answers

    assert qa.resolve_answer is form_answers.resolve_answer
    assert qa.resolve_text is form_answers.resolve_text
    assert qa.resolve_choice is form_answers.resolve_choice
    assert qa.policy_classify is answer_policy.classify
    assert qa.PolicyValues is answer_policy.PolicyValues


def test_profile_manifests_load_and_validate():
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))

    from jobbots.core.profiles import available_profiles, load_profile, profile_env

    names = available_profiles()
    assert "it" in names and "general" in names

    it = load_profile("it")
    assert it.job_profile == "IT"
    assert it.bot_name == "indeed_it"
    assert not it.validate(), f"IT profile manifest problems: {it.validate()}"

    gen = load_profile("general")
    assert gen.job_profile == "General"
    assert not gen.validate(), f"general profile manifest problems: {gen.validate()}"

    env = profile_env(it, base_env={})
    assert env["JOB_PROFILE"] == "IT"
    assert env["BOT_NAME"] == "indeed_it"


def test_cli_help_and_version(capsys):
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))

    from jobbots.app.cli import main

    assert main(["--version"]) == 0
    assert main([]) == 2  # no command → help
    out = capsys.readouterr().out
    for cmd in ("doctor", "onboard", "discover", "apply", "run", "status", "export", "qa"):
        assert cmd in out
