"""Tests for the dual-engine discovery architecture.

Covers:
- Provider contracts
- Normalizer field compatibility
- Apply-type evidence classification
- Cross-platform deduplication
- Compatibility adapter mapping
- Idempotency & mock provider pipelines
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.discovery.contracts import RawJob, NormalizedJob, QueueRecord
from core.discovery.providers.base import DiscoveryRequest
from core.discovery.classification.apply_type import classify_apply_type
from core.discovery.normalizer import normalize_raw_job, normalize_batch
from core.discovery.deduplicator import deduplicate
from core.discovery.compatibility_adapter import to_queue_record, queue_record_to_enqueue_kwargs
from core.discovery.planner import run_discovery


def test_remote_location_and_radius_are_ported_to_provider_urls():
    """LinkedIn guest HTTP provider retired — Workopolis + request helpers remain."""
    from core.discovery.providers.workopolis_http_provider import WorkopolisHTTPProvider

    request = DiscoveryRequest(profile="it", search_terms=["IT Support"], locations=[""])
    assert request.is_remote_location("") is True
    assert request.is_remote_location("Remote") is True
    assert request.is_remote_location("Vancouver, BC") is False

    workopolis_remote = WorkopolisHTTPProvider._build_url(
        "IT Support", "", 0, radius_km=25, remote=True
    )
    assert "l=Remote" in workopolis_remote
    assert "radius=" not in workopolis_remote

    workopolis_local = WorkopolisHTTPProvider._build_url(
        "IT Support", "Vancouver, BC", 0, radius_km=25, remote=False
    )
    assert "radius=25" in workopolis_local


def test_browser_discovery_profiles_never_cross_identities(monkeypatch):
    """Portal NST profile resolution must not cross IT/General identities."""
    from core.discovery.providers import workopolis_browser_fallback

    # Isolate from host .env dual-slot / stamped IDs
    for k in list(os.environ):
        if k.startswith("NSTBROWSER_PROFILE"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NSTBROWSER_PROFILE_ID_WORKOPOLIS_IT", "workopolis-it")
    monkeypatch.setenv("NSTBROWSER_PROFILE_ID_WORKOPOLIS_GENERAL", "workopolis-general")
    monkeypatch.setenv("NSTBROWSER_ACTIVE_SLOT", "1")

    assert workopolis_browser_fallback._resolve_nst_profile("it") == "workopolis-it"
    assert workopolis_browser_fallback._resolve_nst_profile("general") == "workopolis-general"


# ---------------------------------------------------------------------------
# 1. Provider Contract Test
# ---------------------------------------------------------------------------

def test_raw_job_payload_hash():
    """Verify RawJob payload hashing is stable and unique."""
    raw1 = RawJob(
        source_platform="indeed",
        source_job_id="jk123",
        title="QA Engineer",
        company="Test Corp",
        location="Vancouver, BC",
        description="Write tests.",
        listing_url="https://indeed.com/viewjob?jk=jk123",
    )
    raw2 = RawJob(
        source_platform="indeed",
        source_job_id="jk123",
        title="QA Engineer",
        company="Test Corp",
        location="Vancouver, BC",
        description="Write tests.",
        listing_url="https://indeed.com/viewjob?jk=jk123",
    )
    raw3 = RawJob(
        source_platform="indeed",
        source_job_id="jk124",
        title="QA Engineer",
        company="Test Corp",
        location="Vancouver, BC",
        description="Write tests.",
        listing_url="https://indeed.com/viewjob?jk=jk124",
    )

    assert raw1.payload_hash() == raw2.payload_hash()
    assert raw1.payload_hash() != raw3.payload_hash()


# ---------------------------------------------------------------------------
# 2. Apply-type Evidence / Fallback Test
# ---------------------------------------------------------------------------

def test_classify_apply_type_easy_apply():
    """Verify that jobs with positive easy-apply evidence resolve to EASY_APPLY."""
    job = NormalizedJob(
        source_platform="linkedin",
        source_job_id="123",
        discovery_engine="linkedin_guest",
        query_id="test_query",
        job_title="Support Analyst",
        company_name="Acme",
        location="Vancouver",
        description="Help users",
        listing_url="https://linkedin.com/jobs/view/123",
        destination_url=None,
        date_posted=None,
        apply_type_source="linkedin_easy_apply_filter_click",
    )
    classification = classify_apply_type(job)
    assert classification.apply_type == "EASY_APPLY"
    assert classification.confidence == 0.9
    assert classification.verification_required is False


def test_classify_apply_type_company_apply():
    """Verify that external ATS destination URLs resolve to COMPANY_APPLY."""
    job = NormalizedJob(
        source_platform="indeed",
        source_job_id="456",
        discovery_engine="jobspy",
        query_id="test_query",
        job_title="Cloud Engineer",
        company_name="Acme",
        location="Vancouver",
        description="AWS setup",
        listing_url="https://indeed.com/viewjob?jk=456",
        destination_url="https://boards.greenhouse.io/acme/jobs/123",
        date_posted=None,
        apply_type_source="not_verified",
    )
    classification = classify_apply_type(job)
    assert classification.apply_type == "COMPANY_APPLY"
    assert classification.confidence == 0.85
    assert classification.verification_required is False


def test_classify_apply_type_unknown():
    """Verify that jobs without evidence fall back to UNKNOWN."""
    job = NormalizedJob(
        source_platform="indeed",
        source_job_id="789",
        discovery_engine="jobspy",
        query_id="test_query",
        job_title="Help Desk",
        company_name="Acme",
        location="Vancouver",
        description="Fix computers",
        listing_url="https://indeed.com/viewjob?jk=789",
        destination_url=None,
        date_posted=None,
        apply_type_source="not_verified",
    )
    classification = classify_apply_type(job)
    assert classification.apply_type == "UNKNOWN"
    assert classification.confidence == 0.0
    assert classification.verification_required is True


def test_jobspipe_direct_ats_conversion_and_adzuna_redirect_conversion():
    """API aggregators only emit direct ATS URLs supported by the applier."""
    from core.discovery.providers.jobspipe_provider import jobspipe_job_to_raw
    from core.discovery.providers.adzuna_provider import adzuna_result_to_raw

    raw = jobspipe_job_to_raw({
        "id": "jp-1", "job_title": "QA Engineer", "company": "Acme",
        "location": "Vancouver, BC", "final_url": "https://jobs.lever.co/acme/123",
    }, search_term="QA Engineer")
    assert raw is not None
    assert raw.source_platform == "lever"
    assert raw.destination_url == "https://jobs.lever.co/acme/123"
    assert jobspipe_job_to_raw({"id": "board", "job_title": "QA", "final_url": "https://example.com/job"}, search_term="QA") is None

    adzuna = adzuna_result_to_raw({
        "id": "adz-1", "title": "Support Engineer", "company": {"display_name": "Acme"},
        "location": {"display_name": "Remote, Canada"}, "redirect_url": "https://adzuna.example/1",
    }, search_term="Support Engineer", destination_url="https://job-boards.greenhouse.io/acme/jobs/456")
    assert adzuna is not None
    assert adzuna.source_platform == "greenhouse"


# ---------------------------------------------------------------------------
# 3. Normalizer Field Compatibility Test
# ---------------------------------------------------------------------------

def test_normalize_raw_job():
    """Verify normalizer maps RawJob fields accurately and adds query details."""
    raw = RawJob(
        source_platform="Indeed",
        source_job_id="jk999",
        title="Systems Administrator",
        company="Rob Half",
        location="Surrey, BC",
        description="AD, Windows, Exchange",
        listing_url="https://ca.indeed.com/viewjob?jk=jk999",
        destination_url="https://lever.co/robhalf/123",
        date_posted="2026-07-10",
        easy_apply_evidence="",
    )

    norm = normalize_raw_job(
        raw,
        discovery_engine="jobspy",
        search_term="Sysadmin",
        location="Surrey, BC",
        freshness_days=7,
    )

    assert norm.source_platform == "indeed"
    assert norm.source_job_id == "jk999"
    assert norm.discovery_engine == "jobspy"
    assert norm.query_id == "sysadmin_surrey_bc_7d"
    assert norm.job_title == "Systems Administrator"
    assert norm.company_name == "Rob Half"
    assert norm.location == "Surrey, BC"
    assert norm.description == "AD, Windows, Exchange"
    assert norm.date_posted == "2026-07-10"
    assert norm.listing_url == "https://ca.indeed.com/viewjob?jk=jk999"
    assert norm.destination_url == "https://lever.co/robhalf/123"
    assert norm.apply_type == "COMPANY_APPLY"  # derived from lever.co destination URL
    assert norm.raw_payload_hash == raw.payload_hash()
    assert len(norm.source_refs) == 1
    assert norm.source_refs[0] == {"platform": "indeed", "job_id": "jk999"}


# ---------------------------------------------------------------------------
# 4. Cross-Platform Deduplication Test
# ---------------------------------------------------------------------------

def test_deduplicate_cross_platform():
    """Verify cross-platform postings match and compile source_refs."""
    job_indeed = NormalizedJob(
        source_platform="indeed",
        source_job_id="ind1",
        discovery_engine="jobspy",
        query_id="query_1",
        job_title="QA Tester",
        company_name="KPU",
        location="Vancouver",
        description="Test software.",
        date_posted=None,
        listing_url="https://indeed.com/job1",
        destination_url="https://example.com/careers/qa",
        source_refs=[{"platform": "indeed", "job_id": "ind1"}],
    )
    job_workopolis = NormalizedJob(
        source_platform="workopolis",
        source_job_id="work2",
        discovery_engine="workopolis_http",
        query_id="query_2",
        job_title="QA Tester",
        company_name="KPU",
        location="Vancouver",
        description="Test software.",
        date_posted=None,
        listing_url="https://workopolis.com/job2",
        destination_url="https://example.com/careers/qa",  # matching destination URL
        source_refs=[{"platform": "workopolis", "job_id": "work2"}],
    )

    deduped = deduplicate([job_indeed, job_workopolis])
    assert len(deduped) == 1
    winner = deduped[0]
    assert winner.source_platform == "indeed"
    assert len(winner.source_refs) == 2
    assert {"platform": "indeed", "job_id": "ind1"} in winner.source_refs
    assert {"platform": "workopolis", "job_id": "work2"} in winner.source_refs


def test_deduplicate_different_positions():
    """Verify company/title/location matches but different description fingerprints do not dedup."""
    job1 = NormalizedJob(
        source_platform="indeed",
        source_job_id="ind_qa_1",
        discovery_engine="jobspy",
        query_id="q",
        job_title="QA Tester",
        company_name="KPU",
        location="Vancouver",
        description="Testing manual web interface, mobile application, selenium testing.",
        date_posted=None,
        listing_url="https://indeed.com/job1",
        destination_url=None,
        source_refs=[{"platform": "indeed", "job_id": "ind_qa_1"}],
    )
    job2 = NormalizedJob(
        source_platform="indeed",
        source_job_id="ind_qa_2",
        discovery_engine="jobspy",
        query_id="q",
        job_title="QA Tester",
        company_name="KPU",
        location="Vancouver",
        description="Automation engineer using pytest, playwright, CI/CD pipelines, Docker infrastructure.",
        date_posted=None,
        listing_url="https://indeed.com/job2",
        destination_url=None,
        source_refs=[{"platform": "indeed", "job_id": "ind_qa_2"}],
    )

    deduped = deduplicate([job1, job2])
    # CTL matches, but description fingerprint mismatch prevents deduplication
    assert len(deduped) == 2


# ---------------------------------------------------------------------------
# 5. Compatibility Adapter Test
# ---------------------------------------------------------------------------

def test_compatibility_adapter_contract():
    """Verify compatibility adapter outputs exact expected contract fields."""
    job = NormalizedJob(
        source_platform="linkedin",
        source_job_id="link123",
        discovery_engine="linkedin_guest",
        query_id="query",
        job_title="Security Analyst",
        company_name="Fortinet",
        location="Burnaby, BC",
        description="Network security monitoring, SOC.",
        date_posted=None,
        listing_url="https://linkedin.com/jobs/view/link123",
        destination_url=None,
        apply_type="EASY_APPLY",
    )

    rec = to_queue_record(job, profile="IT")

    assert rec.portal == "linkedin"
    assert rec.profile == "it"
    assert rec.source_job_id == "link123"
    assert rec.title == "Security Analyst"
    assert rec.company == "Fortinet"
    assert rec.location == "Burnaby, BC"
    assert rec.url == "https://linkedin.com/jobs/view/link123"
    assert rec.description == "Network security monitoring, SOC."
    assert rec.gate_score is None
    assert rec.gate_reason == ""
    assert rec.resume_policy == "tailored"
    assert rec.initial_status == "queued"
    assert rec.application_method == "easy_apply"

    # Enqueue arguments matching enqueue_approved_job signature
    kwargs = queue_record_to_enqueue_kwargs(rec)
    expected_keys = {
        "portal", "profile", "job_id", "title", "company", "location",
        "url", "description", "gate_score", "gate_reason",
        "resume_policy", "initial_status", "application_method", "region",
        "company_ai_approved"
    }
    assert set(kwargs.keys()) == expected_keys


def test_compatibility_adapter_unknown():
    """Verify that UNKNOWN apply type maps to unknown method and unverified status."""
    job = NormalizedJob(
        source_platform="indeed",
        source_job_id="ind789",
        discovery_engine="jobspy",
        query_id="query",
        job_title="Help Desk",
        company_name="Acme",
        location="Vancouver",
        description="Fix computers",
        date_posted=None,
        listing_url="https://indeed.com/job789",
        destination_url=None,
        apply_type="UNKNOWN",
    )

    rec = to_queue_record(job, profile="IT")

    assert rec.portal == "indeed"
    assert rec.profile == "it"
    assert rec.source_job_id == "ind789"
    assert rec.initial_status == "unverified"
    assert rec.application_method == "unknown"


# ---------------------------------------------------------------------------
# 6. Idempotency & dry-run assertion
# ---------------------------------------------------------------------------

@patch("core.discovery.planner._load_search_terms")
@patch("core.discovery.planner._load_search_locations")
@patch("core.discovery.planner._build_providers")
@patch("core.discovery.planner._screen_and_enqueue")
def test_dry_run_no_enqueue_assertion(
    mock_screen_enqueue,
    mock_build_providers,
    mock_locations,
    mock_terms,
):
    """Verify that a dry-run planner invocation does not write/enqueue anything."""
    mock_terms.return_value = ["QA"]
    mock_locations.return_value = ["Vancouver"]

    # Mock provider
    mock_provider = MagicMock()
    mock_provider.name = "mock_provider"
    mock_provider.supported_platforms = ["indeed"]
    mock_provider.discover.return_value = [
        RawJob(
            source_platform="indeed",
            source_job_id="j1",
            title="QA Analyst",
            company="Acme",
            location="Vancouver",
            description="Testing jobs.",
            listing_url="https://indeed.com/viewjob?jk=j1",
        )
    ]
    mock_build_providers.return_value = [mock_provider]

    # Run planner in dry-run mode
    run_discovery(profile="it", portals=["indeed"], dry_run=True)

    # _screen_and_enqueue should be called with dry_run=True
    assert mock_screen_enqueue.call_count == 1
    kwargs = mock_screen_enqueue.call_args[1]
    assert kwargs["dry_run"] is True


# ---------------------------------------------------------------------------
# 7. IMAP Email Parsing Heuristics Test
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8. Geo / work-mode / apply-type policy (legacy Phase-I parity)
# ---------------------------------------------------------------------------

from core.discovery.classification.location_policy import (
    classify_region,
    detect_work_mode,
    decide_job_policy,
    policy_enabled,
    REGION_METRO_VAN,
    REGION_OTHER,
    WORK_REMOTE,
    WORK_HYBRID,
    WORK_ONSITE,
)


def _job(location, *, apply_type="UNKNOWN", is_remote_hint=False, description=""):
    return NormalizedJob(
        source_platform="indeed",
        source_job_id="jk-policy",
        discovery_engine="jobspy",
        query_id="q",
        job_title="Software Engineer",
        company_name="Acme",
        location=location,
        description=description,
        date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=jk-policy",
        destination_url=None,
        apply_type=apply_type,
        is_remote_hint=is_remote_hint,
    )


def test_classify_region_metro_vs_other():
    for loc in ("Vancouver, BC", "Surrey, BC", "Richmond, BC", "Burnaby, BC",
                "Coquitlam, BC", "Langley, BC", "Delta, BC", "White Rock, BC",
                "North Vancouver, BC", "New Westminster, BC"):
        assert classify_region(loc) == REGION_METRO_VAN, loc
    for loc in ("Toronto, ON", "Montréal, QC", "Calgary, AB", "Canada",
                "Ottawa, ON"):
        assert classify_region(loc) == REGION_OTHER, loc
    # US "Vancouver, WA" must not be treated as Metro Van.
    assert classify_region("Vancouver, WA") == REGION_OTHER


def test_detect_work_mode_priority():
    # Hybrid always wins, even when the board also tags it remote.
    assert detect_work_mode("Toronto, ON", "Hybrid work", is_remote_hint=True) == WORK_HYBRID
    assert detect_work_mode("Remote", "", is_remote_hint=False) == WORK_REMOTE
    assert detect_work_mode("Toronto, ON", "Fully remote role") == WORK_REMOTE
    assert detect_work_mode("Calgary, AB", "", is_remote_hint=True) == WORK_REMOTE
    assert detect_work_mode("Langley, BC", "Work Location: In person") == WORK_ONSITE


def test_policy_matches_hand_reviewed_examples():
    """Reproduce every one of the 10 manually-reviewed leads."""
    cases = [
        # (job, expected_action, expected_method_if_kept)
        # 1. InvestorCOM — Toronto, Remote, easy apply → REJECT (Metro only)
        (_job("Toronto, ON (Remote)", apply_type="EASY_APPLY", is_remote_hint=True), "REJECT", None),
        # 2. Smile Digital — Toronto, Remote, company site → REJECT
        (_job("Toronto, ON (Remote)", apply_type="COMPANY_APPLY", is_remote_hint=True), "REJECT", None),
        # 3. General Dynamics — Calgary, Hybrid → REJECT
        (_job("Calgary, AB", apply_type="UNKNOWN", description="Hybrid work"), "REJECT", None),
        # 4. Basis — Vancouver, Remote, unverified apply-type → VERIFY (metro)
        (_job("Vancouver, BC", apply_type="UNKNOWN", is_remote_hint=True), "VERIFY", "unverified"),
        # 5. BDO — Montréal, company site, on-site → REJECT
        (_job("Montréal, QC", apply_type="COMPANY_APPLY", description="Work Location: In person"), "REJECT", None),
        # 6. Ashby — Canada, Remote, company site → REJECT
        (_job("Canada (Remote)", apply_type="COMPANY_APPLY", is_remote_hint=True), "REJECT", None),
        # 7. UBC — Vancouver, company site → SAVE (bookmark)
        (_job("Vancouver, BC", apply_type="COMPANY_APPLY"), "SAVE", "company_site"),
        # 8. Airbus — Montréal, Hybrid, company site → REJECT
        (_job("Montréal, QC", apply_type="COMPANY_APPLY", description="Hybrid work"), "REJECT", None),
        # 9. Wattpad — Toronto, Hybrid, easy apply → REJECT (hybrid out of metro)
        (_job("Toronto, ON (Remote)", apply_type="EASY_APPLY", is_remote_hint=True, description="Hybrid work"), "REJECT", None),
        # 10. Traction Rec — Vancouver, Hybrid, easy apply → APPLY (metro)
        (_job("Vancouver, BC", apply_type="EASY_APPLY", description="Hybrid work"), "APPLY", "easy_apply"),
    ]
    for idx, (job, expected_action, expected_method) in enumerate(cases, start=1):
        decision = decide_job_policy(job)
        assert decision.action == expected_action, (
            f"case {idx}: got {decision.action} ({decision.reason}), want {expected_action}"
        )
        if expected_method is not None:
            assert decision.application_method == expected_method, (
                f"case {idx}: method {decision.application_method} != {expected_method}"
            )


def test_policy_metro_company_site_saves_as_bookmark():
    decision = decide_job_policy(_job("Burnaby, BC", apply_type="COMPANY_APPLY"))
    assert decision.action == "SAVE"
    assert decision.application_method == "company_site"
    assert decision.gate_easy_apply is False  # strict save gate


# --- Unverified apply-type safeguards -------------------------------------

def test_title_geo_exclusive_overrides_search_centre_location():
    """Google SERP often tags the search centre while the title is exclusive."""
    from core.discovery.classification.location_policy import title_exclusive_out_of_area

    assert title_exclusive_out_of_area(
        "IT Helpdesk Specialist | Quebec City (Province of Quebec, Canada)"
    ) == "title_geo_outside_metro"
    assert title_exclusive_out_of_area(
        "Technical Support Analyst (Remote - Mexico Only)"
    ) == "title_geo_outside_metro"
    assert title_exclusive_out_of_area("Service Desk Analyst") is None
    assert title_exclusive_out_of_area(
        "Support Engineer (Toronto / Vancouver)"
    ) is None

    quebec = _job(
        "Vancouver, BC",
        apply_type="COMPANY_APPLY",
    )
    quebec.job_title = "IT Helpdesk Specialist | Quebec City (Province of Quebec, Canada)"
    d = decide_job_policy(quebec)
    assert d.action == "REJECT"
    assert d.reason == "title_geo_outside_metro"

    mexico = _job("Vancouver, BC", apply_type="COMPANY_APPLY")
    mexico.job_title = "Technical Support Analyst (Remote - Mexico Only) - Greenhouse"
    d2 = decide_job_policy(mexico)
    assert d2.action == "REJECT"
    assert d2.reason == "title_geo_outside_metro"


def test_outside_metro_unverified_apply_type_is_rejected():
    """Outside Metro Van, an unknown/unverified apply type is never queued."""
    for job in (
        _job("Toronto, ON (Remote)", apply_type="UNKNOWN", is_remote_hint=True),   # remote but unverified apply
        _job("Ottawa, ON (Remote)", apply_type="UNKNOWN", is_remote_hint=True),
    ):
        decision = decide_job_policy(job)
        assert decision.action == "REJECT"
        assert decision.reason == "outside_metro_vancouver_only"
        assert decision.keep is False  # planner never enqueues → cannot be leased


def test_outside_metro_requires_confirmed_remote():
    """Easy-apply out-of-province with no confirmed-remote signal is rejected."""
    # is_remote_hint=False and no remote token in the location string.
    decision = decide_job_policy(_job("Toronto, ON", apply_type="EASY_APPLY"))
    assert decision.action == "REJECT"
    assert decision.reason == "outside_metro_vancouver_only"


def test_outside_metro_confirmed_remote_easy_apply_is_rejected():
    decision = decide_job_policy(
        _job("Toronto, ON (Remote)", apply_type="EASY_APPLY", is_remote_hint=True)
    )
    assert decision.action == "REJECT"
    assert decision.reason == "outside_metro_vancouver_only"


def test_metro_boundary_cannot_be_disabled_by_legacy_geo_flag(monkeypatch):
    monkeypatch.setenv("DISCOVERY_GEO_POLICY", "0")
    monkeypatch.delenv("METRO_VANCOUVER_ONLY", raising=False)
    assert policy_enabled() is True
    decision = decide_job_policy(
        _job("Remote, Canada", apply_type="EASY_APPLY", is_remote_hint=True)
    )
    assert decision.action == "REJECT"
    assert decision.reason == "outside_metro_vancouver_only"


def test_remote_pass_semantics_do_not_bypass_metro_boundary():
    """Remote evidence never bypasses the Metro Vancouver-only boundary."""
    from core.discovery.classification.location_policy import _confirmed_remote

    # Remote pass + confirmed Easy Apply + explicit fully remote → REJECT
    remote_ea = _job(
        "Toronto, ON (Remote)", apply_type="EASY_APPLY", is_remote_hint=True,
        description="Fully remote Canada-wide role",
    )
    # Simulate search-pass provenance living only on apply_type_source —
    # it must NOT be consulted by _confirmed_remote.
    remote_ea.apply_type_source = "indeed_easy_apply_filtered_pass"
    assert _confirmed_remote(remote_ea) is True
    d = decide_job_policy(remote_ea)
    assert d.action == "REJECT"
    assert d.reason == "outside_metro_vancouver_only"

    # Location token alone also confirms remote (no is_remote_hint needed)
    loc_remote = _job("Remote - Canada", apply_type="EASY_APPLY")
    loc_remote.apply_type_source = "indeed_easy_apply_filtered_pass"
    assert _confirmed_remote(loc_remote) is True
    assert decide_job_policy(loc_remote).action == "REJECT"

    # Remote pass + confirmed Easy Apply + hybrid → REJECT
    hybrid = _job(
        "Toronto, ON", apply_type="EASY_APPLY", is_remote_hint=True,
        description="Hybrid work — 3 days in office",
    )
    hybrid.apply_type_source = "indeed_easy_apply_filtered_pass"
    d = decide_job_policy(hybrid)
    assert d.action == "REJECT"
    assert d.reason == "outside_metro_vancouver_only"

    # Remote pass + confirmed Easy Apply + no remote evidence → REJECT
    no_remote = _job("Toronto, ON", apply_type="EASY_APPLY", is_remote_hint=False)
    no_remote.apply_type_source = "indeed_easy_apply_filtered_pass"
    assert _confirmed_remote(no_remote) is False
    d = decide_job_policy(no_remote)
    assert d.action == "REJECT"
    assert d.reason == "outside_metro_vancouver_only"

    # Remote pass + company-site → REJECT (even if confirmed remote)
    company = _job(
        "Toronto, ON (Remote)", apply_type="COMPANY_APPLY", is_remote_hint=True,
    )
    company.apply_type_source = "external_ats_url:jobs.lever.co"
    d = decide_job_policy(company)
    assert d.action == "REJECT"
    assert d.reason == "outside_metro_vancouver_only"

    # Search-pass name alone never confirms remote
    pass_only = _job("Calgary, AB", apply_type="EASY_APPLY", is_remote_hint=False)
    pass_only.apply_type_source = "indeed_easy_apply_filtered_pass"
    assert _confirmed_remote(pass_only) is False


def test_policy_reject_skips_ai_screen(monkeypatch):
    """Deterministic policy rejects must not call the AI gate."""
    from core.discovery import planner
    from core.discovery.contracts import NormalizedJob

    calls = {"n": 0}

    monkeypatch.setenv("DISCOVERY_GEO_POLICY", "1")
    monkeypatch.delenv("BYPASS_SCREENING", raising=False)
    monkeypatch.setattr(planner, "_ensure_monorepo_path", lambda: None)

    import core.discovery._gate_adapter as ga

    def fake_screen(**kwargs):
        calls["n"] += 1
        return True, 90, "ok"

    monkeypatch.setattr(ga, "screen_job", fake_screen)
    monkeypatch.setattr(ga, "hard_screen_job", fake_screen)

    reject_job = NormalizedJob(
        source_platform="indeed", source_job_id="jk-rej",
        discovery_engine="jobspy", query_id="q",
        job_title="Sales Rep", company_name="Acme",
        location="Toronto, ON", description="Hybrid work",
        date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=jk-rej",
        destination_url=None,
        apply_type="EASY_APPLY",
        is_remote_hint=True,
    )
    keep_job = NormalizedJob(
        source_platform="indeed", source_job_id="jk-ok",
        discovery_engine="jobspy", query_id="q",
        job_title="Help Desk Analyst", company_name="Acme",
        location="Vancouver, BC", description="IT support",
        date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=jk-ok",
        destination_url=None,
        apply_type="EASY_APPLY",
    )

    from core.discovery.indeed_sync import IndeedSyncIndex
    idx = IndeedSyncIndex(queue=None, history_ids=set(), load_history=False)
    stats = planner._screen_and_enqueue(
        [reject_job, keep_job], "it", dry_run=True, indeed_sync_index=idx
    )
    assert stats["policy_rejected_before_ai"] == 1
    assert stats["ai_screened"] == 1
    assert stats["ai_passed"] == 1
    assert stats["ai_rejected"] == 0
    assert stats["final_apply"] == 1
    assert calls["n"] == 3  # only the Metro keep job + 2 pre-screens


def test_metro_van_unverified_preserves_unverified_method():
    """Metro-Van unknown apply type is NOT converted to easy_apply; it is held
    for the Phase II verification path (visit → apply/bookmark)."""
    decision = decide_job_policy(_job("Vancouver, BC", apply_type="UNKNOWN"))
    assert decision.action == "VERIFY"
    assert decision.application_method == "unverified"   # never easy_apply
    assert decision.initial_status == "queued"           # leasable, but via verify
    assert decision.keep is True


def test_general_company_site_save_does_not_use_it_ai_gate(monkeypatch):
    """General office leads may be saved without the IT-only batch reviewer."""
    from core.discovery import planner
    from core.discovery.indeed_sync import IndeedSyncIndex

    monkeypatch.setenv("DISCOVERY_GEO_POLICY", "1")
    monkeypatch.setattr(planner, "_ensure_monorepo_path", lambda: None)

    import core.discovery._gate_adapter as ga

    monkeypatch.setattr(
        ga,
        "hard_screen_job",
        lambda **kwargs: (True, 100, "general hard gate: configured office/customer-service title"),
    )
    monkeypatch.setattr(
        ga,
        "batch_ai_screen_jobs",
        lambda jobs: (_ for _ in ()).throw(AssertionError("General save reached IT AI gate")),
    )

    job = NormalizedJob(
        source_platform="indeed",
        source_job_id="general-company-save",
        discovery_engine="jobspy",
        query_id="q",
        job_title="Administrative Assistant",
        company_name="Acme",
        location="Vancouver, BC",
        description="Coordinate office schedules and customer inquiries.",
        date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=general-company-save",
        destination_url="https://acme.example/careers/1",
        apply_type="COMPANY_APPLY",
    )
    stats = planner._screen_and_enqueue(
        [job], "general", dry_run=True,
        indeed_sync_index=IndeedSyncIndex(queue=None, history_ids=set(), load_history=False),
    )

    assert stats["passed"] == 1
    assert stats["final_save"] == 1
    assert stats["ai_screened"] == 1
    assert stats["ai_rejected"] == 0


def test_status_unverified_is_not_a_leasable_status():
    """Clarify the two distinct uses of the word "unverified":

    * As a **status** — ``status="unverified"`` — a record is NOT leasable
      (``claim()`` only leases ``queued``/``retry``). This is the state we do NOT
      use for the Metro-Van route.
    * As an **application_method** — ``metadata.application_method="unverified"``
      with ``status="queued"`` — this IS intentionally leasable (the lease-and-
      verify route). See ``test_metro_van_unverified_method_is_leasable``.
    """
    from core.job_queue import RETRYABLE
    assert "unverified" not in RETRYABLE          # status "unverified" → never leased
    assert {"queued", "retry"} <= RETRYABLE       # the leasable statuses


def test_metro_van_unverified_method_is_leasable(tmp_path):
    """The chosen Metro-Van route (status=queued, method=unverified) IS leasable,
    unlike a record whose *status* is unverified."""
    from core.job_queue import JobQueue
    q = JobQueue(tmp_path / "q.db")
    jid, _ = q.enqueue(
        portal="indeed", profile="it", source_job_id="mv-unv-1", title="T", company="C",
        url="https://x", metadata={"application_method": "unverified", "region": "METRO_VAN"},
        initial_status="queued",
    )
    job = q.claim(worker="w", portals=["indeed"], profile="it")
    assert job is not None and job["id"] == jid  # queued+method=unverified → leasable
    q.drop_test_database()


def test_worker_dispatch_never_blind_applies_unverified(monkeypatch, tmp_path):
    """Phase II routing proof: an ``unverified`` record is sent through the
    verify path (bookmark-first + verify), a ``company_site`` record is
    bookmark-only, and only ``easy_apply`` submits directly."""
    import scripts.application_worker as aw

    captured = {}

    class _Result:
        returncode = 0

    def fake_run(cmd, cwd=None, env=None):
        captured["env"] = dict(env or {})
        return _Result()

    monkeypatch.setattr(aw.subprocess, "run", fake_run)
    monkeypatch.setattr(aw, "ensure_resume_server_healthy", lambda: True)
    # Clear dual-slot / host .env so per-bot key is what dispatch uses.
    for k in list(os.environ):
        if k.startswith("NSTBROWSER_PROFILE") or k in {
            "NSTBROWSER_ACTIVE_SLOT", "NSTBROWSER_API_KEY_2", "NSTBROWSER_DAILY_OPENS_1",
        }:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NSTBROWSER_ACTIVE_SLOT", "1")
    monkeypatch.setenv("NSTBROWSER_PROFILE_ID_INDEED_IT", "cf393220-reuse-only")
    monkeypatch.setenv("BROWSER_VENDOR", "nstbrowser")

    def mkjob(method):
        return {
            "id": "1", "portal": "indeed", "profile": "it",
            "source_job_id": "jk1", "title": "QA Analyst", "company": "Acme",
            "url": "https://ca.indeed.com/viewjob?jk=jk1", "description": "d",
            "attempts": 0, "metadata": {"application_method": method},
        }

    result_path = tmp_path / "res.json"

    aw.dispatch(mkjob("unverified"), result_path)
    env = captured["env"]
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") == "1"
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") is None  # not forced to bookmark-only
    assert env.get("JOB_QUEUE_BOOKMARK_FIRST") == "1"  # lead saved first
    assert env.get("NSTBROWSER_FORBID_CREATE") == "1"
    assert env.get("NSTBROWSER_PROFILE_ID") == "cf393220-reuse-only"

    aw.dispatch(mkjob("company_site"), result_path)
    env = captured["env"]
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") == "1"
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") is None

    aw.dispatch(mkjob("easy_apply"), result_path)
    env = captured["env"]
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") is None
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") is None
    # Explicit off (not unset) so Infisical/.env keep-alive cannot leak.
    assert env.get("KEEP_BROWSER") == "0"
    assert env.get("NSTBROWSER_KEEP_ALIVE") == "0"

    aw.dispatch(mkjob("easy_apply"), result_path, keep_browser=True)
    env = captured["env"]
    assert env.get("KEEP_BROWSER") == "1"
    assert env.get("NSTBROWSER_KEEP_ALIVE") == "1"
    assert env.get("JOB_QUEUE_BOOKMARK_FIRST") == "1"
    assert env.get("NSTBROWSER_FORBID_CREATE") == "1"


def test_jobspy_remote_pass_forces_remote_and_easy_apply():
    """Empty / Remote: Easy Apply filter wins; is_remote is omitted (Indeed
    if/elif) and applied client-side. Metro uses two passes."""
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.scrape_proxy import ScrapeProxyLadder, ProxyTier

    provider = JobSpyProvider(portals=["indeed"])
    ladder = ScrapeProxyLadder(tiers=ProxyTier(), mode="local")
    calls = []

    class _EmptyDF:
        empty = True

    def fake_scrape(**kwargs):
        calls.append(dict(kwargs))
        return _EmptyDF()

    remote_req = DiscoveryRequest(
        profile="it", search_terms=["QA"], locations=[""], radius_km=25,
    )
    provider._scrape_term(fake_scrape, term="QA", location="", request=remote_req, ladder=ladder)
    assert len(calls) == 1
    assert calls[0].get("easy_apply") is True
    # Indeed cannot combine is_remote + easy_apply; remote is post-filtered.
    assert "is_remote" not in calls[0]
    assert "distance" not in calls[0]

    calls.clear()
    provider._scrape_term(fake_scrape, term="QA", location="Remote", request=remote_req, ladder=ladder)
    assert calls[0].get("easy_apply") is True
    assert "is_remote" not in calls[0]

    calls.clear()
    metro_req = DiscoveryRequest(
        profile="it", search_terms=["QA"], locations=["Surrey, BC"], radius_km=25,
    )
    provider._scrape_term(fake_scrape, term="QA", location="Surrey, BC", request=metro_req, ladder=ladder)
    assert len(calls) == 2  # Easy Apply pass + all-leads pass
    ea_pass, all_pass = calls
    assert ea_pass.get("distance") == 25
    assert ea_pass.get("easy_apply") is True
    assert all_pass.get("distance") == 25
    assert "easy_apply" not in all_pass  # all-leads keeps company-site / unknown
    assert all_pass.get("is_remote") is None or all_pass.get("is_remote") is not True


def test_jobspy_easy_apply_pass_omits_hours_old_so_filter_applies():
    """Indeed JobSpy silently ignores easy_apply when hours_old is set.

    The metro Easy Apply pass must drop hours_old so indeedApplyScope is used.
    """
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.scrape_proxy import ScrapeProxyLadder, ProxyTier

    provider = JobSpyProvider(portals=["indeed"])
    ladder = ScrapeProxyLadder(tiers=ProxyTier(), mode="local")
    calls = []

    class _EmptyDF:
        empty = True

    def fake_scrape(**kwargs):
        calls.append(dict(kwargs))
        return _EmptyDF()

    req = DiscoveryRequest(
        profile="it", search_terms=["QA"], locations=["Vancouver, BC"],
        radius_km=25, freshness_days=7,
    )
    provider._scrape_term(fake_scrape, term="QA", location="Vancouver, BC", request=req, ladder=ladder)
    assert len(calls) == 2
    ea_pass, all_pass = calls
    assert ea_pass.get("easy_apply") is True
    assert "hours_old" not in ea_pass  # critical: otherwise Indeed ignores easy_apply
    assert "easy_apply" not in all_pass
    assert all_pass.get("hours_old") == 7 * 24  # all-leads may keep freshness


def test_linkedin_jobspy_only_runs_easy_apply_pass():
    """LinkedIn discovery must not enqueue an unverified all-leads pass."""
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.scrape_proxy import ScrapeProxyLadder, ProxyTier

    provider = JobSpyProvider(portals=["linkedin"])
    ladder = ScrapeProxyLadder(tiers=ProxyTier(), mode="local")
    calls = []

    class _EmptyDF:
        empty = True

    def fake_scrape(**kwargs):
        calls.append(dict(kwargs))
        return _EmptyDF()

    request = DiscoveryRequest(
        profile="it", search_terms=["IT Support"], locations=["Vancouver, BC"],
        radius_km=25, freshness_days=7,
    )
    provider._scrape_term(
        fake_scrape,
        term="IT Support",
        location="Vancouver, BC",
        request=request,
        ladder=ladder,
    )

    assert len(calls) == 1
    assert calls[0]["site_name"] == ["linkedin"]
    assert calls[0]["easy_apply"] is True
    assert "hours_old" not in calls[0]


def test_linkedin_jobspy_external_ats_pass_fetches_details(monkeypatch):
    """Opt-in LinkedIn pass asks JobSpy for external applyUrl details."""
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.scrape_proxy import ScrapeProxyLadder, ProxyTier

    monkeypatch.setenv("LINKEDIN_EXTERNAL_ATS_DISCOVERY", "1")
    provider = JobSpyProvider(portals=["linkedin"])
    ladder = ScrapeProxyLadder(tiers=ProxyTier(), mode="local")
    calls = []

    class _EmptyDF:
        empty = True

    def fake_scrape(**kwargs):
        calls.append(dict(kwargs))
        return _EmptyDF()

    request = DiscoveryRequest(
        profile="it", search_terms=["IT Business Analyst"],
        locations=["Vancouver, BC"], radius_km=25, freshness_days=7,
    )
    provider._scrape_term(
        fake_scrape,
        term="IT Business Analyst",
        location="Vancouver, BC",
        request=request,
        ladder=ladder,
    )

    assert len(calls) == 2
    easy_apply_pass, external_pass = calls
    assert easy_apply_pass["easy_apply"] is True
    assert "linkedin_fetch_description" not in easy_apply_pass
    assert "easy_apply" not in external_pass
    assert external_pass["linkedin_fetch_description"] is True
    assert external_pass["hours_old"] == 7 * 24


def test_linkedin_jobspy_rows_get_direct_url_and_easy_apply_evidence():
    """JobSpy LinkedIn rows map to the queue's direct-link contract."""
    from core.discovery.providers.jobspy_provider import JobSpyProvider

    row = {
        "site": "linkedin",
        "id": "123456789",
        "title": "IT Support Specialist",
        "company": "Acme",
        "location": "Vancouver, BC",
        "description": "Support internal users.",
        "job_url": "https://www.linkedin.com/jobs/search/?currentJobId=123456789",
        "job_url_direct": "https://www.linkedin.com/jobs/view/123456789",
        "easy_apply": False,
    }

    raw = JobSpyProvider._row_to_raw_job(
        row,
        "IT Support",
        search_pass="linkedin_easy_apply",
        force_easy_apply_evidence=True,
    )
    normalized = normalize_raw_job(raw, discovery_engine="jobspy")

    assert raw.listing_url == "https://www.linkedin.com/jobs/view/123456789/"
    assert raw.easy_apply_evidence == "linkedin_easy_apply_filtered_pass"
    assert normalized.apply_type == "EASY_APPLY"
    assert normalized.apply_type_confirmed is True


