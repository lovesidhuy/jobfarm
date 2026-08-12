"""Unit tests for the ATS slug flywheel (no network, no Mongo required).

Run:  .venv/bin/python -m pytest tests/test_ats_flywheel.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.ats_slugs import (  # noqa: E402
    clean_slug,
    extract_slugs_from_text,
    extract_slugs_from_url,
    platform_for_url,
)
from core.discovery.classification.geo_normalizer import (  # noqa: E402
    REMOTE_SCOPE_CANADA,
    resolve_ats_location,
)
from core.discovery.classification.location_policy import (  # noqa: E402
    REGION_METRO_VAN,
    REGION_OTHER,
)
from core.discovery.providers.ats_board_api import (  # noqa: E402
    ashby_job_to_raw,
    bamboohr_job_to_raw,
    gh_job_to_raw,
    lever_job_to_raw,
    AtsBoardApiProvider,
    _select_records,
)
from core.discovery.slug_registry import (  # noqa: E402
    JsonSlugRegistry,
    _jobbots_mongo_db_name,
    register_slugs_from_url,
)
from core.discovery import registry_growth  # noqa: E402
from core.discovery.external_seeds import parse_slug_list, seed_feashliaa_lists  # noqa: E402


# ---------------------------------------------------------------------------
# ats_slugs
# ---------------------------------------------------------------------------

class TestCleanSlug:
    @pytest.mark.parametrize("raw,expected", [
        ("acme", "acme"),
        ("  Acme-Corp  ", "acme-corp"),
        ("acme_corp", "acme_corp"),
        ("https://boards.greenhouse.io/acme/jobs/123", "acme"),
        ("https://job-boards.greenhouse.io/initech/jobs/9", "initech"),
        ("https://jobs.lever.co/acme/abcd-1234", "acme"),
        ("https://acme.greenhouse.io/", "acme"),
        ("boards.greenhouse.io/acme/", "acme"),
        ("", ""),
        (None, ""),
        ("www", ""),                # infra host, not a slug
        ("!!!", ""),                # invalid charset
        ("a" * 100, ""),            # too long
        ("https://example.com/careers", ""),  # non-ATS URL
    ])
    def test_clean(self, raw, expected):
        assert clean_slug(raw) == expected


class TestExtractFromUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://boards.greenhouse.io/acme/jobs/123", [("greenhouse", "acme")]),
        ("https://job-boards.greenhouse.io/initech/jobs/9?utm=x", [("greenhouse", "initech")]),
        ("https://jobs.lever.co/hooli/uuid-1", [("lever", "hooli")]),
        ("https://piedpiper.lever.co/", [("lever", "piedpiper")]),
        ("https://acme.greenhouse.io/embed/job_app?token=1", [("greenhouse", "acme")]),
        # Google redirect wrapper (?url= / ?q=)
        ("https://www.google.com/url?q=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F1",
         [("greenhouse", "acme")]),
        ("https://example.com/jobs", []),
        (None, []),
    ])
    def test_extract(self, url, expected):
        assert extract_slugs_from_url(url) == expected

    def test_platform_for_url(self):
        assert platform_for_url("https://boards.greenhouse.io/x/jobs/1") == "greenhouse"
        assert platform_for_url("https://jobs.lever.co/x/1") == "lever"
        assert platform_for_url("https://jobs.ashbyhq.com/acme/uuid") == "ashby"
        assert platform_for_url("https://acme.bamboohr.com/careers/12") == "bamboohr"
        assert platform_for_url("https://example.com") is None


class TestExtractFromText:
    def test_lever_inline_config(self):
        # Variant B footprint: Lever slug buried in a small-business page.
        text = 'window.leverConfig = { applyUrl: "https://jobs.lever.co/acmestartup" };'
        assert ("lever", "acmestartup") in extract_slugs_from_text(text)

    def test_gh_board_and_subdomain(self):
        text = "See boards.greenhouse.io/tinylabs and jobs at tinylabs2.greenhouse.io"
        pairs = extract_slugs_from_text(text)
        assert ("greenhouse", "tinylabs") in pairs
        assert ("greenhouse", "tinylabs2") in pairs

    def test_ashby_and_bamboo_text(self):
        text = "Apply at jobs.ashbyhq.com/lightspeedhq and portableelectric.bamboohr.com/careers"
        pairs = extract_slugs_from_text(text)
        assert ("ashby", "lightspeedhq") in pairs
        assert ("bamboohr", "portableelectric") in pairs

    def test_infra_hosts_not_slugs(self):
        text = "visit www.greenhouse.io or boards.greenhouse.io for info"
        assert extract_slugs_from_text(text) == []


class TestAshbyBambooConverters:
    def test_bamboohr_job_opening_name_vancouver(self):
        job = {
            "id": "89",
            "jobOpeningName": "Technical Support Lead",
            "location": {"city": "Vancouver", "state": "British Columbia"},
            "atsLocation": {},
            "isRemote": None,
        }
        raw = bamboohr_job_to_raw("portableelectric", "Portable Electric", job)
        assert raw is not None
        assert raw.title == "Technical Support Lead"
        assert raw.source_platform == "bamboohr"
        assert "portableelectric.bamboohr.com/careers/89" in raw.destination_url
        assert raw.location  # geo-qualified

    def test_bamboohr_skips_empty_title_without_jobOpeningName(self):
        # Pre-fix shape: jobTitle only — still works if present
        job = {"id": "1", "jobTitle": "IT Support", "location": {"city": "Burnaby", "state": "BC"}}
        raw = bamboohr_job_to_raw("acme", "Acme", job)
        assert raw is not None
        assert raw.title == "IT Support"

    def test_ashby_canada_remote_dropped(self):
        job = {
            "id": "abc",
            "title": "Application Support Specialist",
            "location": "Remote in Canada",
            "jobUrl": "https://jobs.ashbyhq.com/lightspeedhq/abc",
            "isRemote": True,
        }
        assert ashby_job_to_raw("lightspeedhq", "Lightspeed", job) is None

    def test_board_api_supports_all_four(self):
        assert set(AtsBoardApiProvider.supported_platforms) >= {
            "greenhouse", "lever", "ashby", "bamboohr",
        }


# ---------------------------------------------------------------------------
# geo_normalizer
# ---------------------------------------------------------------------------

class TestGeoNormalizer:
    @pytest.mark.parametrize("raw,region,in_area", [
        ("Vancouver, BC", REGION_METRO_VAN, True),
        ("Vancouver - Hybrid", REGION_METRO_VAN, True),
        ("Vancouver, BC (on-site)", REGION_METRO_VAN, True),
        ("Burnaby, British Columbia", REGION_METRO_VAN, True),
        ("Toronto / Vancouver", REGION_METRO_VAN, True),   # best fragment wins
        ("Vancouver, WA", REGION_OTHER, False),            # US guard
        ("Portland, OR", REGION_OTHER, False),
        ("San Francisco, CA", REGION_OTHER, False),
    ])
    def test_metro_and_guard(self, raw, region, in_area):
        res = resolve_ats_location(raw)
        assert res.region == region
        assert res.in_search_area == in_area

    @pytest.mark.parametrize("raw", [
        "Remote, Canada",
        "Remote - Canada",
        "Canada (Remote)",
        "Anywhere - Western Canada",
        "Remote - USA or Canada",
    ])
    def test_canada_remote_eligible(self, raw):
        res = resolve_ats_location(raw)
        assert res.in_search_area is True
        assert res.is_remote is True
        assert res.remote_scope == REMOTE_SCOPE_CANADA

    def test_hybrid_hint(self):
        res = resolve_ats_location("Vancouver - Hybrid")
        assert res.work_mode_hint == "hybrid"
        assert res.is_remote is False  # hybrid ≠ remote

    def test_multi_location_picks_metro(self):
        res = resolve_ats_location("Toronto, ON / Vancouver, BC")
        assert res.region == REGION_METRO_VAN
        assert "Vancouver" in res.canonical_location


# ---------------------------------------------------------------------------
# slug_registry (JSON backend — no Mongo)
# ---------------------------------------------------------------------------

class TestJsonRegistry:
    @pytest.fixture()
    def reg(self, tmp_path):
        return JsonSlugRegistry(tmp_path / "reg.json")

    def test_upsert_insert_then_update(self, reg):
        assert reg.upsert_slug("acme", "greenhouse", source="manual_seed") == "inserted"
        assert reg.upsert_slug("acme", "greenhouse", source="jobspy") == "updated"
        # Same slug, other platform → separate record.
        assert reg.upsert_slug("acme", "lever", source="manual_seed") == "inserted"

    def test_seed_defaults(self, reg, tmp_path):
        reg.upsert_slug("acme", "greenhouse", source="manual_seed")
        import json
        data = json.loads((tmp_path / "reg.json").read_text())
        rec = data["slugs"]["greenhouse:acme"]
        assert rec["status"] == "active"
        assert rec["discovery_source"] == "manual_seed"
        assert rec["last_successful_poll_at"] is None
        assert rec["consecutive_failures"] == 0

    def test_invalid_rejected(self, reg):
        assert reg.upsert_slug("!!!", "greenhouse", source="x") == "invalid"
        assert reg.upsert_slug("acme", "workday", source="x") == "invalid"

    def test_dead_slug_policy(self, reg, monkeypatch):
        monkeypatch.setenv("ATS_SLUG_MAX_CONSEC_FAILURES", "3")
        reg.upsert_slug("deadco", "greenhouse", source="jobspy")
        for _ in range(2):
            reg.mark_poll_failure("deadco", "greenhouse", reason="http_404")
        assert len(reg.iter_active_slugs()) == 1  # still active at 2
        reg.mark_poll_failure("deadco", "greenhouse", reason="http_404")
        assert reg.iter_active_slugs() == []  # inactive at 3
        import json
        rec = json.loads(reg._path.read_text())["slugs"]["greenhouse:deadco"]
        assert rec["status"] == "inactive"
        assert rec["deactivated_reason"] == "http_404_x3"

    def test_success_resets_failures(self, reg, monkeypatch):
        monkeypatch.setenv("ATS_SLUG_MAX_CONSEC_FAILURES", "3")
        reg.upsert_slug("flaky", "lever", source="tavily")
        reg.mark_poll_failure("flaky", "lever", reason="http_404")
        reg.mark_poll_failure("flaky", "lever", reason="http_404")
        reg.mark_poll_success("flaky", "lever")
        reg.mark_poll_failure("flaky", "lever", reason="http_404")
        assert len(reg.iter_active_slugs()) == 1  # reset worked

    def test_stats(self, reg):
        reg.upsert_slug("a", "greenhouse", source="x")
        reg.upsert_slug("b", "lever", source="x")
        s = reg.stats()
        assert s["greenhouse_active"] == 1
        assert s["lever_active"] == 1
        assert s["total"] == 2


# ---------------------------------------------------------------------------
# provider record conversion (pure, no HTTP)
# ---------------------------------------------------------------------------

class TestRecordConversion:
    def test_gh_metro_job_kept(self):
        job = {
            "id": 123,
            "title": "IT Systems Administrator",
            "location": {"name": "Vancouver, BC (on-site)"},
            "absolute_url": "https://job-boards.greenhouse.io/aspect/jobs/123",
        }
        raw = gh_job_to_raw("aspect", "Aspect Biosystems", job)
        assert raw is not None
        assert raw.source_platform == "greenhouse"
        assert raw.source_job_id == "gh-aspect-123"
        assert raw.location == "Vancouver, BC"
        assert raw.raw_extras["board_slug"] == "aspect"
        assert raw.raw_extras["geo_raw"] == "Vancouver, BC (on-site)"

    def test_gh_us_job_dropped(self):
        job = {
            "id": 1,
            "title": "Software Engineer",
            "location": {"name": "San Francisco, CA"},
            "absolute_url": "https://boards.greenhouse.io/x/jobs/1",
        }
        assert gh_job_to_raw("x", "X", job) is None

    def test_lever_remote_canada_dropped(self):
        job = {
            "id": "abc",
            "text": "QA Engineer",
            "categories": {"location": "Remote, Canada", "team": "Quality"},
            "hostedUrl": "https://jobs.lever.co/hooli/abc",
            "createdAt": 1785000000000,
        }
        assert lever_job_to_raw("hooli", "Hooli", job) is None

    def test_lever_missing_url_dropped(self):
        job = {"id": "x", "text": "Dev", "categories": {"location": "Vancouver, BC"}}
        assert lever_job_to_raw("hooli", "Hooli", job) is None


# ---------------------------------------------------------------------------
# footprint sensor (pure functions only — no HTTP)
# ---------------------------------------------------------------------------

class TestFootprintSensor:
    def test_query_variants(self):
        from core.discovery.footprint_sensor import build_footprint_queries

        qs = build_footprint_queries(["Vancouver"])
        assert len(qs) == 5  # A GH embed, B Lever, C boards, D Ashby, E BambooHR
        # Variant A — custom-domain GH embed
        assert any(
            '"Vancouver" site:*.greenhouse.io' in q
            and "-site:boards.greenhouse.io" in q
            and "-site:www.greenhouse.io" in q
            for q in qs
        )
        # Variant B — Lever inline config
        assert any('"Vancouver" intext:"jobs.lever.co" -site:lever.co' in q for q in qs)
        # Variant C — direct board footprint + About/Team
        assert any(
            '"Vancouver" site:boards.greenhouse.io OR site:jobs.lever.co' in q
            and '"About Us"' in q and '"Team"' in q
            for q in qs
        )
        # Variant D/E — Ashby + BambooHR local boards
        assert any("site:jobs.ashbyhq.com" in q for q in qs)
        assert any("site:bamboohr.com/careers" in q for q in qs)

    def test_mine_from_serp_hits(self):
        from core.discovery.footprint_sensor import mine_slugs_from_hits

        hits = [
            # Path slug in URL
            {"url": "https://boards.greenhouse.io/tinylabs/jobs/1", "title": "", "content": ""},
            # Subdomain slug in URL (variant A)
            {"url": "https://acmestartup.greenhouse.io/", "title": "", "content": ""},
            # Custom domain w/ slug only in snippet (variant B)
            {"url": "https://acme.ca/careers", "title": "Careers",
             "content": "Apply via jobs.lever.co/acmestartup today"},
            # Noise
            {"url": "https://example.com", "title": "", "content": ""},
        ]
        pairs = mine_slugs_from_hits(hits, page_fetch=False)
        assert ("greenhouse", "tinylabs") in pairs
        assert ("greenhouse", "acmestartup") in pairs
        assert ("lever", "acmestartup") in pairs


# ---------------------------------------------------------------------------
# ats_crossmatch — LinkedIn → board reverse-engineering
# ---------------------------------------------------------------------------

class TestCompanyResolution:
    @pytest.mark.parametrize("company,expected_key", [
        ("AbCellera Biologics, Inc.", "abcellerabiologics"),
        ("Brex Inc.", "brex"),
        ("1Password", "1password"),
        ("Rival Technologies", "rival"),       # suffix stripped
        ("AOT Technologies", "aot"),
    ])
    def test_normalize(self, company, expected_key):
        from core.discovery.ats_crossmatch import normalize_company

        assert normalize_company(company) == expected_key

    def test_candidates_include_suffixy_variant(self):
        from core.discovery.ats_crossmatch import company_slug_candidates

        cands = company_slug_candidates("Rival Technologies")
        assert "rival" in cands                # suffix-stripped
        assert "rivaltechnologies" in cands    # suffix-bearing

    def test_resolve_exact_and_prefix(self):
        from core.discovery.ats_crossmatch import resolve_company_slug

        slugs = {"abcellera", "brex", "rivaltechnologies"}
        assert resolve_company_slug("Brex Inc.", slugs)[0] == "brex"
        assert resolve_company_slug("AbCellera Biologics", slugs)[0] == "abcellera"
        assert resolve_company_slug("Rival Technologies", slugs)[0] == "rivaltechnologies"
        assert resolve_company_slug("Microsoft", slugs)[0] is None


class TestTitleMatch:
    @pytest.mark.parametrize("a,b,expected", [
        ("Software Engineer II, Backend", "Software Engineer II, Backend", True),
        ("IT Systems Administrator", "IT Systems Administrator (12-month term)", True),
        ("QA Engineer", "QA Engineer - Platform", True),
        ("Software Engineer II, Backend", "Senior Software Engineer, Backend", False),
        ("IT Support Analyst", "Marketing Manager", False),
    ])
    def test_titles(self, a, b, expected):
        from core.discovery.ats_crossmatch import titles_match

        matched, _ = titles_match(a, b)
        assert matched == expected


class TestCrossmatch:
    def _li(self, title, company, evidence="", dest=""):
        from core.discovery.contracts import RawJob

        return RawJob(
            source_platform="linkedin", source_job_id=f"li-{title}",
            title=title, company=company, location="Vancouver, BC",
            listing_url="https://www.linkedin.com/jobs/view/1",
            destination_url=dest or None,
            easy_apply_evidence=evidence,
        )

    def _board(self, slug, title, platform="greenhouse", jid="1"):
        from core.discovery.contracts import RawJob

        return RawJob(
            source_platform=platform, source_job_id=f"gh-{slug}-{jid}",
            title=title, company=slug, location="Vancouver, BC",
            listing_url=f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}",
            destination_url=f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}",
            raw_extras={"board_slug": slug},
        )

    def test_match_flow(self, tmp_path):
        from core.discovery.slug_registry import JsonSlugRegistry

        reg = JsonSlugRegistry(tmp_path / "reg.json")
        reg.upsert_slug("brex", "greenhouse", source="manual_seed")

        linkedin = [
            self._li("Software Engineer II, Backend", "Brex"),
            self._li("Senior Software Engineer, Travel", "Brex"),
            self._li("Program Manager", "UnknownCo"),
            self._li("EA job", "Brex", evidence="linkedin_easy_apply_filtered_pass"),
        ]
        boards = [
            self._board("brex", "Software Engineer II, Backend", jid="8603327002"),
            self._board("brex", "Staff Software Engineer, Travel & Expense", jid="8635342002"),
        ]
        from core.discovery.ats_crossmatch import crossmatch_linkedin_jobs

        matched, stats = crossmatch_linkedin_jobs(linkedin, boards, reg)
        assert stats.linkedin_jobs == 4
        assert stats.matches == 1  # only the backend role; EA row skipped
        lead = matched[0]
        assert lead.source_platform == "greenhouse"
        assert lead.source_job_id == "gh-brex-8603327002"
        assert lead.destination_url.endswith("/jobs/8603327002")
        assert lead.raw_extras["discovered_by"] == "linkedin_ats_crossmatch"
        assert lead.raw_extras["linkedin_title"] == "Software Engineer II, Backend"

    def test_no_registry_no_match(self, tmp_path):
        from core.discovery.ats_crossmatch import crossmatch_linkedin_jobs

        matched, stats = crossmatch_linkedin_jobs(
            [self._li("X", "Y")], [self._board("y", "X")], None
        )
        assert matched == []


# ---------------------------------------------------------------------------
# seed script parsing (pure functions)
# ---------------------------------------------------------------------------

class TestSeedParsing:
    def test_text_rows(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "seed_slug_registry", ROOT / "scripts" / "seed_slug_registry.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rows = mod.parse_inputs("acme\n# comment\nhttps://boards.greenhouse.io/initech/jobs/1\n", "text")
        assert len(rows) == 2
        slug, platform, _ = mod._row_to_slug_platform(rows[1], None)
        assert slug == "initech"
        assert platform == "greenhouse"

    def test_json_rows(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "seed_slug_registry", ROOT / "scripts" / "seed_slug_registry.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rows = mod.parse_inputs(
            '[{"slug": "acme", "platform": "greenhouse"}, "hooli"]', "json"
        )
        assert len(rows) == 2
        s1, p1, _ = mod._row_to_slug_platform(rows[0], None)
        assert (s1, p1) == ("acme", "greenhouse")
        s2, p2, _ = mod._row_to_slug_platform(rows[1], "lever")
        assert (s2, p2) == ("hooli", "lever")

    def test_csv_rows(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "seed_slug_registry", ROOT / "scripts" / "seed_slug_registry.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rows = mod.parse_inputs("slug,platform,company\nacme,greenhouse,Acme Corp\n", "csv")
        assert len(rows) == 1
        slug, platform, company = mod._row_to_slug_platform(rows[0], None)
        assert (slug, platform, company) == ("acme", "greenhouse", "Acme Corp")


# ---------------------------------------------------------------------------
# Registry DB selection + growth (seed / flywheel)
# ---------------------------------------------------------------------------

class TestJobbotsMongoDbName:
    def test_prefers_jobbots_mongo_database(self, monkeypatch):
        monkeypatch.setenv("JOBBOTS_MONGO_DATABASE", "jobbots")
        monkeypatch.setenv("MONGODB_HISTORY_DB", "auto_job_applier_history")
        assert _jobbots_mongo_db_name("auto_job_applier_history") == "jobbots"

    def test_falls_back_to_jobbots(self, monkeypatch):
        for k in (
            "JOBBOTS_MONGO_DATABASE",
            "MONGODB_SLUG_DB",
            "MONGODB_DB_NAME",
            "MONGODB_HISTORY_DB",
        ):
            monkeypatch.delenv(k, raising=False)
        assert _jobbots_mongo_db_name("") == "jobbots"


class TestRegistryGrowth:
    def test_seed_from_artifact_into_json_registry(self, tmp_path, monkeypatch):
        # Point get_registry at a JSON file and seed from a mini artifact.
        art = tmp_path / "seed.json"
        art.write_text(
            json.dumps(
                {
                    "slugs": {
                        "greenhouse:seedco": {
                            "slug_id": "seedco",
                            "platform": "greenhouse",
                            "status": "active",
                            "discovery_source": "artifact_seed",
                        },
                        "lever:seedlever": {
                            "slug_id": "seedlever",
                            "platform": "lever",
                            "status": "active",
                            "discovery_source": "artifact_seed",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        reg_path = tmp_path / "live.json"
        monkeypatch.setenv("ATS_SLUG_REGISTRY_BACKEND", "json")
        monkeypatch.setenv("ATS_SLUG_REGISTRY_JSON", str(reg_path))
        # Clear cached registry
        import core.discovery.slug_registry as sr

        sr._registry_cache = None
        counts = registry_growth.seed_from_artifact(path=art, min_active=1, force=True)
        assert counts["inserted"] >= 2
        reg = JsonSlugRegistry(reg_path)
        active = reg.iter_active_slugs()
        platforms = {(r["platform"], r["slug_id"]) for r in active}
        assert ("greenhouse", "seedco") in platforms
        assert ("lever", "seedlever") in platforms

    def test_register_slugs_from_url_grows_registry(self, tmp_path, monkeypatch):
        reg_path = tmp_path / "live.json"
        monkeypatch.setenv("ATS_SLUG_REGISTRY_BACKEND", "json")
        monkeypatch.setenv("ATS_SLUG_REGISTRY_JSON", str(reg_path))
        import core.discovery.slug_registry as sr

        sr._registry_cache = None
        out = register_slugs_from_url(
            "https://job-boards.greenhouse.io/newco/jobs/99",
            source="firecrawl",
        )
        assert out["inserted"] + out["updated"] >= 1
        reg = JsonSlugRegistry(reg_path)
        assert any(r["slug_id"] == "newco" for r in reg.iter_active_slugs())


class TestExternalSeedImport:
    def test_parse_feashliaa_slug_list_deduplicates_and_rejects_bad_rows(self):
        parsed = parse_slug_list(json.dumps(["Acme", "acme", "bad slug!", 4, "hooli-inc"]))
        assert parsed == ["acme", "hooli-inc"]

    def test_seed_feashliaa_import_is_bounded_and_preserves_platform(self, tmp_path):
        reg = JsonSlugRegistry(tmp_path / "registry.json")
        report = seed_feashliaa_lists(
            reg,
            {
                "greenhouse": ["acme", "hooli", "initech"],
                "lever": ["piedpiper"],
                "ashby": ["notion"],
                "bamboohr": ["vancity"],
            },
            per_platform=1,
            offset=1,
        )
        assert report["totals"]["inserted"] == 1
        assert report["platforms"]["greenhouse"]["selected"] == 1
        rows = {(row["platform"], row["slug_id"]) for row in reg.iter_active_slugs()}
        assert rows == {("greenhouse", "hooli")}


class TestBoardPollBudget:
    def test_select_records_is_fair_and_prefers_never_polled(self, monkeypatch):
        monkeypatch.setenv("ATS_BOARD_API_MAX_SLUGS_PER_PLATFORM", "1")
        selected = _select_records([
            {"platform": "greenhouse", "slug_id": "recent", "last_successful_poll_at": "2026-08-08T00:00:00Z"},
            {"platform": "greenhouse", "slug_id": "new", "last_successful_poll_at": None},
            {"platform": "lever", "slug_id": "lever-new", "last_successful_poll_at": None},
            {"platform": "ashby", "slug_id": "ashby-new", "last_successful_poll_at": None},
            {"platform": "bamboohr", "slug_id": "bamboo-new", "last_successful_poll_at": None},
        ])
        assert {(row["platform"], row["slug_id"]) for row in selected} == {
            ("greenhouse", "new"),
            ("lever", "lever-new"),
            ("ashby", "ashby-new"),
            ("bamboohr", "bamboo-new"),
        }
