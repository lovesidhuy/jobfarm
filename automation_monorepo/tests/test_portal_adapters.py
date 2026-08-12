"""Phase 3 gate: portal adapters, integration facades, and the shadow harness.

Proves the new integrations layer routes to the *same* production code —
delegation identity, not reimplementation. No browser, no network, no Mongo,
no AI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"
for _p in (str(_REPO), str(_MONOREPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jobbots.integrations.portals import (  # noqa: E402
    ATS_PORTALS,
    BROWSER_PORTALS,
    PORTAL_ADAPTERS,
    PortalAdapter,
    available_portals,
    get_adapter,
    profile_portals,
)
from jobbots.integrations.portals.base import JobLead  # noqa: E402

_EXPECTED_PORTALS = {
    "indeed", "glassdoor", "workopolis", "linkedin", "jobbank",
    "greenhouse", "ashby", "lever", "bamboohr",
}


def _lead(**over) -> JobLead:
    base = {
        "portal": "indeed",
        "source_job_id": "jk-shadow",
        "title": "IT Support Specialist",
        "company": "Acme",
        "url": "https://ca.indeed.com/viewjob?jk=jk-shadow",
        "location": "Vancouver, BC",
        "description": "Help desk and desktop support.",
        "profile": "it",
        "metadata": {"apply_type": "EASY_APPLY"},
    }
    base.update(over)
    return JobLead(**base)


def test_all_expected_portals_registered():
    assert set(available_portals()) == _EXPECTED_PORTALS
    assert set(BROWSER_PORTALS) == {"indeed", "glassdoor", "workopolis", "linkedin", "jobbank"}
    assert set(ATS_PORTALS) == {"greenhouse", "ashby", "lever", "bamboohr"}


def test_protocol_conformance():
    for name in available_portals():
        adapter = get_adapter(name)
        assert isinstance(adapter, PortalAdapter), name


def test_unknown_portal_rejected():
    with pytest.raises(KeyError):
        get_adapter("monster")


def test_profile_enablement_from_manifest():
    it_portals = profile_portals("it")
    assert "indeed" in it_portals and "greenhouse" in it_portals
    # validation passes for an enabled portal
    assert get_adapter("indeed", profile="it").name == "indeed"
    # and rejects a portal the manifest does not enable
    if "jobbank" not in it_portals:
        with pytest.raises(ValueError):
            get_adapter("jobbank", profile="it")


def test_screen_delegates_to_frozen_gate(monkeypatch):
    from jobbots.core.discovery import _gate_adapter

    calls = {}

    def spy(**kwargs):
        calls.update(kwargs)
        return True, 100, "ok"

    monkeypatch.setattr(_gate_adapter, "hard_screen_job", spy)
    decision = get_adapter("indeed").screen(_lead(), profile="it")
    assert decision.qualified is True and decision.score == 100.0
    assert calls["title"] == "IT Support Specialist"
    assert calls["profile"] == "it"
    assert calls["easy_apply"] is True


def test_screen_matches_direct_call_bit_for_bit():
    """Adapter screening == direct frozen gate call (the shadow invariant)."""
    from jobbots.core.discovery._gate_adapter import hard_screen_job

    lead = _lead()
    via = get_adapter("indeed").screen(lead, profile="it")
    direct = hard_screen_job(
        title=lead.title, company=lead.company, description=lead.description,
        location=lead.location, easy_apply=True, profile="it",
    )
    assert via.qualified == direct[0]
    assert (via.score or 0) == float(direct[1])
    assert via.reason == (direct[2] or "")
    assert via.resume_policy == "tailored"
    # general profile flips the resume policy, like production
    via_gen = get_adapter("indeed").screen(lead, profile="general")
    assert via_gen.resume_policy == "default"


def test_normalize_matches_legacy_normalizer():
    from jobbots.core.discovery.contracts import RawJob
    from jobbots.core.discovery.normalizer import normalize_raw_job

    raw = RawJob(
        source_platform="indeed", source_job_id="jk1", title="QA Analyst",
        company="Acme", location="Vancouver, BC", description="Testing",
        listing_url="https://ca.indeed.com/viewjob?jk=jk1",
    )
    lead = get_adapter("indeed").normalize_job(raw)
    legacy = normalize_raw_job(raw, discovery_engine="legacy")
    assert lead.source_job_id == legacy.source_job_id
    assert lead.title == legacy.job_title
    assert lead.company == legacy.company_name
    assert lead.url == legacy.listing_url
    assert lead.metadata["apply_type"] == legacy.apply_type


def test_apply_delegates_to_worker_dispatch(monkeypatch, tmp_path):
    import scripts.application_worker as aw

    captured = {}

    def fake_dispatch(job, result_path, *, keep_browser=False):
        captured["job"] = job
        result_path.write_text(
            '{"status": "applied", "result_url": "https://x", "reason": "ok"}',
            encoding="utf-8",
        )
        return 0, ""

    monkeypatch.setattr(aw, "dispatch", fake_dispatch)
    result = get_adapter("indeed").apply(_lead(), profile="it")
    assert result.status == "applied"
    assert result.result_url == "https://x"
    job = captured["job"]
    assert job["portal"] == "indeed"
    assert job["profile"] == "it"
    assert job["metadata"]["application_method"] == "easy_apply"
    assert job["source_job_id"] == "jk-shadow"


def test_verify_terminal_mapping_delegates():
    from jobbots.integrations.portals.base import ApplyResult

    adapter = get_adapter("indeed")
    lead = _lead()
    res = ApplyResult(
        status="applied", result_url="https://x", reason="",
        detail={"application_method": "easy_apply",
                "stats": {"applied_count": 1, "failed_count": 0,
                          "external_count": 0, "bookmarked_count": 0}},
    )
    check = adapter.verify(lead, res)
    assert check.verified is True
    assert check.method == "easy_apply"


def test_ats_adapters_wrap_core_ats_registry():
    from jobbots.core.ats import adapters as core_ats_adapters  # noqa: F401
    from jobbots.core.ats.registry import _ADAPTERS, detect_platform

    assert detect_platform("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    for name in ATS_PORTALS:
        portal_adapter = get_adapter(name)
        assert portal_adapter.is_ats is True
        assert portal_adapter.ats_adapter_class() is _ADAPTERS[name]
        assert portal_adapter.detect(f"https://jobs.lever.co/acme/abc") == "lever"


def test_facade_identity():
    """Facades expose the canonical objects themselves (no copies/wrappers)."""
    import importlib

    telegram = importlib.import_module("jobbots.integrations.telegram")
    from jobbots.core.alerts import send_telegram_alert

    assert telegram.send_alert is send_telegram_alert

    storage = importlib.import_module("jobbots.integrations.storage")
    assert storage.job_queue is importlib.import_module("jobbots.core.job_queue")
    assert storage.session_registry is importlib.import_module("jobbots.core.session_registry")

    email = importlib.import_module("jobbots.integrations.email")
    assert email.imap_reader is importlib.import_module("jobbots.core.imap_reader")

    browser = importlib.import_module("jobbots.integrations.browser")
    assert browser.profile_lease is importlib.import_module("jobbots.core.browser.profile_lease")

    ai = importlib.import_module("jobbots.integrations.ai")
    assert ai.prompts is importlib.import_module("jobbots.core.llm_backend.ai.prompts")


def test_shadow_harness_offline():
    from jobbots.integrations import shadow

    gate = shadow.gate_shadow_checks()
    assert len(gate) == len(shadow._GATE_CASES)
    failures = [g for g in gate if not g["ok"]]
    assert not failures, f"adapter/direct gate drift: {failures}"
    assert shadow.run_shadow(sample=0) == 0


def test_supervised_bot_rows_delegate():
    from jobbots.integrations.portals import supervised_bots

    rows = supervised_bots()
    names = {r.get("bot_name") for r in rows}
    assert {"indeed_it", "indeed_general"} <= names
    indeed_rows = supervised_bots("indeed")
    assert indeed_rows and all(r.get("portal") == "indeed" for r in indeed_rows)


def test_cli_new_commands_present(capsys):
    from jobbots.app.cli import main

    assert main([]) == 2
    out = capsys.readouterr().out
    for cmd in ("portals", "bot", "shadow"):
        assert cmd in out
    # unknown bot name rejected without spawning anything
    assert main(["bot", "definitely_not_a_bot"]) == 2
    # portals listing works offline
    assert main(["portals"]) == 0
    out = capsys.readouterr().out
    assert "indeed (browser)" in out
    assert "greenhouse (ATS)" in out