def test_linkedin_runner_exposes_direct_queue_mode():
    """Worker + LinkedIn hybrid runner must support one queued URL (not search scan)."""
    from pathlib import Path

    monorepo = Path(__file__).resolve().parents[1]
    root = monorepo.parent
    # application_worker stamps LINKEDIN_DIRECT_JOB_URL for the LinkedIn portal path
    worker = (monorepo / "scripts" / "application_worker.py").read_text(encoding="utf-8")
    assert "LINKEDIN_DIRECT_JOB_URL" in worker
    assert "LINKEDIN_DIRECT_JOB_JSON" in worker

    hybrid = (
        root / "legacy" / "linkedin-ai-auto-apply-source" / "hybrid_runner.js"
    )
    if hybrid.is_file():
        source = hybrid.read_text(encoding="utf-8")
        assert "LINKEDIN_DIRECT_JOB_URL" in source


def test_jobspy_filtered_pass_tags_confirmed_easy_apply_even_without_row_flag():
    """Jobs from an Easy Apply filtered pass are confirmed EASY_APPLY even if
    JobSpy's per-row easy_apply column is missing/False."""
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.normalizer import normalize_raw_job

    class _Row(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    row = _Row({
        "site": "indeed",
        "id": "jk-traction",
        "title": "QA Analyst (Hybrid)",
        "company": "Traction Rec",
        "location": "Vancouver, BC",
        "description": "QA",
        "job_url": "https://ca.indeed.com/viewjob?jk=jk-traction",
        "job_url_direct": "https://ca.indeed.com/viewjob?jk=jk-traction",
        "easy_apply": False,  # unreliable / missing — must NOT block confirmation
        "is_remote": False,
    })
    raw = JobSpyProvider._row_to_raw_job(
        row, "QA Analyst",
        search_pass="metro_easy_apply",
        force_easy_apply_evidence=True,
    )
    assert raw.easy_apply_evidence == "indeed_easy_apply_filtered_pass"
    job = normalize_raw_job(raw, discovery_engine="jobspy", search_term="QA Analyst", location="Vancouver, BC")
    assert job.apply_type == "EASY_APPLY"
    assert job.apply_type_confirmed is True
    assert job.apply_type_source == "indeed_easy_apply_filtered_pass"


def test_absence_of_easy_apply_is_unknown_not_company_site():
    """All-leads pass without ATS URL must stay UNKNOWN (never guess company-site)."""
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.normalizer import normalize_raw_job

    class _Row(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    row = _Row({
        "site": "indeed",
        "id": "jk-unknown",
        "title": "IT Support",
        "company": "Acme",
        "location": "Vancouver, BC",
        "description": "support",
        "job_url": "https://ca.indeed.com/viewjob?jk=jk-unknown",
        "job_url_direct": "https://ca.indeed.com/viewjob?jk=jk-unknown",
        "easy_apply": False,
    })
    raw = JobSpyProvider._row_to_raw_job(
        row, "IT Support", search_pass="metro_all_leads", force_easy_apply_evidence=False,
    )
    assert raw.easy_apply_evidence == ""
    job = normalize_raw_job(raw, discovery_engine="jobspy")
    assert job.apply_type == "UNKNOWN"
    assert job.apply_type_confirmed is False


def test_dedup_prefers_easy_apply_over_company_site():
    """When the same job appears in both Metro passes, keep confirmed Easy Apply."""
    from core.discovery.deduplicator import deduplicate
    from core.discovery.contracts import NormalizedJob

    company = NormalizedJob(
        source_platform="indeed", source_job_id="jk1", discovery_engine="jobspy",
        query_id="q", job_title="QA Analyst", company_name="Traction Rec",
        location="Vancouver, BC", description="d", date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=jk1", destination_url=None,
        apply_type="COMPANY_APPLY", apply_type_source="external_ats_url:boards.greenhouse.io",
        apply_type_confidence=0.85, apply_type_confirmed=True,
    )
    easy = NormalizedJob(
        source_platform="indeed", source_job_id="jk1", discovery_engine="jobspy",
        query_id="q", job_title="QA Analyst", company_name="Traction Rec",
        location="Vancouver, BC", description="d", date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=jk1", destination_url=None,
        apply_type="EASY_APPLY", apply_type_source="indeed_easy_apply_filtered_pass",
        apply_type_confidence=0.9, apply_type_confirmed=True,
    )
    # Order: company-site first, then easy-apply duplicate must win.
    out = deduplicate([company, easy])
    assert len(out) == 1
    assert out[0].apply_type == "EASY_APPLY"
    assert out[0].apply_type_source == "indeed_easy_apply_filtered_pass"

def test_parse_email_for_job():
    """Verify that IMAP email subject and sender parsing extracts correct job info."""
    from scripts.sync_imap_applied_data import parse_email_for_job

    # Pattern: "Your application for [Job Title] at [Company]"
    res1 = parse_email_for_job(
        subject="Your application for QA Analyst at Fortinet!",
        from_addr="Fortinet Careers <no-reply@fortinet.com>"
    )
    assert res1["job_title"] == "QA Analyst"
    assert res1["company_name"] == "Fortinet"

    # Pattern: "Thank you for applying to [Company]"
    res2 = parse_email_for_job(
        subject="Thank you for applying to KPU!",
        from_addr="HR KPU <noreply@example.com>"
    )
    assert res2["company_name"] == "KPU"

    # Pattern: "Indeed Application Received: [Job Title]"
    res3 = parse_email_for_job(
        subject="Indeed Application Received: Junior IT Support",
        from_addr="Indeed Apply <apply@indeed.com>"
    )
    assert res3["job_title"] == "Junior IT Support"
    assert res3["source_platform"] == "indeed"


# ---------------------------------------------------------------------------
# 9. Phase-II terminal-state lockdown for lease-and-verify (Metro-Van unverified)
# ---------------------------------------------------------------------------

def test_resolve_direct_result_bookmarked_before_applied():
    """A company-site / verify-external bookmark resolves to bookmarked+company_site,
    never applied — even though the bot ran the apply flow first."""
    from core.shared_modules.queue_result import resolve_direct_queue_result
    stats = {"applied_count": 0, "failed_count": 0, "external_count": 0,
             "bookmarked_count": 1, "last_reason": "Company-site bookmarked (verify: external apply)"}
    status, method, _ = resolve_direct_queue_result(stats, verify_mode=True)
    assert status == "bookmarked"
    assert method == "company_site"


def test_resolve_direct_result_easy_apply_submission():
    from core.shared_modules.queue_result import resolve_direct_queue_result
    stats = {"applied_count": 1, "failed_count": 0, "external_count": 0, "bookmarked_count": 0}
    status, method, _ = resolve_direct_queue_result(stats, verify_mode=True)
    assert status == "applied"
    assert method == "easy_apply"


def test_resolve_direct_result_external_counts_as_bookmark():
    """An external redirect that still counted as applied is a saved lead, not a
    genuine submission → bookmarked + company_site."""
    from core.shared_modules.queue_result import resolve_direct_queue_result
    stats = {"applied_count": 1, "failed_count": 0, "external_count": 1, "bookmarked_count": 0}
    status, method, _ = resolve_direct_queue_result(stats, verify_mode=False)
    assert status == "bookmarked"
    assert method == "company_site"


def test_resolve_direct_result_verify_no_outcome_is_manual_review():
    """A verify job with no resolvable outcome must NOT retry forever."""
    from core.shared_modules.queue_result import resolve_direct_queue_result
    stats = {"applied_count": 0, "failed_count": 0, "external_count": 0, "bookmarked_count": 0}
    status, method, _ = resolve_direct_queue_result(stats, verify_mode=True)
    assert status == "manual_review"
    # A non-verify job with no outcome stays a (retryable) failure.
    status2, _, _ = resolve_direct_queue_result(stats, verify_mode=False)
    assert status2 == "failed"


def test_classify_outcome_resolves_method_and_terminal_state():
    """Worker-side terminal mapping: resolved method + bounded retry."""
    import scripts.application_worker as aw

    # Easy Apply submission (verify job) → applied + easy_apply.
    action, method = aw.classify_outcome(
        {"status": "applied", "application_method": "easy_apply"}, "unverified", 1, 3)
    assert action == "applied" and method == "easy_apply"

    # Already applied must NOT count as a new applied win.
    action, method = aw.classify_outcome(
        {"status": "applied", "reason": "Already applied to this job"}, "easy_apply", 1, 3)
    assert action == "already_applied" and method == "easy_apply"
    action, method = aw.classify_outcome(
        {"status": "already_applied", "reason": "already applied (LinkedIn top-card)"},
        "easy_apply", 1, 3)
    assert action == "already_applied"

    # Cover letter policy skip is terminal skipped.
    action, method = aw.classify_outcome(
        {"status": "failed", "reason": "Cover letter screen — skipped by policy"},
        "easy_apply", 1, 3)
    assert action == "skipped"

    # External/company-site (verify job) → bookmarked + company_site.
    action, method = aw.classify_outcome(
        {"status": "bookmarked", "application_method": "company_site"}, "unverified", 1, 3)
    assert action == "bookmarked" and method == "company_site"

    # Verify job, non-transient failure → manual_review (never endless retry).
    action, method = aw.classify_outcome(
        {"status": "failed", "reason": "Apply button not found"}, "unverified", 1, 3)
    assert action == "manual_review"

    # Verify job, transient failure within budget → retry.
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "captcha challenge blocked"}, "unverified", 1, 3)
    assert action == "captcha_cf_requeue"

    # Cloudflare (and canary_timeout text that mentions captcha) → end-of-queue requeue.
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "cloudflare turnstile"}, "easy_apply", 1, 3)
    assert action == "captcha_cf_requeue"
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "canary_timeout mid captcha solve"}, "easy_apply", 2, 3)
    assert action == "captcha_cf_requeue"
    # Plain canary_timeout without captcha/cf markers stays a normal transient retry.
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "canary_timeout"}, "easy_apply", 1, 3)
    assert action == "retry"

    # Verify job, transient but attempts exhausted → manual_review (bounded).
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "captcha challenge blocked"}, "unverified", 3, 3)
    assert action == "manual_review"

    # Non-verify permanent failure keeps legacy dead-letter behaviour.
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "external apply"}, "easy_apply", 1, 3)
    assert action == "dead"

    # Explicit bot manual_review signal is honoured.
    action, _ = aw.classify_outcome({"status": "manual_review"}, "unverified", 1, 3)
    assert action == "manual_review"

    # Production thrash / SmartApply entry flakes → retry within budget (2026-07 rebuild).
    for reason in (
        "bot exited 1 without result",
        "NSTbrowser profile c05dd5c8 is already leased.",
        "SmartApply tab did not open (full-page)",
        "SmartApply failed",
        "Event loop is closed",
    ):
        action, _ = aw.classify_outcome(
            {"status": "failed", "reason": reason}, "easy_apply", 1, 3)
        assert action == "retry", reason
    # Exhausted budget → dead for non-verify.
    action, _ = aw.classify_outcome(
        {"status": "failed", "reason": "bot exited 1 without result"}, "easy_apply", 3, 3)
    assert action == "dead"


