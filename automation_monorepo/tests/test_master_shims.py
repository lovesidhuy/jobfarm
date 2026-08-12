"""Compile-check master-tree shims and monorepo bridge modules (no browser deps)."""
from __future__ import annotations

import py_compile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MASTER = _REPO / "master"

_SHIM_PATHS = [
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "captcha_handler.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "open_chrome.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "_monorepo_bridge.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "indeed_bot.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "glassdoor_bot.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "Auto_job_applier_glassdoor" / "modules" / "captcha_handler.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "Auto_job_applier_glassdoor" / "modules" / "indeed_bot.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "Auto_job_applier_glassdoor" / "modules" / "glassdoor_bot.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "modules" / "captcha_handler.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "modules" / "open_chrome.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "modules" / "glassdoor_bot.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "Auto_job_applier_glassdoor" / "modules" / "captcha_handler.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "Auto_job_applier_glassdoor" / "modules" / "indeed_bot.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "Auto_job_applier_glassdoor" / "modules" / "glassdoor_bot.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "workopolis_bot.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "modules" / "workopolis_bot.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "modules" / "indeed_bot.py",
    _MASTER / "gen_indeed" / "Auto_indeed" / "config" / "secret_manager.py",
    _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "config" / "secret_manager.py",
]


import pytest

@pytest.mark.skipif(not _MASTER.exists(), reason="master/ tree retired in open-source release")
def test_master_shim_files_compile():
    missing = [p for p in _SHIM_PATHS if not p.is_file()]
    assert not missing, f"Missing shim paths: {missing}"
    for path in _SHIM_PATHS:
        py_compile.compile(str(path), doraise=True)


@pytest.mark.skipif(not _MASTER.exists(), reason="master/ tree retired in open-source release")
def test_monorepo_bridge_resolves():
    import importlib.util

    bridge = _MASTER / "it_indeed cwgeopy" / "Auto_indeed" / "modules" / "_monorepo_bridge.py"
    spec = importlib.util.spec_from_file_location("test_bridge", bridge)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    root = mod.monorepo_root()
    assert (root / "supervisor.py").is_file()
