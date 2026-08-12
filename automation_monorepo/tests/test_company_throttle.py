"""Unit tests for company application throttle and lead deduplication."""
from __future__ import annotations

import uuid
import pytest
from core.shared_modules.company_throttle import (
    check_company_throttle_and_dedupe,
    is_unknown_company,
)


def test_is_unknown_company():
    assert is_unknown_company("") is True
    assert is_unknown_company("Unknown") is True
    assert is_unknown_company("N/A") is True
    assert is_unknown_company("Tailscale") is False
    assert is_unknown_company("Alexander College") is False


def test_exact_title_company_dedupe(monkeypatch):
    from core.job_queue import JobQueue

    monkeypatch.setenv("JOBBOTS_MONGO_DATABASE", f"jobbots_test_throttle_{uuid.uuid4().hex[:10]}")
    q = JobQueue()

    # Enqueue and complete a job
    jid1, _ = q.enqueue(
        portal="greenhouse",
        profile="it",
        source_job_id="tailscale-101",
        title="Software Engineer, Strategic Projects",
        company="Tailscale",
        url="https://job-boards.greenhouse.io/tailscale/jobs/101",
    )
    # Mark as applied
    q.claim(worker="test_worker")
    q.complete(jid1, lease_owner=q.jobs.find_one({"_id": jid1})["lease_owner"], result_url="https://job-boards.greenhouse.io/tailscale/jobs/101")

    # Now attempt to check exact duplicate job
    duplicate_job = {
        "id": "tailscale-101-dupe",
        "company": "Tailscale",
        "title": "Software Engineer, Strategic Projects",
        "url": "https://job-boards.greenhouse.io/tailscale/jobs/101",
    }
    action, reason = check_company_throttle_and_dedupe(q, duplicate_job)
    assert action == "already_applied"
    assert "dedupe" in reason

    q.drop_test_database()


def test_company_rate_limit_throttle(monkeypatch):
    from core.job_queue import JobQueue

    monkeypatch.setenv("JOBBOTS_MONGO_DATABASE", f"jobbots_test_throttle_{uuid.uuid4().hex[:10]}")
    monkeypatch.setenv("MAX_APPLICATIONS_PER_COMPANY", "1")
    monkeypatch.setenv("COMPANY_COOLDOWN_DAYS", "14")
    monkeypatch.setattr(
        "core.shared_modules.company_throttle.load_email_applied_index",
        lambda **kw: type("DummyEmailIndex", (), {"match_reason": lambda self, c, t: None})(),
    )

    q = JobQueue()

    # Enqueue and complete 1 application to Tailscale
    jid1, _ = q.enqueue(
        portal="greenhouse",
        profile="it",
        source_job_id="tailscale-101",
        title="Software Engineer, Strategic Projects",
        company="Tailscale",
        url="https://job-boards.greenhouse.io/tailscale/jobs/101",
    )
    q.claim(worker="test_worker")
    q.complete(jid1, lease_owner=q.jobs.find_one({"_id": jid1})["lease_owner"])

    # Now check a 2nd DIFFERENT job at Tailscale
    new_role_job = {
        "id": "tailscale-102-new",
        "company": "Tailscale Inc.",
        "title": "Infrastructure Engineer",
        "url": "https://job-boards.greenhouse.io/tailscale/jobs/102",
    }
    action, reason = check_company_throttle_and_dedupe(q, new_role_job)
    assert action == "skipped"
    assert "company_rate_limit" in reason
    assert "Tailscale" in reason

    q.drop_test_database()