def test_open_chrome_stale_profile_lease_recovery_present():
    """Single-worker farm recovers DynamoDB 'already leased' once (source guard)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "jobbots" / "core" / "browser" / "open_chrome.py"
    text = src.read_text(encoding="utf-8")
    assert "force-releasing stale lease once" in text
    assert "force_release()" in text
    assert "except RuntimeError as lease_exc" in text


def test_smartapply_tab_recovery_present():
    """SmartApply recovers when focus jumps to ca.indeed.com homepage tab."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "jobbots"
        / "core"
        / "shared_modules"
        / "indeed"
        / "smartapply.py"
    )

    text = src.read_text(encoding="utf-8")
    assert "Recovered SmartApply tab after leave" in text
    assert "SMARTAPPLY_DOMAIN in cu" in text


def test_dispatch_method_region_guard():
    """Safeguard #1: lease-and-verify only for Metro-Van; otherwise bookmark-only."""
    import scripts.application_worker as aw

    def job(method, region):
        return {"metadata": {"application_method": method, "region": region}}

    assert aw._dispatch_method(job("unverified", "METRO_VAN")) == "unverified"
    # Non-metro verify job is degraded to safe bookmark-only (never applies).
    assert aw._dispatch_method(job("unverified", "OTHER")) == "company_site"
    assert aw._dispatch_method(job("unverified", "UNKNOWN")) == "company_site"
    # No region info → rely on the Phase I-B invariant (kept as-is).
    assert aw._dispatch_method(job("unverified", "")) == "unverified"
    # The guard only affects verify methods.
    assert aw._dispatch_method(job("easy_apply", "OTHER")) == "easy_apply"
    assert aw._dispatch_method(job("company_site", "OTHER")) == "company_site"


