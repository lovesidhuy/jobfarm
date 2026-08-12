"""Offline contract checks for Job Bank's authenticated Direct Apply lane."""
from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"
for _path in (str(_REPO), str(_MONOREPO)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def test_direct_apply_urls_and_source_ids_are_stable():
    from jobbots.core.jobbank_direct_apply import direct_apply_url, source_job_id

    posting = "https://www.jobbank.gc.ca/jobsearch/jobposting/50007533"
    assert source_job_id(posting) == "50007533"
    assert source_job_id("/jobsearch/directapply/50007533") == "50007533"
    assert direct_apply_url(posting) == "https://www.jobbank.gc.ca/jobsearch/directapply/50007533"
    assert direct_apply_url(posting, "/jobsearch/directapply/50007533") == (
        "https://www.jobbank.gc.ca/jobsearch/directapply/50007533"
    )


def test_already_applied_markers_are_detected():
    from jobbots.core.jobbank_direct_apply import application_already_submitted_text

    assert application_already_submitted_text(
        "You have successfully applied for this job through Job Bank!"
    )
    assert application_already_submitted_text(
        "You were previously matched to this job, and you marked it as applied."
    )
    assert not application_already_submitted_text("How to apply: By Direct Apply")


def test_direct_apply_is_not_misrouted_to_smtp():
    import scripts.application_worker as worker

    assert not worker._is_email_portal("jobbank", "email")
    assert not worker._is_email_portal("jobbank", "direct_apply")
    assert worker._is_jobbank_direct_apply("jobbank", "direct_apply")
    assert worker._is_jobbank_direct_apply("job_bank", "jobbank_direct_apply")


def test_nst_proxy_payload_keeps_webshare_credentials_structured():
    from jobbots.core.browser.nst_proxy import nst_proxy_payload, safe_proxy_host

    payload = nst_proxy_payload("http://user:pass@72.1.132.207:8099")
    assert payload["host"] == "72.1.132.207"
    assert payload["port"] == "8099"
    assert payload["username"] == "user"
    assert payload["password"] == "pass"
    assert payload["proxySetting"] == payload["proxyType"] == "custom"
    assert safe_proxy_host(payload["url"]) == "72.1.132.207:8099"


def test_direct_apply_is_disabled_before_any_browser_is_opened(monkeypatch):
    from jobbots.core.jobbank_direct_apply import apply_jobbank_direct_queue_job

    monkeypatch.delenv("JOBBANK_DIRECT_APPLY_ENABLED", raising=False)
    ok, reason, evidence = apply_jobbank_direct_queue_job(
        {"url": "https://www.jobbank.gc.ca/jobsearch/jobposting/50007533", "metadata": {}},
    )
    assert (ok, reason, evidence) == (False, "jobbank_direct_apply_disabled", "")
