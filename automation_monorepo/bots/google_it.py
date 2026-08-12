#!/usr/bin/env python3
"""Phase II applier for Greenhouse / Lever / Ashby / BambooHR (and Google ATS leads).

No NST browser. No residential proxy. Public board forms only — stock Chromium
via Playwright. Using NST/proxy here burns quota for zero benefit.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _clear_proxy_and_nst() -> None:
    """Strip browser residential proxies / NST — keep CapMonster API secrets.

    BambooHR + Lever reCAPTCHA v2 is solved via CapMonster Cloud API
    (``CAPMONSTER_API_KEY``). CapMonster may use ``CAPMONSTER_PROXY_URL`` for
    the *task* (worker IP alignment), but the Playwright browser itself must
    stay direct egress so we never burn NST/Webshare on public board forms.
    """
    for k in (
        "PROXY_URL", "PROXY_CHEAP_URL", "WEBSHARE_PROXY_URL",
        "JOBSPY_PROXY_WEBSHARE", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy",
        "NSTBROWSER_API_KEY", "NSTBROWSER_API_KEY_2", "NST_API_KEY",
        "NSTBROWSER_PROFILE_ID", "NST_PROFILE_ID",
    ):
        os.environ.pop(k, None)
    for k in list(os.environ):
        if k.startswith("NSTBROWSER_PROFILE"):
            os.environ.pop(k, None)
    os.environ["BROWSER_VENDOR"] = "playwright"


def _enable_ats_capmonster() -> None:
    """Force CapMonster on for ATS reCAPTCHA v2 (Bamboo/Lever/Greenhouse)."""
    os.environ.setdefault("USE_CAPMONSTER_CAPTCHA_SOLVER", "1")
    os.environ.setdefault("CAPTCHA_USE_CAPMONSTER", "1")
    os.environ.setdefault("USE_CAPMONSTER", "1")
    # Public boards: prefer proxyless reCAPTCHA; fall back to CAPMONSTER_PROXY_URL
    # when CapMonster returns ERROR_CAPTCHA_UNSOLVABLE with no proxy.
    os.environ.setdefault("CAPMONSTER_RECAPTCHA_PROXYLESS_FALLBACK", "1")
    # Unattended farm: CapMonster only (no 90s human wait on headless VM).
    os.environ.setdefault("ATS_CAPTCHA_ALLOW_HUMAN_WAIT", "0")
    os.environ.setdefault("DISABLE_GUI_CAPTCHA", "1")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))

    from core.supervisor_runtime import merge_dotenv_into_env

    merge_dotenv_into_env(os.environ, root / ".env")
    # After dotenv: force ATS path clean (dotenv may re-inject PROXY/NST).
    _clear_proxy_and_nst()
    _enable_ats_capmonster()
    os.environ.setdefault("JOB_PROFILE", "IT")
    # Unattended default: headless Chromium (override ATS_HEADLESS=0 to watch).
    os.environ.setdefault("ATS_HEADLESS", "1")

    job_raw = (os.getenv("JOB_QUEUE_DIRECT_JOB") or "").strip()
    if not job_raw:
        print("[google_it] JOB_QUEUE_DIRECT_JOB missing", file=sys.stderr)
        return 2
    job = json.loads(job_raw)
    meta = job.get("metadata") or {}
    url = (
        (job.get("url") or "").strip()
        or (job.get("destination_url") or "").strip()
        or (meta.get("destination_url") or "").strip()
    )
    title = job.get("title") or ""
    company = job.get("company") or ""
    if not url:
        from core.shared_modules.queue_result import write_queue_result

        write_queue_result("failed", reason="google_it: missing apply url")
        return 2

    from core.shared_modules.ats_apply import apply_url, is_greenhouse_or_lever_url
    from core.shared_modules.queue_result import write_queue_result
    from core.ats.registry import detect_platform

    platform = detect_platform(url) or "ats"
    if not is_greenhouse_or_lever_url(url) and not detect_platform(url) and "apply" not in url.lower() and "job" not in url.lower():
        write_queue_result(
            "failed",
            result_url=url,
            reason=f"google_it: unsupported ATS url: {url}",
            application_method="company_site",
        )
        return 2

    # CapMonster key must be present for Bamboo/Lever reCAPTCHA v2.
    try:
        from core.secret_manager import get_secret
        cm_key = (
            get_secret("CAPMONSTER_API_KEY")
            or get_secret("CAPMONSTER_CLIENT_KEY")
            or os.getenv("CAPMONSTER_API_KEY")
            or os.getenv("CAPMONSTER_CLIENT_KEY")
            or ""
        ).strip()
    except Exception:
        cm_key = (os.getenv("CAPMONSTER_API_KEY") or os.getenv("CAPMONSTER_CLIENT_KEY") or "").strip()
    if cm_key and not os.getenv("CAPMONSTER_API_KEY"):
        os.environ["CAPMONSTER_API_KEY"] = cm_key
    print(
        f"[google_it] Playwright ATS apply platform={platform} "
        f"(no NST browser proxy; CapMonster={'on' if cm_key else 'OFF — reCAPTCHA will fail'}): "
        f"{title} @ {company}"
    )
    from playwright.sync_api import sync_playwright

    headless = str(os.getenv("ATS_HEADLESS") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    with sync_playwright() as p:
        # No proxy= arg — stock Chromium direct egress (never residential).
        # CapMonster solves reCAPTCHA v2 out-of-band via API, not browser proxy.
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="en-CA", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            ok, result_url, reason = apply_url(page, url, title=title, company=company)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    if ok:
        write_queue_result(
            "applied",
            result_url=result_url or url,
            reason=reason or "ATS application submitted",
            application_method="company_site",
        )
        print(f"[google_it] APPLIED {title} @ {company} → {result_url}")
        return 0

    status = "failed"
    reason_l = (reason or "").lower()
    if "already applied" in reason_l:
        write_queue_result(
            "applied",
            result_url=result_url or url,
            reason=reason,
            application_method="company_site",
        )
        print(f"[google_it] ALREADY APPLIED {title}")
        return 0
    write_queue_result(
        status,
        result_url=result_url or url,
        reason=reason or "ATS apply failed",
        application_method="company_site",
    )
    print(f"[google_it] FAILED {title}: {reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