def test_application_worker_rejects_preexisting_non_metro_queue_rows():
    """A stale row cannot bypass the new Metro-only discovery boundary."""
    import scripts.application_worker as aw

    assert aw._is_metro_vancouver_queue_job(
        {"location": "Vancouver, BC", "metadata": {"region": "METRO_VAN"}}
    ) is True
    assert aw._is_metro_vancouver_queue_job(
        {"location": "Remote, Canada", "metadata": {"region": "OTHER"}}
    ) is False
    assert aw._is_metro_vancouver_queue_job(
        {"location": "Toronto, ON", "metadata": {}}
    ) is False
    assert aw._is_metro_vancouver_queue_job({"location": "", "metadata": {}}) is False
    # Search-centre false positive: location/metadata metro, title exclusive elsewhere.
    assert aw._is_metro_vancouver_queue_job(
        {
            "title": "IT Helpdesk Specialist | Quebec City (Province of Quebec, Canada)",
            "location": "Vancouver, BC",
            "metadata": {"region": "METRO_VAN"},
        }
    ) is False
    assert aw._is_metro_vancouver_queue_job(
        {
            "title": "Technical Support Analyst (Remote - Mexico Only)",
            "location": "Vancouver, BC",
            "metadata": {"region": "METRO_VAN"},
        }
    ) is False
    # Multi-city title that still names Metro Van remains eligible.
    assert aw._is_metro_vancouver_queue_job(
        {
            "title": "Support Engineer (Toronto / Vancouver)",
            "location": "Vancouver, BC",
            "metadata": {"region": "METRO_VAN"},
        }
    ) is True


