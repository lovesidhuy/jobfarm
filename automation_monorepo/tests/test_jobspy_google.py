from __future__ import annotations

import pandas as pd

from core.discovery.providers.jobspy_provider import JobSpyProvider


def _row(**overrides):
    values = {
        "site": "google",
        "id": "google-1",
        "title": "IT Support Analyst",
        "company": "Example Co",
        "location": "Vancouver, BC",
        "description": "Support internal systems.",
        "job_url": "https://www.google.com/search?q=jobs",
        "job_url_direct": "https://boards.greenhouse.io/example/jobs/123",
        "date_posted": None,
        "easy_apply": False,
        "is_remote": False,
    }
    values.update(overrides)
    return pd.Series(values)


def test_google_jobspy_row_preserves_greenhouse_destination():
    raw = JobSpyProvider._row_to_raw_job(_row(), "IT Support", search_pass="google_ats")
    assert raw.source_platform == "google"
    assert raw.destination_url == "https://boards.greenhouse.io/example/jobs/123"
    assert raw.listing_url.startswith("https://www.google.com")


def test_google_jobspy_row_uses_ats_listing_when_direct_url_missing():
    raw = JobSpyProvider._row_to_raw_job(
        _row(
            job_url_direct="",
            job_url="https://jobs.lever.co/example/abc-123",
        ),
        "IT Support",
        search_pass="google_ats",
    )
    assert raw.destination_url == "https://jobs.lever.co/example/abc-123"


def test_google_jobspy_row_drops_non_ats_destination():
    raw = JobSpyProvider._row_to_raw_job(
        _row(
            job_url_direct="https://www.example.com/careers/123",
            job_url="https://www.google.com/search?q=jobs",
        ),
        "IT Support",
        search_pass="google_ats",
    )
    assert raw.destination_url is None
