"""Fast unit tests for monorepo core (no browser, Mongo, or network)."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _ci_env(monkeypatch):
    monkeypatch.setenv("BOT_NAME", "ci-smoke")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "")
    monkeypatch.setenv("DD_METRICS_ENABLED", "0")
    monkeypatch.delenv("SENTRY_DSN", raising=False)


def test_supervised_bot_configs():
    from core.supervised_bots import supervised_bot_configs

    cfgs = supervised_bot_configs()
    names = {c["bot_name"] for c in cfgs}
    assert "indeed_it" in names
    assert "indeed_general" in names  # office/CS Indeed bot
    assert "glassdoor_it" in names
    assert "workopolis_it" in names
    # Sole LinkedIn bot (lovepreetsidhu8173 NST); linkedin_it disabled.
    assert "linkedin_general" in names
    assert "linkedin_it" not in names
    # Google ATS (Greenhouse/Lever) discovery + apply profile for cloud cycle.
    assert "google_it" in names
    # Job Bank Direct Apply (authenticated NST profile).
    assert "jobbank_it" in names
    # Glassdoor/Workopolis general stay off (IT only).
    assert "glassdoor_general" not in names
    assert "workopolis_general" not in names
    assert len(cfgs) == 7


def test_linkedin_always_maps_to_linkedin_general_bot():
    from core.browser.nst_profile_safety import portal_profile_bot_name

    assert portal_profile_bot_name("linkedin", "it") == "linkedin_general"
    assert portal_profile_bot_name("linkedin", "general") == "linkedin_general"
    assert portal_profile_bot_name("indeed", "it") == "indeed_it"
    assert portal_profile_bot_name("indeed", "general") == "indeed_general"
    assert portal_profile_bot_name("glassdoor", "it") == "glassdoor_it"


def test_datadog_metrics_noop():
    from core.datadog_metrics import gauge, increment

    increment("bot.applications", tags=["bot:ci", "event:applied"])
    gauge("bot.heartbeat", 1.0, tags=["bot:ci"])


def test_sentry_init_without_dsn(monkeypatch):
    import core.sentry_init as s

    monkeypatch.setattr(s, "_resolve_dsn", lambda: "")
    s._initialized = False
    assert s.init_sentry("test") is False


def test_event_log_record_event():
    from core.event_log import record_event

    record_event("skipped", job_id="test-123", bot_name="ci-smoke", reason="unit_test")


def test_datadog_metrics_module_importable():
    from core import datadog_metrics, sentry_init

    assert hasattr(datadog_metrics, "increment")
    assert hasattr(sentry_init, "init_sentry")


def test_indeed_work_history_is_exact_and_dates_are_blank_or_iso():
    from core.shared_modules.indeed_history import (
        valid_work_history_date,
        work_history_payload,
    )

    assert work_history_payload() == [
        {"company": "Vancouver Coastal Health", "title": "Porter",
         "start_date": "2022-10-01", "end_date": "", "current": True},
        {"company": "Bell", "title": "Sales Representative",
         "start_date": "2018-04-01", "end_date": "2021-08-01", "current": False},
    ]
    assert valid_work_history_date("")
    assert valid_work_history_date("2021-08-01")
    assert not valid_work_history_date("N/A")


def test_build_subprocess_env_sets_identity(tmp_path, monkeypatch):
    from pathlib import Path
    from core.supervisor_runtime import build_subprocess_env

    # Avoid loading local automation_monorepo/.env (may pin CapSolver).
    base = Path(tmp_path)
    cfg = {
        "bot_name": "indeed_it",
        "cdp_port": "9222",
        "bot_instance_id": "0",
        "profile_dir": str(base / "data" / "browser_profiles" / "indeed_it"),
        "profile": "IT",
    }
    monkeypatch.setenv("IMAP_EMAIL_IT", "it@example.com")
    monkeypatch.setenv("IMAP_APP_PASSWORD_IT", "secret")
    monkeypatch.delenv("BROWSER_VENDOR", raising=False)
    monkeypatch.delenv("CAPTCHA_CLOUDFLARE_SOLVER", raising=False)

    def fake_secret(name, default=""):
        if name == "NSTBROWSER_PROFILE_ID_INDEED_IT":
            return "nst-indeed-it"
        return default

    parent = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CAPTCHA_CLOUDFLARE_SOLVER", "BROWSER_VENDOR"}
    }

    env = build_subprocess_env(
        cfg,
        "test-run",
        base,
        parent_environ=parent,
        get_secret=fake_secret,
    )
    assert env["BOT_NAME"] == "indeed_it"
    assert env["CDP_PORT"] == "9222"
    assert env["JOB_PROFILE"] == "IT"
    assert env["IMAP_EMAIL"] == "it@example.com"
    assert env["IMAP_APP_PASSWORD"] == "secret"
    assert env["BROWSER_VENDOR"] == "nstbrowser"
    assert env["NSTBROWSER_PROFILE_ID"] == "nst-indeed-it"
    assert env["CAPTCHA_CLOUDFLARE_SOLVER"] == "capsolver"


def test_nstbrowser_vendor_does_not_fallback_to_other_profiles(monkeypatch):
    import core.supervised_bots as bots
    import core.browser.nst_accounts as nst_accounts
    import pytest

    monkeypatch.setenv("BROWSER_VENDOR", "nstbrowser")
    monkeypatch.setenv("NSTBROWSER_FORBID_CREATE", "1")
    monkeypatch.delenv("NSTBROWSER_PROFILE_ID", raising=False)
    monkeypatch.delenv("NST_PROFILE_ID", raising=False)
    # Stamp path uses resolve_profile_id (not only _nstbrowser_profile_id_for).
    monkeypatch.setattr(bots, "_nstbrowser_profile_id_for", lambda bot_name: "")
    monkeypatch.setattr(
        nst_accounts,
        "resolve_profile_id",
        lambda bot_name: (1, "", None),
    )
    monkeypatch.setattr(
        nst_accounts,
        "resolve_api_key",
        lambda slot=None: (1, ""),
    )

    with pytest.raises(RuntimeError, match="Missing existing NST profile"):
        bots._stamp_browser_profile_ids("indeed_it", overwrite=True)


def test_nstbrowser_vendor_clears_when_create_allowed_and_missing(monkeypatch):
    import core.supervised_bots as bots
    import core.browser.nst_accounts as nst_accounts

    monkeypatch.setenv("BROWSER_VENDOR", "nstbrowser")
    monkeypatch.setenv("NSTBROWSER_FORBID_CREATE", "0")
    monkeypatch.delenv("NSTBROWSER_PROFILE_ID", raising=False)
    monkeypatch.delenv("NST_PROFILE_ID", raising=False)
    monkeypatch.setattr(bots, "_nstbrowser_profile_id_for", lambda bot_name: "")
    monkeypatch.setattr(
        nst_accounts,
        "resolve_profile_id",
        lambda bot_name: (1, "", None),
    )
    monkeypatch.setattr(
        nst_accounts,
        "resolve_api_key",
        lambda slot=None: (1, ""),
    )

    bots._stamp_browser_profile_ids("indeed_it", overwrite=True)

    assert "NSTBROWSER_PROFILE_ID" not in os.environ


def test_nstbrowser_forbid_create_defaults_on(monkeypatch):
    from core.browser.nst_profile_safety import (
        nstbrowser_forbid_create,
        refuse_profile_creation,
        require_existing_nst_profile_id,
    )
    import pytest

    monkeypatch.delenv("NSTBROWSER_FORBID_CREATE", raising=False)
    assert nstbrowser_forbid_create() is True
    with pytest.raises(RuntimeError, match="FORBID_CREATE"):
        refuse_profile_creation(context="unit-test")

    monkeypatch.setenv("NSTBROWSER_FORBID_CREATE", "0")
    assert nstbrowser_forbid_create() is False
    refuse_profile_creation(context="allowed")  # no raise

    monkeypatch.setenv("NSTBROWSER_FORBID_CREATE", "1")
    assert require_existing_nst_profile_id("abc-123") == "abc-123"
    with pytest.raises(RuntimeError, match="INDEED_IT"):
        require_existing_nst_profile_id("", bot_name="indeed_it")


def test_nstbrowser_keep_alive_env_flags(monkeypatch):
    from core.browser.open_chrome import nstbrowser_keep_alive

    monkeypatch.delenv("KEEP_BROWSER", raising=False)
    monkeypatch.delenv("NSTBROWSER_KEEP_ALIVE", raising=False)
    assert nstbrowser_keep_alive() is False
    monkeypatch.setenv("KEEP_BROWSER", "1")
    assert nstbrowser_keep_alive() is True
    monkeypatch.delenv("KEEP_BROWSER", raising=False)
    monkeypatch.setenv("NSTBROWSER_KEEP_ALIVE", "yes")
    assert nstbrowser_keep_alive() is True


def test_dataimpulse_proxy_uses_sticky_session_by_default(monkeypatch):
    from core.evasion import _capmonster

    monkeypatch.setenv("CAPMONSTER_PROXY_URL", "http://user:pass@gw.dataimpulse.com:823")
    monkeypatch.delenv("CAPMONSTER_DATAIMPULSE_SESSION_ID", raising=False)
    monkeypatch.delenv("DATAIMPULSE_SESSION_ID", raising=False)
    monkeypatch.delenv("CAPMONSTER_DATAIMPULSE_ROTATE_PER_TASK", raising=False)
    monkeypatch.delenv("BYPASS_PROXY", raising=False)

    first = _capmonster._capmonster_proxy_fields()
    second = _capmonster._capmonster_proxy_fields()

    assert first["proxyAddress"] == "gw.dataimpulse.com"
    assert first["proxyLogin"] == "user__jobbots-cf"
    assert second["proxyLogin"] == first["proxyLogin"]


def test_dataimpulse_proxy_supports_explicit_session(monkeypatch):
    from core.evasion import _capmonster

    monkeypatch.setenv("CAPMONSTER_PROXY_URL", "http://user__old:pass@gw.dataimpulse.com:823")
    monkeypatch.setenv("CAPMONSTER_DATAIMPULSE_SESSION_ID", "cf-prod-1")
    monkeypatch.delenv("CAPMONSTER_DATAIMPULSE_ROTATE_PER_TASK", raising=False)

    fields = _capmonster._capmonster_proxy_fields()

    assert fields["proxyLogin"] == "user__cf-prod-1"


def test_dataimpulse_proxy_rotation_is_opt_in(monkeypatch):
    from core.evasion import _capmonster

    monkeypatch.setenv("CAPMONSTER_PROXY_URL", "http://user:pass@gw.dataimpulse.com:823")
    monkeypatch.delenv("CAPMONSTER_DATAIMPULSE_SESSION_ID", raising=False)
    monkeypatch.delenv("DATAIMPULSE_SESSION_ID", raising=False)
    monkeypatch.setenv("CAPMONSTER_DATAIMPULSE_ROTATE_PER_TASK", "1")

    first = _capmonster._capmonster_proxy_fields()
    second = _capmonster._capmonster_proxy_fields()

    assert first["proxyLogin"].startswith("user__sessid.")
    assert second["proxyLogin"].startswith("user__sessid.")
    assert second["proxyLogin"] != first["proxyLogin"]


def test_cf_clearance_url_cookie_uses_playwright_schema(monkeypatch):
    from core.evasion import _capmonster

    added_batches = []

    class FakeContext:
        def add_cookies(self, cookies):
            added_batches.append(cookies)

        def cookies(self, url):
            return [{"name": "cf_clearance", "value": "clearance", "domain": ".indeed.com"}]

    class FakePage:
        url = "https://ca.indeed.com/jobs?q=Office+Clerk"
        context = FakeContext()

        def evaluate(self, script):
            return self.url

    assert _capmonster._apply_capmonster_cf_clearance(FakePage(), "clearance") is True

    applied_batch = added_batches[-1]
    url_cookie = next(cookie for cookie in applied_batch if cookie.get("url"))
    domain_cookie = next(cookie for cookie in applied_batch if cookie.get("domain") == ".indeed.com")
    assert "path" not in url_cookie
    assert domain_cookie["path"] == "/"


def test_capmonster_cf_hard_reject_detection():
    from core.evasion import _handlers

    assert _handlers._capmonster_cf_hard_reject({
        "final_status": "CF_CLEARANCE_APPLIED_NOT_ACCEPTED",
        "capmonster_result": "cf_clearance",
    }) is True
    assert _handlers._capmonster_cf_hard_reject({
        "final_status": "TOKEN_RESCUE_RETURNED_NO_USABLE_TOKEN",
        "capmonster_result": "ERROR_CAPTCHA_UNSOLVABLE",
    }) is True
    assert _handlers._capmonster_cf_hard_reject({
        "final_status": "SOLVED_BY_CAPMONSTER_CF_CLEARANCE",
        "capmonster_result": "cf_clearance",
    }) is False


def test_recaptcha_enterprise_task_keeps_data_s_with_payload(monkeypatch):
    from core.evasion import _capmonster

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errorId": 0, "taskId": 123}

    def fake_post(url, json, timeout):
        captured["task"] = json["task"]
        return FakeResponse()

    monkeypatch.setattr(_capmonster.requests, "post", fake_post)
    monkeypatch.setenv("CAPMONSTER_RECAPTCHA_SEND_DATA_S_VALUE", "1")

    task_id = _capmonster._create_capmonster_task(
        "client-key",
        {
            "websiteURL": "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
            "websiteKey": "site-key",
            "isEnterprise": True,
            "enterprisePayload": {"action": "submit"},
            "recaptchaDataSValue": "data-s-token",
        },
        user_agent="ua",
        cookies="a=b",
        use_proxy=False,
    )

    assert task_id == 123
    assert captured["task"]["type"] == "RecaptchaV2EnterpriseTaskProxyless"
    assert captured["task"]["enterprisePayload"]["s"] == "data-s-token"
    assert captured["task"]["recaptchaDataSValue"] == "data-s-token"


def test_recaptcha_unsolvable_cooldown(monkeypatch):
    from core.evasion import _capmonster

    params = {
        "websiteURL": "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
        "websiteKey": "site-key",
        "recaptchaDataSValue": "data-s-token",
        "enterprisePayload": {"s": "data-s-token"},
    }
    monkeypatch.setenv("CAPMONSTER_RECAPTCHA_UNSOLVABLE_COOLDOWN_SECONDS", "900")
    _capmonster._LAST_RECAPTCHA_UNSOLVABLE.clear()

    assert _capmonster._recaptcha_recently_unsolvable(params) is False
    _capmonster._remember_recaptcha_unsolvable(params)
    assert _capmonster._recaptcha_recently_unsolvable(params) is True

    changed = dict(params)
    changed["recaptchaDataSValue"] = "different"
    assert _capmonster._recaptcha_recently_unsolvable(changed) is False


def test_recaptcha_proxyless_fallback_defaults_on(monkeypatch):
    from core.evasion import _capmonster

    created_tasks = []

    def fake_create_task(client_key, params, user_agent, cookies, *, use_proxy=True, force_standard=False):
        created_tasks.append((use_proxy, force_standard))
        return 100 + len(created_tasks)

    def fake_poll(client_key, task_id, timeout):
        _capmonster._poll_capmonster_result.last_error_code = "ERROR_CAPTCHA_UNSOLVABLE"
        return None

    monkeypatch.setattr(_capmonster, "_extract_recaptcha_params", lambda page: {
        "websiteURL": "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
        "websiteKey": "site-key",
        "isEnterprise": True,
        "recaptchaDataSValue": "data-s-token",
        "enterprisePayload": {"s": "data-s-token"},
    })
    monkeypatch.setattr(_capmonster, "_get_page_user_agent", lambda page: "ua")
    monkeypatch.setattr(_capmonster, "_get_page_cookies", lambda page: "a=b")
    monkeypatch.setattr(_capmonster, "_create_capmonster_task", fake_create_task)
    monkeypatch.setattr(_capmonster, "_poll_capmonster_result", fake_poll)
    monkeypatch.setattr(_capmonster, "_capmonster_client_key", lambda: "client-key")
    monkeypatch.setenv("CAPMONSTER_PROXY_URL", "http://user:pass@gw.dataimpulse.com:823")
    monkeypatch.setenv("CAPMONSTER_RECAPTCHA_MAX_RETRY_ROUNDS", "1")
    monkeypatch.delenv("CAPMONSTER_RECAPTCHA_PROXYLESS_FALLBACK", raising=False)
    _capmonster._LAST_RECAPTCHA_UNSOLVABLE.clear()

    assert _capmonster.solve_recaptcha_with_capmonster(object(), timeout=1) is False
    assert created_tasks == [(True, False), (False, False), (False, True), (True, True)]


def test_recaptcha_retries_full_proxy_proxyless_rounds(monkeypatch):
    from core.evasion import _capmonster

    created_tasks = []

    def fake_create_task(client_key, params, user_agent, cookies, *, use_proxy=True, force_standard=False):
        created_tasks.append((use_proxy, force_standard))
        return 200 + len(created_tasks)

    def fake_poll(client_key, task_id, timeout):
        _capmonster._poll_capmonster_result.last_error_code = "ERROR_CAPTCHA_UNSOLVABLE"
        return None

    monkeypatch.setattr(_capmonster, "_extract_recaptcha_params", lambda page: {
        "websiteURL": "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
        "websiteKey": "site-key",
        "isEnterprise": True,
        "recaptchaDataSValue": "data-s-token",
        "enterprisePayload": {"s": "data-s-token"},
    })
    monkeypatch.setattr(_capmonster, "_get_page_user_agent", lambda page: "ua")
    monkeypatch.setattr(_capmonster, "_get_page_cookies", lambda page: "a=b")
    monkeypatch.setattr(_capmonster, "_create_capmonster_task", fake_create_task)
    monkeypatch.setattr(_capmonster, "_poll_capmonster_result", fake_poll)
    monkeypatch.setattr(_capmonster, "_capmonster_client_key", lambda: "client-key")
    monkeypatch.setenv("CAPMONSTER_PROXY_URL", "http://user:pass@gw.dataimpulse.com:823")
    monkeypatch.setenv("CAPMONSTER_RECAPTCHA_MAX_RETRY_ROUNDS", "3")
    monkeypatch.delenv("CAPMONSTER_RECAPTCHA_PROXYLESS_FALLBACK", raising=False)
    _capmonster._LAST_RECAPTCHA_UNSOLVABLE.clear()

    assert _capmonster.solve_recaptcha_with_capmonster(object(), timeout=1) is False
    assert created_tasks == [
        (True, False), (False, False),
        (True, False), (False, False),
        (True, False), (False, False),
        (False, True), (True, True),
    ]


def test_capsolver_recaptcha_prefers_sticky_proxy_formats(monkeypatch):
    """CapMonster/CapSolver Playwright: sticky proxy fields first; full SmartApply URL."""
    from core.evasion import _capsolver

    attempted = []

    monkeypatch.setattr(_capsolver, "_capsolver_client_key", lambda: "client-key")
    monkeypatch.setenv("CAPSOLVER_RECAPTCHA_SEND_COOKIES", "1")
    monkeypatch.setattr(
        _capsolver,
        "_extract_recaptcha_params",
        lambda page: {
            "websiteURL": "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
            "websiteKey": "site-key",
            "isEnterprise": True,
            "enterprisePayload": {"s": "data-s-token-" * 8},
            "recaptchaDataSValue": "data-s-token-" * 8,
    # This is the reCAPTCHA anchor ``sa`` action required by CapSolver.
            "pageAction": "submit",
        },
    )
    monkeypatch.setattr(_capsolver, "_get_page_user_agent", lambda page: "ua-chrome")
    monkeypatch.setattr(_capsolver, "_get_page_cookies", lambda page: "cf_clearance=abc; INDEED_CSRF=1")
    monkeypatch.setattr(
        _capsolver,
        "_capsolver_proxy_task_overlays",
        lambda: [
            ("fields http", {"proxyType": "http", "proxyAddress": "1.2.3.4", "proxyPort": 8099, "proxyLogin": "u", "proxyPassword": "p"}),
            ("http:ip:port:user:pass", {"proxy": "http:1.2.3.4:8099:u:p"}),
        ],
    )

    def fake_create(client_key, task):
        attempted.append(dict(task))
        _capsolver._create_capsolver_task.last_error_code = None
        return f"task-{len(attempted)}"

    def fake_poll(client_key, task_id, timeout):
        # First proxy encoding fails connect; second succeeds.
        if len(attempted) == 1:
            _capsolver._poll_capsolver_result.last_error_code = "ERROR_PROXY_CONNECT_REFUSED"
            return None
        _capsolver._poll_capsolver_result.last_error_code = None
        return {"gRecaptchaResponse": "token-ok"}

    monkeypatch.setattr(_capsolver, "_create_capsolver_task", fake_create)
    monkeypatch.setattr(_capsolver, "_poll_capsolver_result", fake_poll)
    monkeypatch.setattr(
        _capsolver,
        "_finalize_capsolver_recaptcha_token",
        lambda page, solution: solution.get("gRecaptchaResponse") == "token-ok",
    )
    monkeypatch.delenv("CAPSOLVER_RECAPTCHA_PROXYLESS_FALLBACK", raising=False)

    assert _capsolver.solve_recaptcha_with_capsolver(object(), timeout=30) is True
    assert len(attempted) == 2
    assert attempted[0]["type"] == "ReCaptchaV2EnterpriseTask"
    assert attempted[0].get("proxyType") == "http"
    # CapSolver docs + CapMonster: full page URL, not origin-only.
    assert "review-module" in attempted[0]["websiteURL"]
    assert attempted[0].get("userAgent") == "ua-chrome"
    assert attempted[0].get("cookies")
    assert attempted[0].get("enterprisePayload", {}).get("s") == "data-s-token-" * 8
    assert attempted[0]["pageAction"] == "submit"
    assert attempted[1]["proxy"] == "http:1.2.3.4:8099:u:p"
    assert all("ProxyLess" not in t["type"] for t in attempted)


def test_capsolver_recaptcha_skips_proxyless_for_enterprise_with_proxy(monkeypatch):
    """Indeed IP-binds tokens — never ProxyLess when sticky Webshare is present."""
    from core.evasion import _capsolver

    attempted = []

    monkeypatch.setattr(_capsolver, "_capsolver_client_key", lambda: "client-key")
    monkeypatch.setattr(
        _capsolver,
        "_extract_recaptcha_params",
        lambda page: {
            "websiteURL": "https://www.indeed.com/",
            "websiteKey": "site-key",
            "isEnterprise": True,
        },
    )
    monkeypatch.setattr(_capsolver, "_get_page_user_agent", lambda page: "ua")
    monkeypatch.setattr(_capsolver, "_get_page_cookies", lambda page: "")
    monkeypatch.setattr(
        _capsolver,
        "_capsolver_proxy_task_overlays",
        lambda: [("ip:port:user:pass", {"proxy": "1.2.3.4:8099:u:p"})],
    )

    def fake_create(client_key, task):
        attempted.append(task["type"])
        _capsolver._create_capsolver_task.last_error_code = None
        return "task-1"

    def fake_poll(client_key, task_id, timeout):
        _capsolver._poll_capsolver_result.last_error_code = "ERROR_CAPTCHA_UNSOLVABLE"
        return None

    monkeypatch.setattr(_capsolver, "_create_capsolver_task", fake_create)
    monkeypatch.setattr(_capsolver, "_poll_capsolver_result", fake_poll)
    # Even with fallback=1, Indeed must not ProxyLess.
    monkeypatch.setenv("CAPSOLVER_RECAPTCHA_PROXYLESS_FALLBACK", "1")

    assert _capsolver.solve_recaptcha_with_capsolver(object(), timeout=10) is False
    # Enterprise first, then standard V2 fallback on sticky proxy (no ProxyLess).
    assert "ReCaptchaV2EnterpriseTask" in attempted
    assert "ReCaptchaV2Task" in attempted
    assert all("ProxyLess" not in t for t in attempted)


def test_capsolver_enterprise_unsupported_falls_back_to_v2(monkeypatch):
    from core.evasion import _capsolver

    attempted = []

    monkeypatch.setattr(_capsolver, "_capsolver_client_key", lambda: "client-key")
    monkeypatch.setattr(
        _capsolver,
        "_extract_recaptcha_params",
        lambda page: {
            "websiteURL": "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
            "websiteKey": "site-key",
            "isEnterprise": True,
            "enterprisePayload": {"s": "s-token"},
            "recaptchaDataSValue": "s-token",
        },
    )
    monkeypatch.setattr(_capsolver, "_get_page_user_agent", lambda page: "ua")
    monkeypatch.setattr(_capsolver, "_get_page_cookies", lambda page: "a=b")
    monkeypatch.setattr(
        _capsolver,
        "_capsolver_proxy_task_overlays",
        lambda: [("fields http", {"proxyType": "http", "proxyAddress": "1.2.3.4", "proxyPort": 8099, "proxyLogin": "u", "proxyPassword": "p"})],
    )

    def fake_create(client_key, task):
        attempted.append(dict(task))
        if task["type"].endswith("EnterpriseTask"):
            _capsolver._create_capsolver_task.last_error_code = (
                "ERROR_INVALID_TASK_DATA: We don't support this service"
            )
            return None
        _capsolver._create_capsolver_task.last_error_code = None
        return "task-v2"

    def fake_poll(client_key, task_id, timeout):
        _capsolver._poll_capsolver_result.last_error_code = None
        return {"gRecaptchaResponse": "token-v2-ok"}

    monkeypatch.setattr(_capsolver, "_create_capsolver_task", fake_create)
    monkeypatch.setattr(_capsolver, "_poll_capsolver_result", fake_poll)
    monkeypatch.setattr(
        _capsolver,
        "_finalize_capsolver_recaptcha_token",
        lambda page, solution: True,
    )
    monkeypatch.setenv("CAPSOLVER_RECAPTCHA_PROXYLESS_FALLBACK", "1")

    assert _capsolver.solve_recaptcha_with_capsolver(object(), timeout=30) is True
    types = [t["type"] for t in attempted]
    assert types[0] == "ReCaptchaV2EnterpriseTask"
    assert "ReCaptchaV2Task" in types
    v2 = next(t for t in attempted if t["type"] == "ReCaptchaV2Task")
    # CapMonster force_standard: s as recaptchaDataSValue, not enterprisePayload.
    assert v2.get("recaptchaDataSValue") == "s-token"
    assert "enterprisePayload" not in v2
    assert all("ProxyLess" not in t["type"] for t in attempted)


def test_capsolver_indeed_requires_sticky_proxy(monkeypatch):
    from core.evasion import _capsolver

    monkeypatch.setattr(_capsolver, "_capsolver_client_key", lambda: "client-key")
    monkeypatch.setattr(
        _capsolver,
        "_extract_recaptcha_params",
        lambda page: {
            "websiteURL": "https://smartapply.indeed.com/form/review",
            "websiteKey": "site-key",
            "isEnterprise": True,
        },
    )
    monkeypatch.setattr(_capsolver, "_get_page_user_agent", lambda page: "ua")
    monkeypatch.setattr(_capsolver, "_get_page_cookies", lambda page: "")
    monkeypatch.setattr(_capsolver, "_capsolver_proxy_task_overlays", lambda: [])

    assert _capsolver.solve_recaptcha_with_capsolver(object(), timeout=10) is False


def test_capsolver_anticloudflare_applies_cf_clearance(monkeypatch):
    from core.evasion import _capsolver

    class FakePage:
        url = "https://smartapply.indeed.com/beta/indeedapply/form/review-module"

        def evaluate(self, *_a, **_k):
            return self.url

        def reload(self, **_k):
            return None

        def goto(self, *_a, **_k):
            return None

    monkeypatch.setattr(_capsolver, "_capsolver_client_key", lambda: "client-key")
    monkeypatch.setattr(
        _capsolver,
        "_capsolver_anticloudflare_proxy_overlays",
        lambda: [("ip:port:user:pass", {"proxy": "1.2.3.4:8099:u:p"})],
    )
    monkeypatch.setattr(_capsolver, "_get_page_user_agent", lambda page: "Mozilla/5.0 Test")
    monkeypatch.setattr(
        _capsolver,
        "_page_html_for_cf",
        lambda page: "<html><title>Just a moment...</title><body>cf-challenge</body></html>",
    )

    created = []

    def fake_create(client_key, task):
        created.append(dict(task))
        _capsolver._create_capsolver_task.last_error_code = None
        return "cf-task-1"

    def fake_poll(client_key, task_id, timeout):
        return {
            "cookies": {"cf_clearance": "clearance-token-xyz"},
            "token": "clearance-token-xyz",
            "userAgent": "Mozilla/5.0 Test",
        }

    applied = {"ok": False}

    monkeypatch.setattr(_capsolver, "_create_capsolver_task", fake_create)
    monkeypatch.setattr(_capsolver, "_poll_capsolver_result", fake_poll)
    monkeypatch.setattr(
        _capsolver,
        "_apply_capmonster_cf_clearance",
        lambda page, value: applied.__setitem__("ok", value == "clearance-token-xyz") or True,
    )
    monkeypatch.setattr(
        "jobbots.core.evasion._capmonster._apply_capmonster_cookies",
        lambda page, cookies: None,
    )
    monkeypatch.setattr(
        "jobbots.core.evasion._capmonster._cloudflare_clearance_accept_wait_seconds",
        lambda default=30: 1,
    )
    monkeypatch.setattr(
        "jobbots.core.evasion._capmonster._wait_for_cloudflare_clearance",
        lambda page, timeout=1: True,
    )
    monkeypatch.setattr(
        "jobbots.core.evasion._detection.is_cloudflare_challenge",
        lambda page: False,
    )

    assert _capsolver.solve_cloudflare_challenge_with_capsolver(FakePage(), timeout=30) is True
    assert created and created[0]["type"] == "AntiCloudflareTask"
    assert created[0]["proxy"] == "1.2.3.4:8099:u:p"
    assert "html" in created[0]
    assert applied["ok"] is True


def test_captcha_submit_ready_does_not_block_submission(monkeypatch):
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    target_dir = repo / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))

    import modules
    shared_path = str(repo / "jobbots" / "core" / "shared_modules")
    if shared_path not in getattr(modules, "__path__", []):
        modules.__path__.append(shared_path)

    from jobbots.core.evasion._handlers import handle_recaptcha_challenge, handle_recaptcha_widget
    from jobbots.core.shared_modules.indeed.smartapply import _captcha_still_blocking

    class DummyPage:
        url = "https://smartapply.indeed.com/beta/indeedapply/form/review-module"

    page = DummyPage()
    monkeypatch.setattr("jobbots.core.evasion._handlers._indeed_submit_button_ready", lambda p: True)
    monkeypatch.setattr("jobbots.core.shared_modules.indeed.smartapply._is_submit_button_ready", lambda p: True)
    monkeypatch.setattr("jobbots.core.evasion._handlers.is_recaptcha_challenge", lambda p: True)

    assert handle_recaptcha_challenge(page, None) is True
    assert handle_recaptcha_widget(page, None) is True
    assert _captcha_still_blocking(page) is False


def test_nstbrowser_profile_rotation_round_robin(tmp_path, monkeypatch):
    from core.bootstrap_bot_launch import _select_rotated_nstbrowser_profile

    monkeypatch.setenv("NSTBROWSER_PROFILE_ID_INDEED_GENERAL_POOL", "profile-a, profile-b,profile-c")

    assert _select_rotated_nstbrowser_profile(tmp_path, "indeed_general") == "profile-a"
    assert _select_rotated_nstbrowser_profile(tmp_path, "indeed_general") == "profile-b"
    assert _select_rotated_nstbrowser_profile(tmp_path, "indeed_general") == "profile-c"
    assert _select_rotated_nstbrowser_profile(tmp_path, "indeed_general") == "profile-a"


def test_dataimpulse_proxy_url_uses_explicit_sticky_session():
    from core.bootstrap_bot_launch import _dataimpulse_session_proxy_url

    rotated = _dataimpulse_session_proxy_url(
        "http://user__cr.ca:pass@gw.dataimpulse.com:823",
        "indeed-general-sticky-2",
    )

    assert "user__cr.ca;indeed-general-sticky-2" in rotated
    assert "gw.dataimpulse.com:823" in rotated


def test_daily_limit_flag_present_false(tmp_path):
    from core.supervisor_chrome import daily_limit_flag_present

    base = tmp_path / "monorepo"
    base.mkdir()
    assert daily_limit_flag_present("indeed_it", base) is False


def test_local_easy_apply_gate():
    import sys
    from pathlib import Path
    
    repo = Path(__file__).resolve().parents[2]
    target_dir = repo / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))
        
    import modules
    shared_path = str(repo / "jobbots" / "core" / "shared_modules")
    if shared_path not in getattr(modules, "__path__", []):
        modules.__path__.append(shared_path)

    import jobbots.core.shared_modules.indeed as indeed_pkg
    _obvious_non_it_reject = indeed_pkg._obvious_non_it_reject

    # Non-IT roles should be rejected
    rejected_titles = [
        "Maintenance Technician",
        "Maintenance Technician for Window Manufacturing Plant",
        "Quality Assurance Technician",
        "Quality Assurance Technician - 3 to 6 Month Contract",
        "Telecom & Security Technician - TELUS Telecom Installer Team",
        "Electrical Field Service Technician",
    ]
    for title in rejected_titles:
        rejected, reason = _obvious_non_it_reject(
            title=title, company="Test Company", location="Vancouver, BC",
            card_text="", job_details=""
        )
        assert rejected is True, f"Expected '{title}' to be rejected, but it was approved. Reason: {reason}"

    # Valid IT roles should NOT be rejected by obvious non-IT checks
    approved_titles = [
        "QA Analyst",
        "IT Support Specialist",
        "Network Administrator",
        "Software Engineer",
    ]
    for title in approved_titles:
        rejected, reason = _obvious_non_it_reject(
            title=title, company="Test Company", location="Vancouver, BC",
            card_text="", job_details=""
        )
        assert rejected is False, f"Expected '{title}' NOT to be rejected, but it was rejected. Reason: {reason}"


def test_easy_apply_gate_never_calls_ai(monkeypatch):
    import jobbots.core.shared_modules.indeed as indeed_pkg

    def unexpected_ai_call(_title):
        raise AssertionError("Easy Apply screening must remain local")

    monkeypatch.setattr(indeed_pkg, "_ai_title_is_it_role", unexpected_ai_call)

    approved, reason = indeed_pkg._local_easy_apply_gate_should_apply(
        title="Technology Enablement Associate",
        company="Example",
        location="Vancouver, BC",
        card_text="",
        job_details="",
    )

    assert approved is False
    # Unsure titles defer to Phase I batch AI (not an inline AI call here).
    assert (
        "ambiguous_title" in reason
        or "AI prescreen intentionally skipped" in reason
        or "no explicit IT phrase" in reason
    )

    approved, reason = indeed_pkg._local_easy_apply_gate_should_apply(
        title="Technology Enablement Associate",
        company="Example",
        location="Vancouver, BC",
        card_text="Troubleshoot SaaS platform issues and manage support tickets",
        job_details="Work with APIs, SQL, and cloud applications.",
    )

    assert approved is True
    assert "multiple technical signals" in reason


def test_easy_apply_rejects_jd_keyword_bleed_non_it():
    """JD tech jargon must not approve HR/marketing/construction titles (2026-07-31)."""
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    target_dir = repo / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))
    import modules
    shared_path = str(repo / "jobbots" / "core" / "shared_modules")
    if shared_path not in getattr(modules, "__path__", []):
        modules.__path__.append(shared_path)
    import jobbots.core.shared_modules.indeed as indeed_pkg

    jd = (
        "software systems platform application tickets data analytics "
        "cloud api troubleshooting technical"
    )
    bad_titles = (
        "Human Resources Specialist",
        "Digital Marketer - AI Search",
        "Events Coordinator",
        "Project Geotechnical Engineer",
        "Construction - Technologist, Virtual Design & Construction Services",
        "Student Financial Services & Enrollment Coordinator",
        "Retail Operations Analyst",
    )
    for title in bad_titles:
        approved, reason = indeed_pkg._local_easy_apply_gate_should_apply(
            title, "Example Co", "Vancouver, BC", "", jd,
        )
        assert approved is False, f"Should reject {title!r}: {reason}"


def test_classify_region_rejects_richmond_hill():
    from core.discovery.classification.location_policy import (
        classify_region,
        REGION_METRO_VAN,
        REGION_OTHER,
    )

    assert classify_region("Richmond Hill") == REGION_OTHER
    assert classify_region("Richmond Hill, ON") == REGION_OTHER
    assert classify_region("Richmond, BC") == REGION_METRO_VAN
    assert classify_region("Vancouver") == REGION_METRO_VAN
    assert classify_region("Montreal") == REGION_OTHER


def test_easy_apply_historical_it_title_recall():
    import jobbots.core.shared_modules.indeed as indeed_pkg

    historical_good_titles = (
        "IT Administrator",
        "IT Specialist #1010470",
        "User Support Technician",
        "POS Support Specialist_Vancouver",
        "Junior Customer & Product Support Specialist",
        "Technical Analyst - Network",
        "Cloud Solutions Analyst",
        "Business Systems Analyst",
        "Application Analyst",
        "Information Systems Analyst",
        "Data Warehouse Analyst - FTT",
        "AI Integration & Automation Specialist",
        "Embedded Firmware Developer (Hybrid)",
        "Computer Vision Engineer – UAV Systems",
        "Algorithm Engineer Intern",
        "Quality Engineering Co-Op",
        "Software Analyst Intern (Fall 2026, 4/8/12 months)",
        "Technical Privacy Analyst",
        "Technical Writer",
        "Computer Repair Technician",
    )

    for title in historical_good_titles:
        approved, reason = indeed_pkg._local_easy_apply_gate_should_apply(
            title, "Historical Company", "Canada", "", ""
        )
        assert approved is True, f"Missed historical IT title {title!r}: {reason}"

    historical_bad_titles = (
        "Quality Assurance Technician",
        "Inventory - Field Data Collector",
        "IT/SEO & Digital Marketing Specialist",
        "Office Assistant - Customer Service and Technical Support (Full Time)",
        "Telecom & Security Technician - TELUS Telecom Installer Team",
        "Customer Support & Sales Specialist",
        "Senior System Administrator",
    )

    for title in historical_bad_titles:
        approved, reason = indeed_pkg._local_easy_apply_gate_should_apply(
            title, "Historical Company", "Canada", "", ""
        )
        assert approved is False, f"Unexpected low-quality approval {title!r}: {reason}"


def test_clearance_is_not_a_local_blocker_and_company_site_gate_is_strict():
    import jobbots.core.shared_modules.indeed as indeed_pkg

    rejected, reason = indeed_pkg._obvious_non_it_reject(
        "Cyber Security Analyst", "Example", "Ottawa, ON", "",
        "Must be eligible for Top Secret security clearance and a polygraph.",
    )
    assert rejected is False, reason

    decision, reason = indeed_pkg._local_company_site_gate(
        "Junior Cloud Support Analyst", "Example", "Canada", "",
        "Support AWS cloud applications, Linux systems, APIs, and customer tickets. Maintain systems administration and troubleshoot customer inquiries in a professional and timely manner. Technical experience with networks and databases is required.",
    )
    assert decision == "approve", reason

    decision, reason = indeed_pkg._local_company_site_gate(
        "Systems Administrator", "Example", "Canada", "",
        "Required: at least 7 years of systems administration experience.",
    )
    assert decision == "reject", reason

    decision, reason = indeed_pkg._local_company_site_gate(
        "Technology Specialist", "Example", "Canada", "", "",
    )
    assert decision == "ai", reason


def test_crawlee_queue_helper(tmp_path):
    import sys
    from pathlib import Path
    
    repo = Path(__file__).resolve().parents[2]
    target_dir = repo / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))
        
    import modules
    shared_path = str(repo / "jobbots" / "core" / "shared_modules")
    if shared_path not in getattr(modules, "__path__", []):
        modules.__path__.append(shared_path)

    import jobbots.core.shared_modules.indeed as indeed_pkg
    CrawleeQueueHelper = indeed_pkg.loop.CrawleeQueueHelper

    import uuid
    queue_name = f"test-queue-{uuid.uuid4().hex}"
    queue = CrawleeQueueHelper(queue_name)
    queue.open()
    
    jid = "test_job_123"
    # Ensure it starts as not present
    assert queue.has_job(jid) is False
    
    # Add the job
    queue.add_job(jid, "https://example.com/job", {"title": "Test Job"})
    
    # Now it should be present
    assert queue.has_job(jid) is True
    assert queue.has_job("non_existent_jid") is False