def test_build_dispatch_env_resets_stale_verify_flags(tmp_path):
    """Safeguard #7: inherited verify/bookmark flags cannot leak into a job."""
    import scripts.application_worker as aw

    stale = {
        "JOB_QUEUE_VERIFY_APPLY_TYPE": "1",
        "JOB_QUEUE_BOOKMARK_ONLY": "1",
        "JOB_QUEUE_BOOKMARK_FIRST": "1",
        "PATH": "/usr/bin",
    }
    base_job = {"portal": "indeed", "profile": "it", "source_job_id": "jk",
                "title": "T", "company": "C", "url": "u", "description": "d",
                "metadata": {"application_method": "easy_apply"}}

    # Easy-apply job: both verify flags must be cleared despite stale base env.
    _, env, _ = aw.build_dispatch_env(base_job, tmp_path / "r.json", base_env=stale)
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") is None
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") is None
    assert env.get("JOB_QUEUE_BOOKMARK_FIRST") == "1"

    # Company-site job: only bookmark-only is set, verify cleared.
    cs_job = dict(base_job, metadata={"application_method": "company_site"})
    _, env, _ = aw.build_dispatch_env(cs_job, tmp_path / "r.json", base_env=stale)
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") == "1"
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") is None

    # Verify job: only verify flag set, bookmark-only cleared.
    v_job = dict(base_job, metadata={"application_method": "unverified", "region": "METRO_VAN"})
    _, env, _ = aw.build_dispatch_env(v_job, tmp_path / "r.json", base_env=stale)
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") == "1"
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") is None


def test_job_queue_manual_review_is_terminal(tmp_path):
    """manual_review is a terminal, non-retryable outcome."""
    from core.job_queue import JobQueue, TERMINAL, RETRYABLE
    assert "manual_review" in TERMINAL and "manual_review" not in RETRYABLE
    q = JobQueue(tmp_path / "q.db")
    jid, _ = q.enqueue(portal="indeed", profile="it", source_job_id="mr-1", title="T",
                       company="C", url="u", metadata={"application_method": "unverified"})
    job = q.claim(worker="w")
    assert q.manual_review(jid, job["lease_owner"], "u", reason="ambiguous") is True
    assert q.counts() == {"manual_review": 1}
    # A terminal manual_review record is not re-leasable.
    assert q.claim(worker="w2") is None
    q.drop_test_database()


def test_job_queue_persists_resolved_method(tmp_path):
    """Safeguard #5: an unverified record's method is rewritten after verify."""
    from core.job_queue import JobQueue
    q = JobQueue(tmp_path / "q.db")
    jid, _ = q.enqueue(portal="indeed", profile="it", source_job_id="rm-1", title="T",
                       company="C", url="u", metadata={"application_method": "unverified"})
    job = q.claim(worker="w")
    assert q.set_application_method(jid, "easy_apply", lease_owner=job["lease_owner"]) is True
    stored = q.jobs.find_one({"_id": jid})
    assert stored["metadata"]["application_method"] == "easy_apply"
    # Finishing as applied still works after the method rewrite.
    assert q.complete(jid, job["lease_owner"], "u") is True
    q.drop_test_database()


# ---------------------------------------------------------------------------
# 10. Wave A — General-profile isolation + Glassdoor/Indeed dispatch env
# ---------------------------------------------------------------------------

def test_general_profile_search_terms_isolated_from_it():
    """indeed_general must load General search terms, not IT title families."""
    from core.discovery.planner import _load_search_terms

    it_terms = _load_search_terms("it")
    gen_terms = _load_search_terms("general")
    assert it_terms, "IT search terms must load"
    assert gen_terms, "General search terms must load"
    assert it_terms != gen_terms

    # IT family markers present in IT config
    it_blob = " | ".join(it_terms).lower()
    assert "qa analyst" in it_blob or "quality assurance" in it_blob
    assert "it support" in it_blob or "systems administrator" in it_blob

    # General family markers present; IT QA family must not dominate General
    gen_blob = " | ".join(gen_terms).lower()
    assert "customer service" in gen_blob or "receptionist" in gen_blob
    assert "qa analyst" not in gen_blob
    assert "sdet" not in gen_blob
    assert "penetration tester" not in gen_blob


def test_general_gate_does_not_use_it_title_hard_reject(monkeypatch):
    """With JOB_PROFILE=GENERAL, customer-service titles are not rejected by IT rules.

    IT profile hard-rejects non-IT titles via ``_obvious_non_it_reject``.
    General profile uses ``_general_local_gate_reject`` instead (and rejects IT titles).
    """
    import sys
    from pathlib import Path

    from jobbots.core.shared_modules.indeed import gates as gates_mod

    title = "Receptionist"
    company = "Acme"
    location = "Vancouver, BC"
    description = "Front desk and guest check-in."

    # IT path: obvious non-IT reject should fire.
    it_reject, it_reason = gates_mod._obvious_non_it_reject(
        title, company, location, "", description, easy_apply=True
    )
    assert it_reject is True, f"IT gate should reject Receptionist: {it_reason}"

    # General path: Receptionist must NOT be rejected by General local rules.
    gen_reject, gen_reason = gates_mod._general_local_gate_reject(
        title, company, location, description
    )
    assert gen_reject is False, f"General gate must not reject Receptionist: {gen_reason}"

    # And General *does* reject an IT title (proves it uses its own rules).
    it_title_reject, _ = gates_mod._general_local_gate_reject(
        "QA Analyst", company, location, "Selenium testing"
    )
    assert it_title_reject is True

    # screen_job_with_ai branches on JOB_PROFILE without calling Groq when local reject hits.
    monkeypatch.setenv("JOB_PROFILE", "IT")
    passed, score, reason = gates_mod.screen_job_with_ai(
        title, company, description, location, easy_apply=True
    )
    assert passed is False
    # Local IT hard-gate reasons vary by phrasing — accept any hard/local reject.
    assert (
        "local obvious reject" in reason
        or "hard gate:" in reason
        or "non-IT title" in reason
        or "title lacks" in reason
    ), f"unexpected IT reject reason: {reason!r}"


    # GENERAL: Receptionist passes local general gate; mock AI so we don't hit the network.
    monkeypatch.setenv("JOB_PROFILE", "GENERAL")
    monkeypatch.setattr(
        gates_mod, "_groq_gate_should_save_general",
        lambda *a, **k: (True, "mocked general approve"),
    )
    passed, score, reason = gates_mod.screen_job_with_ai(
        title, company, description, location, easy_apply=True
    )
    assert passed is True
    assert reason == "mocked general approve"


def test_dispatch_env_isolation_indeed_it_general_and_glassdoor(tmp_path):
    """Safeguard #7 for indeed_it, indeed_general, and glassdoor_it profiles.

    Wave B.1: glassdoor never sets bookmark/verify flags (even if method is
    company_site/unverified — those should not be leased, but env stays clean).
    """
    import scripts.application_worker as aw

    stale = {
        "JOB_QUEUE_VERIFY_APPLY_TYPE": "1",
        "JOB_QUEUE_BOOKMARK_ONLY": "1",
        "JOB_QUEUE_BOOKMARK_FIRST": "0",
        "PATH": "/usr/bin",
    }

    def mk(portal, profile, method, region="METRO_VAN"):
        return {
            "portal": portal,
            "profile": profile,
            "source_job_id": "jk1",
            "title": "T",
            "company": "C",
            "url": "https://example.com/job",
            "description": "d",
            "metadata": {"application_method": method, "region": region},
        }

    cases = [
        # portal, profile, method, expect_only, expect_verify, expect_first
        ("indeed", "it", "easy_apply", None, None, "1"),
        ("indeed", "general", "easy_apply", None, None, "1"),
        ("glassdoor", "it", "easy_apply", None, None, None),
        ("indeed", "it", "company_site", "1", None, "1"),
        ("indeed", "general", "company_site", "1", None, "1"),
        ("glassdoor", "it", "company_site", None, None, None),
        ("indeed", "it", "unverified", None, "1", "1"),
        ("indeed", "general", "unverified", None, "1", "1"),
        ("glassdoor", "it", "unverified", None, None, None),
    ]
    for portal, profile, method, expect_only, expect_verify, expect_first in cases:
        _, env, _ = aw.build_dispatch_env(
            mk(portal, profile, method), tmp_path / f"{portal}_{profile}_{method}.json",
            base_env=stale,
        )
        assert env.get("JOB_QUEUE_BOOKMARK_FIRST") == expect_first
        assert env.get("JOB_QUEUE_BOOKMARK_ONLY") == expect_only
        assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") == expect_verify


# ---------------------------------------------------------------------------
# 11. Wave B.1 — Glassdoor-strict policy + Indeed sync + config
# ---------------------------------------------------------------------------

def _gd_job(
    location,
    *,
    apply_type="EASY_APPLY",
    is_remote_hint=False,
    description="",
    source_refs=None,
    listing_url="https://www.glassdoor.com/job-listing/gd1",
    source_job_id="gd1",
    title="QA Analyst",
    company="Acme",
):
    return NormalizedJob(
        source_platform="glassdoor",
        source_job_id=source_job_id,
        discovery_engine="jobspy",
        query_id="q",
        job_title=title,
        company_name=company,
        location=location,
        description=description,
        date_posted=None,
        listing_url=listing_url,
        destination_url=None,
        apply_type=apply_type,
        is_remote_hint=is_remote_hint,
        source_refs=source_refs or [{"platform": "glassdoor", "job_id": source_job_id}],
    )


def test_glassdoor_strict_metro_any_work_mode_easy_apply():
    """Metro Van + EA → APPLY for on-prem / hybrid / remote."""
    cases = [
        _gd_job("Vancouver, BC"),
        _gd_job("Burnaby, BC", description="Hybrid — 3 days in office"),
        _gd_job("Surrey, BC", description="Fully remote", is_remote_hint=True),
        _gd_job("Richmond, BC", description="Work from home option"),
    ]
    for job in cases:
        d = decide_job_policy(job)
        assert d.action == "APPLY", (job.location, d.reason)
        assert d.application_method == "easy_apply"
        assert d.reason == "glassdoor_metro_easy_apply"


def test_glassdoor_strict_outside_metro_rejected():
    for job in (
        _gd_job("Toronto, ON", is_remote_hint=True),
        _gd_job("Canada", is_remote_hint=True),
        _gd_job("Remote - Canada", is_remote_hint=True),
        _gd_job("Calgary, AB", apply_type="EASY_APPLY"),
    ):
        d = decide_job_policy(job)
        assert d.action == "REJECT"
        assert d.reason == "outside_metro_vancouver_only"


def test_glassdoor_strict_non_ea_rejected():
    # COMPANY_APPLY should be rejected
    d = decide_job_policy(_gd_job("Vancouver, BC", apply_type="COMPANY_APPLY"))
    assert d.action == "REJECT"
    assert d.reason == "glassdoor_company_site_rejected"

    # UNKNOWN: Wave B.1 Easy Apply only — reject (do not queue verify)
    d2 = decide_job_policy(_gd_job("Vancouver, BC", apply_type="UNKNOWN"))
    assert d2.action == "REJECT"
    assert d2.reason == "glassdoor_non_easy_apply"



def _wp_job(
    location: str,
    *,
    apply_type: str = "EASY_APPLY",
    description: str = "IT support role",
    is_remote_hint: bool = False,
    title: str = "IT Support",
    company: str = "Acme",
    source_job_id: str = "wp1",
) -> NormalizedJob:
    return NormalizedJob(
        source_platform="workopolis",
        source_job_id=source_job_id,
        discovery_engine="workopolis_http",
        query_id="q",
        job_title=title,
        company_name=company,
        location=location,
        description=description,
        date_posted=None,
        listing_url=f"https://www.workopolis.com/job/{source_job_id}",
        destination_url=None,
        apply_type=apply_type,
        is_remote_hint=is_remote_hint,
        source_refs=[{"platform": "workopolis", "job_id": source_job_id}],
    )


def test_workopolis_strict_metro_easy_apply():
    d = decide_job_policy(_wp_job("Burnaby, BC"))
    assert d.action == "APPLY"
    assert d.application_method == "easy_apply"
    assert d.reason == "workopolis_metro_easy_apply"


def test_workopolis_strict_outside_metro_rejected():
    d = decide_job_policy(_wp_job("Toronto, ON", is_remote_hint=True))
    assert d.action == "REJECT"
    assert d.reason == "outside_metro_vancouver_only"


def test_workopolis_strict_non_ea_rejected():
    for apply_type in ("COMPANY_APPLY", "UNKNOWN"):
        d = decide_job_policy(_wp_job("Vancouver, BC", apply_type=apply_type))
        assert d.action == "REJECT"
        assert d.reason == "workopolis_non_easy_apply"


def test_indeed_policy_enforces_metro_boundary():
    """Indeed cannot queue an outside-Metro remote Easy Apply job."""
    d = decide_job_policy(_job("Toronto, ON (Remote)", apply_type="EASY_APPLY", is_remote_hint=True))
    assert d.action == "REJECT"
    assert d.reason == "outside_metro_vancouver_only"


def test_deduplicate_indeed_wins_over_glassdoor():
    """Wave B.1: Indeed identity wins even when Glassdoor is listed first."""
    gd = _gd_job(
        "Vancouver, BC",
        title="QA Tester",
        company="KPU",
        listing_url="https://www.glassdoor.com/job-listing/g1",
        source_job_id="g1",
    )
    gd.destination_url = "https://example.com/careers/qa"
    gd.description = "Test software quality assurance."
    ind = NormalizedJob(
        source_platform="indeed",
        source_job_id="ind1",
        discovery_engine="jobspy",
        query_id="q",
        job_title="QA Tester",
        company_name="KPU",
        location="Vancouver, BC",
        description="Test software quality assurance.",
        date_posted=None,
        listing_url="https://ca.indeed.com/viewjob?jk=ind1",
        destination_url="https://example.com/careers/qa",
        apply_type="EASY_APPLY",
        source_refs=[{"platform": "indeed", "job_id": "ind1"}],
    )
    deduped = deduplicate([gd, ind])
    assert len(deduped) == 1
    assert deduped[0].source_platform == "indeed"
    platforms = {r["platform"] for r in deduped[0].source_refs}
    assert "indeed" in platforms and "glassdoor" in platforms


