"""Farm productivity contract — offline, free, must pass in CI.

Guards the topology that makes the ephemeral (build → work → destroy) farm
productive after the jobbots refactor. Complements ``test_cf_heavy_proxy.py``
and ``jobbots farm-check``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"
for _p in (str(_REPO), str(_MONOREPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_farm_check_offline_ok():
    from jobbots.app.farm_check import run_farm_check

    report = run_farm_check(live=False)
    assert report["ok"], [f["name"] + ": " + str(f.get("detail")) for f in report["failures"]]
    assert report["failed"] == 0
    assert report["total"] >= 25


def test_supervised_topology_indeed_and_linkedin():
    from jobbots.core.supervised_bots import supervised_bot_configs

    enabled = {c["bot_name"]: c for c in supervised_bot_configs(include_disabled=False)}
    all_bots = {c["bot_name"]: c for c in supervised_bot_configs(include_disabled=True)}

    assert "indeed_it" in enabled and "indeed_general" in enabled
    assert enabled["indeed_it"]["job_profile"].upper() == "IT"
    assert enabled["indeed_general"]["job_profile"].title() == "General"
    assert enabled["linkedin_general"]["enabled"] is True
    assert all_bots["linkedin_it"].get("enabled", True) is False
    assert all_bots["glassdoor_general"].get("enabled", True) is False
    assert all_bots["workopolis_general"].get("enabled", True) is False
    assert "google_it" in enabled  # ATS Playwright — no NST
    assert enabled["jobbank_it"]["portal"] == "jobbank"


def test_nst_required_bots_exclude_ats_playwright():
    from jobbots.core.browser.nst_accounts import REQUIRED_BOTS
    from jobbots.app.farm_check import _NST_BROWSER_BOTS

    assert "google_it" not in REQUIRED_BOTS
    assert "google_it" not in _NST_BROWSER_BOTS
    for bot in ("indeed_it", "indeed_general", "glassdoor_it", "workopolis_it", "linkedin_general", "jobbank_it"):
        assert bot in REQUIRED_BOTS
        assert bot in _NST_BROWSER_BOTS


def test_jobbank_direct_apply_official_in_overrides():
    text = (_REPO / "packer/linux/runtime-prod-overrides.conf").read_text(encoding="utf-8")
    apply_line = next(ln for ln in text.splitlines() if ln.startswith("JOBBOTS_APPLY_PORTALS="))
    assert "jobbank" in apply_line
    assert "JOBBOTS_JOBBANK_EMAIL_APPLY_RETIRED=1" in text
    assert "JOBBANK_DIRECT_APPLY_ENABLED=1" in text
    assert re.search(r"^NSTBROWSER_ACTIVE_SLOT=[12]\s*$", text, re.M)


def test_glassdoor_discover_covers_workopolis():
    bin_path = _REPO / "packer/linux/bin/jobbots-discover-glassdoor-it"
    text = bin_path.read_text(encoding="utf-8")
    assert "--portals glassdoor,workopolis" in text


def test_proxy_lane_design_offline():
    from jobbots.app.farm_check import check_proxy_lane_design

    results = check_proxy_lane_design()
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed


def test_discovery_ladder_offline():
    from jobbots.app.farm_check import check_discovery_ladder_offline

    results = check_discovery_ladder_offline()
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed


def test_cli_farm_check_registered():
    from jobbots.app.cli import build_parser

    p = build_parser()
    args = p.parse_args(["farm-check"])
    assert args.command == "farm-check"
    assert args.live is False
