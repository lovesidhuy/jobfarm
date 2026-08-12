"""Unit tests for ATS URL detection and adapter registry (no browser)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ci_env(monkeypatch):
    monkeypatch.setenv("BOT_NAME", "ci-smoke")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "")
    monkeypatch.setenv("DD_METRICS_ENABLED", "0")
    monkeypatch.setenv("FORM_ANSWERS_DISABLE_AI", "1")
    monkeypatch.delenv("SENTRY_DSN", raising=False)


# ── platform detection from URL ──────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/acme/jobs/123456",
    "https://job-boards.greenhouse.io/acme/jobs/123456",
    "https://boards.greenhouse.io/embed/job_app?for=acme&token=123",
    "https://www.boards.greenhouse.io/acme",
    "https://grnh.se/abc123",
    "https://gh.io/xyz",
])
def test_detect_greenhouse(url):
    from core.ats.registry import detect_platform
    assert detect_platform(url) == "greenhouse"


@pytest.mark.parametrize("url", [
    "https://jobs.lever.co/acme/abc-def-123",
    "https://jobs.lever.co/acme/abc-def-123/apply",
    "https://www.jobs.lever.co/acme",
])
def test_detect_lever(url):
    from core.ats.registry import detect_platform
    assert detect_platform(url) == "lever"


@pytest.mark.parametrize("url", [
    "https://jobs.ashbyhq.com/acme/abc-def",
    "https://www.jobs.ashbyhq.com/acme/abc-def",
    "https://jobs.ashbyhq.com/Acme/abc-def?department=eng",
])
def test_detect_ashby(url):
    from core.ats.registry import detect_platform
    assert detect_platform(url) == "ashby"


@pytest.mark.parametrize("url", [
    "https://acme.bamboohr.com/careers/123",
    "https://acme.bamboohr.com/jobs/view.php?id=123",
    "https://www.acme.bamboohr.com/careers/123",
])
def test_detect_bamboohr(url):
    from core.ats.registry import detect_platform
    assert detect_platform(url) == "bamboohr"


@pytest.mark.parametrize("url", [
    None,
    "",
    "https://example.com/jobs",
    "https://indeed.com/viewjob?jk=123",
    "https://linkedin.com/jobs/view/123",
    "https://greenhouse.io",  # marketing site root, but hostname matches
    "not-a-url",
])
def test_detect_unsupported(url):
    from core.ats.registry import detect_platform
    # greenhouse.io root still matches the host pattern (by design);
    # everything else must not detect.
    result = detect_platform(url)
    if url == "https://greenhouse.io":
        assert result == "greenhouse"
    else:
        assert result is None


# ── adapter class resolution ─────────────────────────────────────────

def test_detect_adapter_returns_classes():
    from core.ats.registry import detect_adapter
    from core.ats.adapters import (
        GreenhouseAdapter, LeverAdapter, AshbyAdapter, BambooHRAdapter,
    )
    assert detect_adapter("https://boards.greenhouse.io/a/jobs/1") is GreenhouseAdapter
    assert detect_adapter("https://jobs.lever.co/a/b") is LeverAdapter
    assert detect_adapter("https://jobs.ashbyhq.com/a/b") is AshbyAdapter
    assert detect_adapter("https://a.bamboohr.com/careers/1") is BambooHRAdapter
    assert detect_adapter("https://example.com") is None


def test_supported_platforms_includes_all_four():
    from core.ats.registry import supported_platforms
    platforms = supported_platforms()
    for p in ("greenhouse", "lever", "ashby", "bamboohr"):
        assert p in platforms


def test_is_supported_url():
    from core.ats.registry import is_supported_url
    assert is_supported_url("https://boards.greenhouse.io/a/jobs/1")
    assert is_supported_url("https://jobs.ashbyhq.com/a/b")
    assert not is_supported_url("https://example.com")
    assert not is_supported_url(None)


def test_declarative_registration_detects_url_and_embedded_dom():
    """New ATS modules declare all detection evidence alongside the adapter."""
    from core.ats import registry
    from core.ats.base import ATSAdapter

    class DemoAdapter(ATSAdapter):
        platform_name = "demo"

        @classmethod
        def detect(cls, url):
            return "demo.example" in (url or "")

        def initialize(self, page, profile, **kw): pass
        def upload_documents(self, **kw): return {"resume": False, "cover": False}
        def fill_application(self):
            from core.ats.types import FillStats
            return FillStats()
        def answer_questions(self): return 0
        def submit(self): return False
        def verify_submission(self): return None

    class EmbeddedDemoPage:
        url = "https://careers.company.example/apply"
        frames = []
        main_frame = None

        def content(self):
            return '<div data-demo-application="true"></div>'

    registry.register(
        "demo", DemoAdapter, host_suffixes=("demo.example",),
        aliases=("dmo.io",), dom_markers=('data-demo-application="true"',),
    )
    try:
        assert registry.detect_platform("https://tenant.demo.example/jobs/1") == "demo"
        assert registry.detect_platform("https://dmo.io/abc") == "demo"
        assert registry.detect_adapter_from_page(EmbeddedDemoPage()) is DemoAdapter
    finally:
        registry._ADAPTERS.pop("demo", None)
        registry._SPECS.pop("demo", None)


# ── per-adapter detect classmethods ──────────────────────────────────

def test_adapter_detect_classmethods():
    from core.ats.adapters import (
        GreenhouseAdapter, LeverAdapter, AshbyAdapter, BambooHRAdapter,
    )
    assert GreenhouseAdapter.detect("https://boards.greenhouse.io/a/jobs/1")
    assert GreenhouseAdapter.detect("https://grnh.se/abc")
    assert not GreenhouseAdapter.detect("https://jobs.lever.co/a/b")
    assert not GreenhouseAdapter.detect("")

    assert LeverAdapter.detect("https://jobs.lever.co/a/b")
    assert not LeverAdapter.detect("https://boards.greenhouse.io/a/jobs/1")

    assert AshbyAdapter.detect("https://jobs.ashbyhq.com/a/b")
    assert not AshbyAdapter.detect("https://jobs.lever.co/a/b")

    assert BambooHRAdapter.detect("https://acme.bamboohr.com/careers/1")
    assert not BambooHRAdapter.detect("https://jobs.ashbyhq.com/a/b")


# ── legacy facade compatibility ──────────────────────────────────────

def test_facade_is_greenhouse_or_lever_url_covers_all_platforms():
    from core.shared_modules.ats_apply import is_greenhouse_or_lever_url
    assert is_greenhouse_or_lever_url("https://boards.greenhouse.io/a/jobs/1")
    assert is_greenhouse_or_lever_url("https://jobs.lever.co/a/b")
    assert is_greenhouse_or_lever_url("https://jobs.ashbyhq.com/a/b")
    assert is_greenhouse_or_lever_url("https://acme.bamboohr.com/careers/1")
    assert is_greenhouse_or_lever_url("https://grnh.se/abc")
    assert not is_greenhouse_or_lever_url("https://example.com/jobs")
    assert not is_greenhouse_or_lever_url(None)
    assert not is_greenhouse_or_lever_url("")


def test_facade_apply_url_rejects_unsupported():
    from core.shared_modules.ats_apply import apply_url

    class FakePage:
        url = "https://example.com/jobs"

    ok, result_url, reason = apply_url(FakePage(), "https://example.com/jobs")
    assert ok is False
    assert result_url == "https://example.com/jobs"
    assert "not a supported ATS" in reason


def test_page_looks_like_ats_apply_by_url():
    from core.shared_modules.ats_apply import page_looks_like_ats_apply

    class FakePage:
        url = "https://boards.greenhouse.io/acme/jobs/1"

        def content(self):
            return "<html></html>"

        @property
        def frames(self):
            return []

        @property
        def main_frame(self):
            return None

    assert page_looks_like_ats_apply(FakePage()) is True


def test_page_looks_like_ats_apply_by_dom_marker():
    from core.shared_modules.ats_apply import page_looks_like_ats_apply

    class FakePage:
        url = "https://careers.acme.com/jobs/1"

        def content(self):
            return '<html><body><form class="ashby-application">...</form></body></html>'

        @property
        def frames(self):
            return []

        @property
        def main_frame(self):
            return None

    assert page_looks_like_ats_apply(FakePage()) is True


def test_page_looks_like_ats_apply_negative():
    from core.shared_modules.ats_apply import page_looks_like_ats_apply

    class FakePage:
        url = "https://example.com"

        def content(self):
            return "<html><body>Hello world</body></html>"

        @property
        def frames(self):
            return []

        @property
        def main_frame(self):
            return None

    assert page_looks_like_ats_apply(FakePage()) is False


# ── senior role rejection screening ─────────────────────────────────

@pytest.mark.parametrize("title", [
    "Senior Software Engineer",
    "Sr. Systems Administrator",
    "Lead Data Analyst",
    "Principal QA Engineer",
    "IT Manager",
    "Director of Technology",
    "Head of Infrastructure",
    "Staff DevOps Engineer",
    "Software Engineer III",
    "Systems Analyst IV",
    "Tier 3 Support Technician",
    "Founding Full Stack Engineer",
    "Distinguished Engineer",
    "Executive Vice President of IT",
])
def test_senior_role_rejection_in_gates(title):
    from core.discovery._gate_adapter import hard_screen_job
    passed, score, reason = hard_screen_job(title=title, company="Acme", description="", easy_apply=False)
    assert passed is False
    assert "senior" in reason.lower() or "strict title check" in reason.lower()


@pytest.mark.parametrize("title", [
    "Senior Software Engineer",
    "Sr. Systems Administrator",
    "Lead Engineer",
    "Principal Architect",
    "IT Director",
    "Engineering Manager",
    "Head of Support",
])
def test_senior_role_rejection_in_ats_board_api(title):
    from core.discovery.providers.ats_board_api import _TITLE_REJECT_RE
    assert _TITLE_REJECT_RE.search(title) is not None


# ── multi-platform ATS discovery slug & query extraction ───────────────

def test_ats_slugs_extraction_all_four_platforms():
    from core.discovery.ats_slugs import extract_slugs_from_url, extract_slugs_from_text, platform_for_url
    assert platform_for_url("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert platform_for_url("https://jobs.lever.co/acme/123") == "lever"
    assert platform_for_url("https://jobs.ashbyhq.com/acme/456") == "ashby"
    assert platform_for_url("https://acme.bamboohr.com/careers/789") == "bamboohr"

    assert extract_slugs_from_url("https://jobs.ashbyhq.com/acme/456") == [("ashby", "acme")]
    assert extract_slugs_from_url("https://acme.bamboohr.com/careers/789") == [("bamboohr", "acme")]

    snippet = "Check out https://jobs.ashbyhq.com/techcorp and https://bigcorp.bamboohr.com"
    extracted = extract_slugs_from_text(snippet)
    assert ("ashby", "techcorp") in extracted
    assert ("bamboohr", "bigcorp") in extracted


def test_google_cdp_query_includes_all_four_platforms():
    from core.discovery.providers.google_cdp_provider import build_google_web_ats_query
    q = build_google_web_ats_query("IT Support", "Vancouver, BC")
    assert "site:boards.greenhouse.io" in q
    assert "site:jobs.lever.co" in q
    assert "site:jobs.ashbyhq.com" in q
    assert "site:bamboohr.com" in q