def test_indeed_sync_skips_glassdoor_twin_of_queue_url(tmp_path):
    from core.discovery.indeed_sync import IndeedSyncIndex, glassdoor_already_on_indeed
    from core.job_queue import JobQueue

    q = JobQueue(tmp_path / "q.db")
    q.enqueue(
        portal="indeed", profile="it", source_job_id="ind-twin",
        title="Help Desk Analyst", company="Acme",
        url="https://ca.indeed.com/viewjob?jk=ind-twin",
        location="Vancouver, BC",
    )
    # Soft match: Ltd. suffix + location drift must still hit.
    q.enqueue(
        portal="indeed", profile="it", source_job_id="ind-wcr",
        title="IT Service Desk Analyst", company="West Coast Reduction Ltd.",
        url="https://ca.indeed.com/viewjob?jk=ind-wcr",
        location="Vancouver, BC, CA",
    )
    idx = IndeedSyncIndex(queue=q, history_ids=set(), load_history=False)

    # CTL match against Indeed queue
    gd = _gd_job(
        "Vancouver, BC",
        title="Help Desk Analyst",
        company="Acme",
        source_job_id="gd-twin",
    )
    skip, reason = glassdoor_already_on_indeed(gd, index=idx)
    assert skip is True
    assert reason in {"indeed_queue_ctl", "indeed_queue_ct"}

    gd_soft = _gd_job(
        "Vancouver",
        title="IT Service Desk Analyst",
        company="West Coast Reduction",
        source_job_id="gd-wcr",
    )
    skip_soft, reason_soft = glassdoor_already_on_indeed(gd_soft, index=idx)
    assert skip_soft is True
    assert reason_soft in {"indeed_queue_ctl", "indeed_queue_ct"}

    # source_refs containing Indeed
    gd2 = _gd_job("Vancouver, BC", source_refs=[
        {"platform": "glassdoor", "job_id": "g2"},
        {"platform": "indeed", "job_id": "ind-x"},
    ])
    skip2, reason2 = glassdoor_already_on_indeed(gd2, index=idx)
    assert skip2 is True
    assert reason2 == "indeed_source_ref"
    q.drop_test_database()


def test_indeed_sync_uses_email_applied_history(tmp_path):
    from core.discovery.indeed_sync import IndeedSyncIndex, glassdoor_already_on_indeed
    from core.job_queue import JobQueue

    q = JobQueue(tmp_path / "q-email.db")
    q.db["email_applied_history"].insert_many([
        {
            "company_name": "Unknown",
            "job_title": "Terminal Support Specialist (Tier 1)",
            "subject": "Indeed Application: Terminal Support Specialist (Tier 1)",
            "source_platform": "indeed",
        },
        {
            "company_name": "Dunn Group",
            "job_title": "Unknown",
            "subject": "Thank you for applying at Dunn Group",
            "source_platform": "unknown",
        },
    ])
    idx = IndeedSyncIndex(queue=q, history_ids=set(), load_history=False)

    gct = _gd_job("Delta", title="Terminal Support Specialist (Tier 1)", company="Global Container Terminals")
    skip, reason = glassdoor_already_on_indeed(gct, index=idx)
    assert skip is True
    assert reason == "indeed_email_title"

    dunn = _gd_job("Vancouver", title="IT Systems Administrator", company="Dunn Group")
    skip2, reason2 = glassdoor_already_on_indeed(dunn, index=idx)
    assert skip2 is False
    assert reason2 == ""
    q.drop_test_database()


def test_glassdoor_indeed_sync_skips_before_ai(monkeypatch):
    from core.discovery import planner
    from core.discovery.indeed_sync import IndeedSyncIndex

    calls = {"n": 0}
    monkeypatch.setenv("DISCOVERY_GEO_POLICY", "1")
    monkeypatch.delenv("BYPASS_SCREENING", raising=False)
    monkeypatch.setattr(planner, "_ensure_monorepo_path", lambda: None)

    import core.discovery._gate_adapter as ga

    def fake_screen(**kwargs):
        calls["n"] += 1
        return True, 90, "ok"

    monkeypatch.setattr(ga, "screen_job", fake_screen)
    monkeypatch.setattr(ga, "hard_screen_job", fake_screen)

    idx = IndeedSyncIndex(queue=None, history_ids=set(), load_history=False)
    # Seed soft CT key (company|title) used by Wave B.1 sync
    idx.by_ct.add("acme|help desk analyst")
    idx.by_ctl.add("acme|help desk analyst|vancouver")

    twin = _gd_job(
        "Vancouver, BC", title="Help Desk Analyst", company="Acme",
        source_job_id="gd-sync",
    )
    keep = _gd_job(
        "Burnaby, BC", title="QA Analyst", company="OtherCo",
        source_job_id="gd-keep",
    )
    stats = planner._screen_and_enqueue(
        [twin, keep], "it", dry_run=True, indeed_sync_index=idx,
    )
    assert stats["glassdoor_skipped_indeed_sync"] == 1
    assert stats["ai_screened"] == 1
    assert stats["glassdoor_enqueued_ea"] == 1
    assert calls["n"] == 3


def test_workopolis_sync_skips_indeed_queue_and_email(tmp_path):
    from core.discovery.indeed_sync import IndeedSyncIndex, workopolis_already_on_indeed
    from core.job_queue import JobQueue

    q = JobQueue(tmp_path / "q-wp.db")
    q.enqueue(
        portal="indeed", profile="it", source_job_id="ind-wp",
        title="Help Desk Technician", company="Confidential",
        url="https://ca.indeed.com/viewjob?jk=ind-wp",
        location="Vancouver, BC",
    )
    q.db["email_applied_history"].insert_one({
        "company_name": "Unknown",
        "job_title": "IT Service Desk Specialist (Tier 2)",
        "subject": "Indeed Application: IT Service Desk Specialist (Tier 2)",
        "source_platform": "indeed",
    })
    idx = IndeedSyncIndex(queue=q, history_ids=set(), load_history=False)

    twin = _wp_job(
        "Vancouver, BC",
        title="Help Desk Technician",
        company="Confidential",
        source_job_id="wp-twin",
    )
    skip, reason = workopolis_already_on_indeed(twin, index=idx)
    assert skip is True
    assert reason in {"indeed_queue_ctl", "indeed_queue_ct"}

    email_twin = _wp_job(
        "Surrey, BC",
        title="IT Service Desk Specialist (Tier 2)",
        company="Quicktech",
        source_job_id="wp-email",
    )
    skip2, reason2 = workopolis_already_on_indeed(email_twin, index=idx)
    assert skip2 is True
    assert reason2 == "indeed_email_title"

    keep = _wp_job(
        "Burnaby, BC",
        title="Unique Workopolis Only Role",
        company="Only On Workopolis Inc",
        source_job_id="wp-keep",
    )
    skip3, reason3 = workopolis_already_on_indeed(keep, index=idx)
    assert skip3 is False
    assert reason3 == ""
    q.drop_test_database()


def test_workopolis_sync_skips_glassdoor_applied(tmp_path):
    from core.discovery.indeed_sync import IndeedSyncIndex, workopolis_already_on_indeed
    from core.job_queue import JobQueue

    q = JobQueue(tmp_path / "q-wp-gd.db")
    jid, _ = q.enqueue(
        portal="glassdoor", profile="it", source_job_id="gd-applied",
        title="Systems Admin", company="R Johnson",
        url="https://www.glassdoor.ca/job-listing/x",
        location="Vancouver, BC",
    )
    q.jobs.update_one({"_id": jid}, {"$set": {"status": "applied"}})
    idx = IndeedSyncIndex(queue=q, history_ids=set(), load_history=False)

    twin = _wp_job(
        "Vancouver, BC",
        title="Systems Admin",
        company="R Johnson",
        source_job_id="wp-gd-twin",
    )
    skip, reason = workopolis_already_on_indeed(twin, index=idx)
    assert skip is True
    assert reason in {"glassdoor_applied_ctl", "glassdoor_applied_ct"}
    q.drop_test_database()


def test_workopolis_indeed_sync_skips_before_ai(monkeypatch):
    from core.discovery import planner
    from core.discovery.indeed_sync import IndeedSyncIndex

    calls = {"n": 0}
    monkeypatch.setenv("DISCOVERY_GEO_POLICY", "1")
    monkeypatch.delenv("BYPASS_SCREENING", raising=False)
    monkeypatch.setattr(planner, "_ensure_monorepo_path", lambda: None)

    import core.discovery._gate_adapter as ga

    def fake_screen(**kwargs):
        calls["n"] += 1
        return True, 90, "ok"

    monkeypatch.setattr(ga, "screen_job", fake_screen)
    monkeypatch.setattr(ga, "hard_screen_job", fake_screen)

    idx = IndeedSyncIndex(queue=None, history_ids=set(), load_history=False)
    idx.by_ct.add("acme|help desk analyst")
    idx.by_ctl.add("acme|help desk analyst|vancouver")

    twin = _wp_job(
        "Vancouver, BC", title="Help Desk Analyst", company="Acme",
        source_job_id="wp-sync",
    )
    keep = _wp_job(
        "Burnaby, BC", title="QA Analyst", company="OtherCo",
        source_job_id="wp-keep",
    )
    stats = planner._screen_and_enqueue(
        [twin, keep], "it", dry_run=True, indeed_sync_index=idx,
    )
    assert stats["workopolis_skipped_indeed_sync"] == 1
    assert stats["ai_screened"] == 1
    assert calls["n"] == 3


def test_scrape_proxy_ladder_local_webshare_dataimpulse(monkeypatch):
    from core.discovery.scrape_proxy import (
        ProxyTier,
        ScrapeProxyLadder,
        resolve_proxy_tiers,
    )

    monkeypatch.delenv("JOBSPY_PROXY_WEBSHARE", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_URL", raising=False)
    monkeypatch.delenv("JOBSPY_PROXY_DATAIMPULSE", raising=False)
    monkeypatch.delenv("DATAIMPULSE_PROXY_URL", raising=False)
    monkeypatch.delenv("PROXY_CHEAP_URL", raising=False)
    monkeypatch.delenv("CAPMONSTER_PROXY_URL", raising=False)
    monkeypatch.delenv("JOBSPY_PROXY_URLS", raising=False)
    monkeypatch.delenv("JOBSPY_DISABLE_WEBSHARE", raising=False)
    monkeypatch.delenv("JOBSPY_SKIP_LOCAL", raising=False)
    monkeypatch.setenv("PROXY_URL", "http://u:p@gw.dataimpulse.com:823")
    tiers = resolve_proxy_tiers()
    assert tiers.webshare == ""
    assert "dataimpulse" in tiers.dataimpulse

    ladder = ScrapeProxyLadder(
        tiers=ProxyTier(
            webshare="http://ws:x@proxy.webshare.io:80",
            dataimpulse="http://u:p@gw.dataimpulse.com:823",
        ),
        alternate_every=2,
        step_down_after=3,
    )
    assert ladder.current_proxies() is None
    ladder.note_success()
    ladder.note_success()
    assert ladder.current_label() == "webshare"
    assert ladder.current_proxies() == ["http://ws:x@proxy.webshare.io:80"]
    ladder.note_success()
    ladder.note_success()
    assert ladder.current_label() == "local"

    assert ladder.note_failure("Glassdoor: bad response status code: 429") is True
    assert ladder.current_label() == "webshare"
    assert ladder.note_failure("429 Too Many Requests") is True
    assert ladder.current_label() == "dataimpulse"

    # Absent webshare: local → dataimpulse directly
    ladder2 = ScrapeProxyLadder(
        tiers=ProxyTier(dataimpulse="http://u:p@gw.dataimpulse.com:823"),
    )
    assert ladder2.note_failure("blocked by glassdoor") is True
    assert ladder2.current_label() == "dataimpulse"


def test_scrape_proxy_blacklists_407_and_skips_local(monkeypatch):
    from core.discovery.scrape_proxy import ProxyTier, ScrapeProxyLadder

    ladder = ScrapeProxyLadder(
        tiers=ProxyTier(
            webshare="http://ws:x@proxy.webshare.io:80",
            dataimpulse="http://u:p@thehub.proxy-cheap.com:8080",
        ),
        alternate_every=2,
        step_down_after=2,
        skip_local=True,
    )
    assert ladder.current_label() == "webshare"
    # Dead webshare credential — permanent disable + escalate to residential.
    assert ladder.note_failure(
        "Tunnel connection failed: 407 Proxy Authentication Required"
    ) is True
    assert ladder.current_label() == "dataimpulse"
    assert "webshare" in ladder._blacklisted
    assert ladder.tiers.webshare == ""
    # Success must NOT step back onto blacklisted webshare.
    ladder.note_success()
    ladder.note_success()
    assert ladder.current_label() == "dataimpulse"

    # JOBSPY_DISABLE_WEBSHARE
    monkeypatch.setenv("JOBSPY_DISABLE_WEBSHARE", "1")
    monkeypatch.setenv("JOBSPY_PROXY_WEBSHARE", "http://ws:x@proxy.webshare.io:80")
    monkeypatch.setenv("PROXY_URL", "http://u:p@thehub.proxy-cheap.com:8080")
    monkeypatch.delenv("JOBSPY_PROXY_DATAIMPULSE", raising=False)
    monkeypatch.delenv("DATAIMPULSE_PROXY_URL", raising=False)
    from core.discovery.scrape_proxy import resolve_proxy_tiers

    tiers = resolve_proxy_tiers()
    assert tiers.webshare == ""
    assert "proxy-cheap" in tiers.dataimpulse


def test_glassdoor_config_metro_only_no_remote_empty():
    from core.discovery.planner import _load_search_locations, _load_search_policy
    from core.discovery.providers.jobspy_provider import normalize_glassdoor_location

    locs = _load_search_locations("it", ["glassdoor"])
    assert locs
    assert "" not in locs
    assert all((loc or "").strip() for loc in locs)
    assert all((loc or "").strip().lower() != "remote" for loc in locs)
    # Glassdoor locationAjax must not get "City, BC" / bare Richmond.
    for loc in locs:
        norm = normalize_glassdoor_location(loc)
        assert ", bc" not in norm.lower()
        assert norm.lower() != "richmond"

    assert normalize_glassdoor_location("Vancouver, BC") == "Vancouver"
    assert normalize_glassdoor_location("Richmond, BC") == "Richmond%20BC"
    assert normalize_glassdoor_location("White Rock") == "White%20Rock%20BC"
    assert normalize_glassdoor_location("Richmond BC") == "Richmond%20BC"

    policy = _load_search_policy("it", ["glassdoor"])
    assert policy.get("easy_apply_only") is True

    # Indeed path still has locations
    indeed_locs = _load_search_locations("it", ["indeed"])
    assert indeed_locs


def test_glassdoor_dispatch_refuses_non_easy_apply(tmp_path):
    import scripts.application_worker as aw

    job = {
        "portal": "glassdoor",
        "profile": "it",
        "source_job_id": "gd1",
        "title": "T",
        "company": "C",
        "url": "https://www.glassdoor.com/job",
        "description": "d",
        "metadata": {"application_method": "company_site", "region": "METRO_VAN"},
    }
    method, env, _ = aw.build_dispatch_env(job, tmp_path / "r.json")
    assert method == "company_site"
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") is None
    assert env.get("JOB_QUEUE_BOOKMARK_FIRST") is None
    assert env.get("JOB_QUEUE_VERIFY_APPLY_TYPE") is None

    code, err = aw.dispatch(job, tmp_path / "r2.json")
    assert code == 2
    assert "glassdoor_non_easy_apply_refused" in err


def test_gate_adapter_sets_job_profile_for_general(monkeypatch):
    """discovery gate adapter must set JOB_PROFILE from the profile argument."""
    import os
    from core.discovery import _gate_adapter as ga

    seen = {}

    def fake_screen(*args, **kwargs):
        seen["JOB_PROFILE"] = os.environ.get("JOB_PROFILE")
        return True, 90, "ok"

    monkeypatch.setattr(ga, "_screen_fn", fake_screen)
    monkeypatch.setattr(ga, "_ensure_gate_loaded", lambda: None)

    ga.screen_job(
        title="Receptionist", company="Acme", description="front desk",
        location="Vancouver, BC", easy_apply=True, profile="general",
    )
    assert seen["JOB_PROFILE"] == "GENERAL"

    ga.screen_job(
        title="QA Analyst", company="Acme", description="testing",
        location="Vancouver, BC", easy_apply=True, profile="it",
    )
    assert seen["JOB_PROFILE"] == "IT"


def test_glassdoor_cdp_provider_pagination_and_registration():
    from core.discovery.providers.glassdoor_cdp_provider import get_page_url, GlassdoorCDPProvider
    from core.discovery.planner import _build_providers, _engine_for_platform

    # Verify pagination formatting
    base_slug_url = "https://www.glassdoor.ca/Job/vancouver-bc-qa-analyst-jobs-SRCH_IL.0,12_IM1011_KO13,23.htm"
    assert get_page_url(base_slug_url, 1) == base_slug_url
    assert get_page_url(base_slug_url, 2) == "https://www.glassdoor.ca/Job/vancouver-bc-qa-analyst-jobs-SRCH_IL.0,12_IM1011_KO13,23_IP2.htm"
    assert get_page_url(base_slug_url, 3) == "https://www.glassdoor.ca/Job/vancouver-bc-qa-analyst-jobs-SRCH_IL.0,12_IM1011_KO13,23_IP3.htm"

    base_query_url = "https://www.glassdoor.ca/Job/jobs.htm?sc.keyword=QA+Analyst"
    assert get_page_url(base_query_url, 1) == base_query_url
    url_p2 = get_page_url(base_query_url, 2)
    assert "jobs_IP2.htm" in url_p2

    base_no_htm_url = "https://www.glassdoor.ca/Job/jobs?sc.keyword=QA+Analyst"
    assert get_page_url(base_no_htm_url, 1) == base_no_htm_url
    url_p2_no_htm = get_page_url(base_no_htm_url, 2)
    assert "p=2" in url_p2_no_htm

    # Verify planner registration
    providers = _build_providers(["glassdoor"])
    assert len(providers) == 1
    assert isinstance(providers[0], GlassdoorCDPProvider)

    # Verify platform to engine mapping
    assert _engine_for_platform("glassdoor", {"glassdoor_cdp": 5}) == "glassdoor_cdp"


def test_google_cdp_ats_helpers_and_registration():
    from core.discovery.providers.google_cdp_provider import (
        GoogleCDPProvider,
        build_google_web_ats_query,
        extract_ats_urls,
        is_greenhouse_or_lever,
        canonicalize_ats_url,
        serp_passes_metro_van_canada,
        serp_title_matches_search_intent,
    )
    from core.discovery.planner import _build_providers, _engine_for_platform, _load_search_locations

    assert is_greenhouse_or_lever("https://boards.greenhouse.io/acme/jobs/1")
    assert is_greenhouse_or_lever("https://jobs.lever.co/acme/abc")
    assert not is_greenhouse_or_lever("https://www.linkedin.com/jobs/view/1")

    urls = extract_ats_urls(
        "see https://jobs.lever.co/acme/abc123 and also "
        "https://boards.greenhouse.io/stripe/jobs/99?gh_src=x"
    )
    assert "https://jobs.lever.co/acme/abc123" in urls
    assert canonicalize_ats_url("https://boards.greenhouse.io/stripe/jobs/99?gh_src=x") == (
        "https://boards.greenhouse.io/stripe/jobs/99"
    )

    q = build_google_web_ats_query("QA Analyst", "Vancouver, BC")
    assert "OR Remote" not in q
    assert "Canada" in q
    assert '"Vancouver, BC"' in q
    assert '-"United States"' in q or "-USA" in q
    assert "job-boards.greenhouse.io" in q
    assert "boards.greenhouse.io" in q
    assert "jobs.lever.co" in q

    from core.discovery.providers.google_cdp_provider import build_ats_query_variants

    variants = build_ats_query_variants("IT Support", "Vancouver, BC")
    assert len(variants) >= 2
    assert any("Remote" in v and "Canada" in v for v in variants)

    assert serp_passes_metro_van_canada(
        title="QA Analyst - Vancouver, BC",
        snippet="Greenhouse",
    )
    assert not serp_passes_metro_van_canada(
        title="Senior Software Engineer - San Francisco",
        snippet="Remote USA",
    )
    # Bare title (common SERP shape): keep for Phase-I policy after geo-constrained dork
    assert serp_passes_metro_van_canada(
        title="IT Support Analyst",
        snippet="Full-time · Apply on Greenhouse",
    )
    assert serp_title_matches_search_intent(
        title="IT Support Analyst", search_term="IT Support"
    )
    assert not serp_title_matches_search_intent(
        title="Senior Software Engineer, Payment Platform",
        search_term="IT Support",
    )

    providers = _build_providers(["google"])
    assert len(providers) == 1
    assert isinstance(providers[0], GoogleCDPProvider)
    assert _engine_for_platform("google", {"google_cdp": 1}) == "google_cdp"

    # Google must stay opt-in (not in default portal set)
    default_names = [p.name for p in _build_providers(None)]
    assert "google_cdp" not in default_names

    google_locs = _load_search_locations("it", portals=["google"])
    assert google_locs
    # Single region anchor (query expands metro pack) — not full city fan-out
    assert len(google_locs) == 1
    assert all((loc or "").strip().lower() != "remote" for loc in google_locs)
    assert "vancouver" in google_locs[0].lower()


# ---------------------------------------------------------------------------
# LinkedIn main-terms + batched enqueue
# ---------------------------------------------------------------------------

def test_linkedin_search_terms_are_main_subset():
    from core.discovery.planner import _load_linkedin_search_terms, _load_search_terms

    full = _load_search_terms("it")
    linkedin = _load_linkedin_search_terms("it")
    assert linkedin, "IT linkedin_search_terms must load"
    assert full, "IT search terms must load"
    # IT helpdesk / support LinkedIn list (LINKEDIN_HERO_TERMS) — no CSR/admin.
    # Office/CS is a second pass on the sole linkedin_general bot (profile=general).
    assert len(linkedin) <= 100
    blob = " | ".join(linkedin).lower()
    assert "qa analyst" in blob
    assert "it support" in blob or "help desk" in blob
    assert "systems administrator" in blob
    # CSR / front-desk / admin terms stay off LinkedIn *IT* keyword pass
    assert "customer service" not in blob
    assert "administrative assistant" not in blob
    assert "receptionist" not in blob
    assert "data entry" not in blob


def test_general_hero_terms_are_office_cs_only():
    from config.general.hero_terms import (
        HERO_SEARCH_TERMS,
        LINKEDIN_HERO_TERMS,
        LINKEDIN_OFFICE_TERMS,
    )

    # Indeed general: short office/CS only
    assert 10 <= len(HERO_SEARCH_TERMS) <= 25
    blob = " | ".join(HERO_SEARCH_TERMS).lower()
    assert "customer service" in blob
    assert "receptionist" in blob or "administrative assistant" in blob
    assert "systems administrator" not in blob
    assert "software engineer" not in blob
    assert LINKEDIN_OFFICE_TERMS == HERO_SEARCH_TERMS
    # Sole LinkedIn bot: IT heroes ∪ office/CS
    assert len(LINKEDIN_HERO_TERMS) > len(HERO_SEARCH_TERMS)
    li_blob = " | ".join(LINKEDIN_HERO_TERMS).lower()
    assert "customer service" in li_blob
    assert "it support" in li_blob or "help desk" in li_blob or "qa analyst" in li_blob


def test_linkedin_batches_enqueue_each_chunk(monkeypatch):
    """Sequential LinkedIn mode screens/enqueues after each term batch."""
    from core.discovery import planner as planner_mod
    from core.discovery.providers.base import DiscoveryRequest

    monkeypatch.setenv("LINKEDIN_DISCOVERY_SEQUENTIAL", "1")
    monkeypatch.setenv("LINKEDIN_DISCOVERY_TERM_BATCH", "2")
    monkeypatch.setattr(
        planner_mod,
        "_load_linkedin_search_terms",
        lambda profile: ["QA Analyst", "IT Support", "Data Analyst", "Help Desk Analyst"],
    )
    monkeypatch.setattr(planner_mod, "_load_search_terms", lambda profile: ["FULL_TERM_A"] * 50)
    monkeypatch.setattr(planner_mod, "_load_search_locations", lambda profile, portals=None: ["Vancouver, BC"])
    monkeypatch.setattr(
        planner_mod,
        "_load_search_policy",
        lambda profile, portals=None: {
            "radius_km": 25,
            "easy_apply_only": False,
            "job_types": [],
            "experience_levels": [],
            "workplace_types": [],
        },
    )

    discover_calls: list[list[str]] = []

    class FakeLinkedInJobSpy:
        name = "jobspy_linkedin"
        portals = ["linkedin"]
        supported_platforms = ["linkedin"]

        def discover(self, request: DiscoveryRequest):
            discover_calls.append(list(request.search_terms))
            term = request.search_terms[0]
            return [
                RawJob(
                    source_platform="linkedin",
                    source_job_id=f"li-{term}-{len(discover_calls)}",
                    title=term,
                    company="Acme",
                    location="Vancouver, BC",
                    description="IT support and QA work in Vancouver.",
                    listing_url=f"https://www.linkedin.com/jobs/view/{len(discover_calls)}",
                    raw_extras={"search_term": term},
                )
            ]

    monkeypatch.setattr(
        planner_mod,
        "_build_providers",
        lambda portals: [FakeLinkedInJobSpy()],
    )

    enqueue_calls: list[int] = []

    def fake_screen(jobs, profile, *, dry_run=False, indeed_sync_index=None):
        enqueue_calls.append(len(jobs))
        return {
            "screened": len(jobs),
            "passed": len(jobs),
            "rejected": 0,
            "enqueued": 0 if dry_run else len(jobs),
            "new": len(jobs),
        }

    monkeypatch.setattr(planner_mod, "_screen_and_enqueue", fake_screen)

    result = planner_mod.run_discovery(
        profile="it",
        portals=["linkedin"],
        dry_run=True,
        max_results_per_term=5,
    )

    assert discover_calls == [
        ["QA Analyst", "IT Support"],
        ["Data Analyst", "Help Desk Analyst"],
    ]
    assert len(enqueue_calls) == 2
    assert result["linkedin_terms"] == 4
    assert result["linkedin_batches"] == 2
    assert result["linkedin_sequential"] is True
    assert result["raw_count"] == 2


def test_linkedin_parallel_single_discover(monkeypatch):
    """Default LinkedIn path runs all main terms in one discover (parallel pool)."""
    from core.discovery import planner as planner_mod
    from core.discovery.providers.base import DiscoveryRequest

    monkeypatch.delenv("LINKEDIN_DISCOVERY_SEQUENTIAL", raising=False)
    monkeypatch.setenv("LINKEDIN_DISCOVERY_SEQUENTIAL", "0")
    monkeypatch.setenv("LINKEDIN_DISCOVERY_TERM_BATCH", "2")
    monkeypatch.setattr(
        planner_mod,
        "_load_linkedin_search_terms",
        lambda profile: ["QA Analyst", "IT Support", "Data Analyst", "Help Desk Analyst"],
    )
    monkeypatch.setattr(planner_mod, "_load_search_terms", lambda profile: ["FULL_TERM_A"] * 50)
    monkeypatch.setattr(planner_mod, "_load_search_locations", lambda profile, portals=None: ["Vancouver, BC"])
    monkeypatch.setattr(
        planner_mod,
        "_load_search_policy",
        lambda profile, portals=None: {
            "radius_km": 25,
            "easy_apply_only": False,
            "job_types": [],
            "experience_levels": [],
            "workplace_types": [],
        },
    )

    discover_calls: list[list[str]] = []

    class FakeLinkedInJobSpy:
        name = "jobspy_linkedin"
        portals = ["linkedin"]
        supported_platforms = ["linkedin"]

        def discover(self, request: DiscoveryRequest):
            discover_calls.append(list(request.search_terms))
            return [
                RawJob(
                    source_platform="linkedin",
                    source_job_id=f"li-{i}",
                    title=t,
                    company="Acme",
                    location="Vancouver, BC",
                    description="IT support and QA work in Vancouver.",
                    listing_url=f"https://www.linkedin.com/jobs/view/{i}",
                    raw_extras={"search_term": t},
                )
                for i, t in enumerate(request.search_terms)
            ]

    monkeypatch.setattr(
        planner_mod,
        "_build_providers",
        lambda portals: [FakeLinkedInJobSpy()],
    )
    monkeypatch.setattr(
        planner_mod,
        "_screen_and_enqueue",
        lambda jobs, profile, *, dry_run=False, indeed_sync_index=None: {
            "screened": len(jobs), "passed": len(jobs), "rejected": 0,
            "enqueued": 0 if dry_run else len(jobs), "new": len(jobs),
        },
    )

    result = planner_mod.run_discovery(
        profile="it",
        portals=["linkedin"],
        dry_run=True,
        max_results_per_term=5,
    )

    assert discover_calls == [
        ["QA Analyst", "IT Support", "Data Analyst", "Help Desk Analyst"],
    ]
    assert result["linkedin_terms"] == 4
    assert result["linkedin_sequential"] is False
    assert result["raw_count"] == 4


def test_indeed_still_receives_full_search_terms(monkeypatch):
    """Non-LinkedIn portals must keep the full profile search_terms list."""
    from core.discovery import planner as planner_mod
    from core.discovery.providers.base import DiscoveryRequest

    full_terms = [f"Term {i}" for i in range(12)]
    monkeypatch.setattr(planner_mod, "_load_search_terms", lambda profile: list(full_terms))
    monkeypatch.setattr(
        planner_mod,
        "_load_linkedin_search_terms",
        lambda profile: ["QA Analyst", "IT Support"],
    )
    monkeypatch.setattr(planner_mod, "_load_search_locations", lambda profile, portals=None: ["Vancouver, BC"])
    monkeypatch.setattr(
        planner_mod,
        "_load_search_policy",
        lambda profile, portals=None: {
            "radius_km": 25,
            "easy_apply_only": False,
            "job_types": [],
            "experience_levels": [],
            "workplace_types": [],
        },
    )

    seen_terms: list[list[str]] = []

    class FakeIndeed:
        name = "jobspy_indeed"
        portals = ["indeed"]
        supported_platforms = ["indeed"]

        def discover(self, request: DiscoveryRequest):
            seen_terms.append(list(request.search_terms))
            return []

    monkeypatch.setattr(planner_mod, "_build_providers", lambda portals: [FakeIndeed()])
    monkeypatch.setattr(
        planner_mod,
        "_screen_and_enqueue",
        lambda *a, **k: {"screened": 0, "passed": 0, "rejected": 0, "enqueued": 0, "new": 0},
    )

    planner_mod.run_discovery(profile="it", portals=["indeed"], dry_run=True)
    assert seen_terms == [full_terms]


def test_linkedin_engine_is_jobspy():
    from core.discovery.planner import _engine_for_platform

    assert _engine_for_platform("linkedin", {"jobspy_linkedin": 3}) == "jobspy"


def test_jobspy_provider_names_are_portal_specific():
    from core.discovery.providers.jobspy_provider import JobSpyProvider
    from core.discovery.planner import _build_providers

    assert JobSpyProvider(portals=["linkedin"]).name == "jobspy_linkedin"
    assert JobSpyProvider(portals=["indeed"]).name == "jobspy_indeed"
    names = [p.name for p in _build_providers(["indeed", "linkedin"])]
    assert "jobspy_indeed" in names
    assert "jobspy_linkedin" in names


def test_discovery_hard_screen_mirrors_indeed_easy_apply_seniority():
    """Phase I discovery Easy Apply must reject senior/lead/director like Indeed."""
    from core.discovery._gate_adapter import hard_screen_job

    seniors = [
        "Director, Cyber Security Operations",
        "Lead Data Engineer",
        "Sr. Data Analyst - Tableau",
        "Senior System Administrator",
        "QA Manager",
    ]
    for title in seniors:
        passed, score, reason = hard_screen_job(
            title=title,
            company="Acme",
            description="",
            location="Vancouver, BC",
            easy_apply=True,
        )
        assert passed is False, f"{title!r} should fail Easy Apply Phase I: {reason}"
        assert score == 0
        assert "senior" in reason.lower() or "lead" in reason.lower() or "management" in reason.lower()

    juniors = [
        "Junior QA Analyst",
        "IT Support Specialist",
        "Help Desk Analyst",
        "QA Engineer",
        "Systems Administrator",
    ]
    for title in juniors:
        passed, score, reason = hard_screen_job(
            title=title,
            company="Acme",
            description="Provide desktop and ticket support in Metro Vancouver.",
            location="Vancouver, BC",
            easy_apply=True,
        )
        assert passed is True, f"{title!r} should pass Easy Apply Phase I: {reason}"
        assert score == 100


def test_general_discovery_uses_office_customer_service_gate_not_it_gate():
    """General discovery must queue its configured customer-service targets."""
    from core.discovery._gate_adapter import hard_screen_job

    for title in (
        "Customer Service Representative",
        "Administrative Assistant",
        "Front Desk Receptionist",
        "Data Entry Clerk",
    ):
        passed, score, reason = hard_screen_job(
            title=title,
            company="Acme",
            description="Entry-level Metro Vancouver Easy Apply role.",
            location="Vancouver, BC",
            easy_apply=True,
            profile="general",
        )
        assert passed is True, f"{title!r} should pass General Phase I: {reason}"
        assert score == 100

    passed, score, reason = hard_screen_job(
        title="Customer Service Manager",
        company="Acme",
        description="Manage a team.",
        location="Vancouver, BC",
        easy_apply=True,
        profile="general",
    )
    assert passed is False
    assert score == 0
    assert "senior" in reason.lower() or "management" in reason.lower()


def test_discovery_hard_screen_company_site_rejects_senior_titles():
    """VERIFY / company-site Phase I also applies Indeed save-path seniority."""
    from core.discovery._gate_adapter import hard_screen_job

    passed, _, reason = hard_screen_job(
        title="Senior QA Automation Engineer",
        company="EA",
        description="",
        location="Vancouver, BC",
        easy_apply=False,
    )
    assert passed is False
    assert "senior" in reason.lower() or "management" in reason.lower()

    passed, _, reason = hard_screen_job(
        title="IT Support Specialist",
        company="Acme",
        description="Help desk and desktop support. 1-2 years experience preferred.",
        location="Vancouver, BC",
        easy_apply=False,
    )
    assert passed is True, reason


def test_ambiguous_title_tagged_for_batch_not_hard_non_it():
    """Unsure EA titles get ambiguous_title; obvious non-IT stays hard reject."""
    from core.discovery._gate_adapter import hard_screen_job, is_ambiguous_title_reason

    # Unsure (not obvious non-IT, not clear IT phrase) → batch title path
    passed, score, reason = hard_screen_job(
        title="Platform Coordinator",
        company="Acme",
        description="Coordinate day to day platform workflows.",
        location="Vancouver, BC",
        easy_apply=True,
    )
    assert passed is False
    assert is_ambiguous_title_reason(reason), reason

    # Obvious non-IT → hard reject, never batch
    passed2, _, reason2 = hard_screen_job(
        title="Barista",
        company="Cafe",
        description="Make coffee and serve customers.",
        location="Vancouver, BC",
        easy_apply=True,
    )
    assert passed2 is False
    assert not is_ambiguous_title_reason(reason2), reason2


def test_general_hard_gate_rejects_floor_retail_and_invalid_company():
    """General farm should keep office CSR, drop cashier/dental/nan company."""
    from core.discovery._gate_adapter import hard_screen_job

    ok, _, reason = hard_screen_job(
        title="Customer Service Representative",
        company="Aviso",
        description="Answer inbound client calls.",
        location="Vancouver, BC",
        easy_apply=True,
        profile="general",
    )
    assert ok is True, reason

    for title in (
        "Cashier and Customer Service",
        "Dental Receptionist",
        "Medical Office Assistant",
        "Retail Associate",
    ):
        passed, _, r = hard_screen_job(
            title=title,
            company="Store",
            description="",
            location="Surrey, BC",
            easy_apply=True,
            profile="general",
        )
        assert passed is False, f"{title} should be rejected: {r}"
        assert "floor retail" in r.lower() or "clinical" in r.lower() or "trades" in r.lower(), r

    bad_co, _, r2 = hard_screen_job(
        title="Receptionist",
        company="nan",
        description="",
        location="Vancouver, BC",
        easy_apply=True,
        profile="general",
    )
    assert bad_co is False
    assert "invalid_company" in r2


def test_workopolis_dispatch_refuses_non_easy_apply(tmp_path):
    import scripts.application_worker as aw

    job = {
        "portal": "workopolis",
        "profile": "it",
        "source_job_id": "wp1",
        "title": "T",
        "company": "C",
        "url": "https://www.workopolis.com/job",
        "description": "d",
        "metadata": {"application_method": "company_site", "region": "METRO_VAN"},
    }
    method, env, _ = aw.build_dispatch_env(job, tmp_path / "r.json")
    assert method == "company_site"
    assert env.get("JOB_QUEUE_BOOKMARK_ONLY") is None

    code, err = aw.dispatch(job, tmp_path / "r2.json")
    assert code == 2
    assert "workopolis_non_easy_apply_refused" in err


def test_email_title_containment_match(tmp_path):
    """Email 'ERP Administrator' must NOT block different title 'IT and ERP Administrator'."""
    from core.discovery.indeed_sync import IndeedSyncIndex, workopolis_already_on_indeed
    from core.job_queue import JobQueue

    q = JobQueue(tmp_path / "q-email-contain.db")
    q.db["email_applied_history"].insert_one({
        "company_name": "Unknown",
        "job_title": "ERP Administrator",
        "subject": "Indeed Application: ERP Administrator",
        "source_platform": "indeed",
    })
    idx = IndeedSyncIndex(queue=q, history_ids=set(), load_history=False)
    job = _wp_job("Surrey", title="IT and ERP Administrator", company="KASA SUPPLY LTD.")
    skip, reason = workopolis_already_on_indeed(job, index=idx)
    assert skip is False
    assert reason == ""
    q.drop_test_database()


def test_email_refresh_can_be_disabled(monkeypatch):
    from core.discovery import email_history_refresh as ehr

    monkeypatch.setenv("DISCOVERY_REFRESH_EMAIL_HISTORY", "0")
    stats = ehr.refresh_email_applied_history()
    assert stats.get("skipped") is True
    assert stats.get("reason") == "disabled"


def test_bridge_honors_batch_title_approval(tmp_path, monkeypatch):
    """Phase-I batch title PROCEED must not be dropped by bridge re-hard-gate."""
    import uuid
    from core.shared_modules.job_queue_bridge import enqueue_approved_job

    # Point JobQueue at isolated DB for this test
    monkeypatch.setenv("JOBBOTS_MONGO_DATABASE", f"jobbots_test_bridge_{uuid.uuid4().hex[:12]}")
    jid = f"ambig-{uuid.uuid4().hex[:10]}"
    qid, created = enqueue_approved_job(
        portal="indeed",
        profile="it",
        job_id=jid,
        title="Operations Analyst",
        company="Acme Tech",
        location="Vancouver, BC",
        url=f"https://indeed.com/viewjob?jk={jid}",
        description="Coordinate workflows.",
        gate_score=85,
        gate_reason="batch AI title approval: looks like IT-adjacent ops tooling",
        application_method="easy_apply",
        region="METRO_VAN",
        company_ai_approved=True,
    )
    # Without phase1_ai_ok, hard_screen would reject this title and return (None, False).
    assert qid is not None
    assert created is True


def test_linkedin_hybrid_runner_location_and_consent_clearing_rules():
    """Verify hybrid_runner.js and hybrid_heuristics.js preserve LinkedIn location input clearing & consent picking."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    runner_path = root / "legacy" / "linkedin-ai-auto-apply-source" / "hybrid_runner.js"
    heuristics_path = root / "legacy" / "linkedin-ai-auto-apply-source" / "hybrid_heuristics.js"

    if not runner_path.is_file():
        pytest.skip("retired linkedin runner source not present in open-source release")

    runner_content = runner_path.read_text(encoding="utf-8")
    heuristics_content = heuristics_path.read_text(encoding="utf-8")

    # Assert robust location input clearing logic present in hybrid_runner.js
    assert "valueSetter" in runner_content, "hybrid_runner.js must contain valueSetter for location input clearing"
    assert "input.dispatchEvent(new Event('input'" in runner_content, "hybrid_runner.js must dispatch input events for location typeahead"
    assert "pickConsentOption" in runner_content, "hybrid_runner.js must use pickConsentOption heuristic"

    # Assert consent heuristics present in hybrid_heuristics.js
    assert "pickConsentOption" in heuristics_content, "hybrid_heuristics.js must define pickConsentOption"
