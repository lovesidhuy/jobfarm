from __future__ import annotations

from ._bootstrap import *  # noqa: F403

def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _ensure_tailored_resume_before_easy_apply(page, job_id, title, company) -> None:
    """Generate a tailored resume only when we are about to submit Easy Apply.

    Bookmark-only / company-site save / verify-external paths must never reach
    this helper. Worth-save checks happen before apply and must not trigger it.
    """
    if _env_flag("JOB_QUEUE_BOOKMARK_ONLY"):
        return
    if os.getenv("INDEED_TAILORED_RESUME_PATH"):
        return
    # Optional hard skip for save-check / dry runs
    if _env_flag("SKIP_RESUME_TAILOR") or _env_flag("INDEED_SKIP_RESUME_TAILOR"):
        print_lg("  [Resume Tailor] Skipped (SKIP_RESUME_TAILOR set).")
        return
    description = os.getenv("INDEED_JOB_DESCRIPTION_FOR_TAILOR") or ""
    try:
        from jobbots.core.shared_modules.indeed.loop import _tailor_resume_and_set_path
        _tailor_resume_and_set_path(title, company, description, job_id, page)
    except Exception as exc:
        print_lg(f"  [Resume Tailor] Deferred tailor failed (continuing apply): {exc}")


def _is_already_applied_on_page(page) -> bool:
    try:
        # Explicit Applied CTA / badge
        for sel in (
            "button:has-text('Applied')",
            "[data-testid*='applied' i]",
            "[aria-label*='Applied' i]",
            "span:has-text('Applied')",
            "div:has-text(\"You've applied\")",
        ):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    txt = (el.inner_text() or "").strip().lower()
                    if "applied" in txt and "easily apply" not in txt and "apply now" not in txt:
                        return True
            except Exception:
                continue
        for btn in page.query_selector_all("button, a, span[role='button']"):
            try:
                if not btn.is_visible():
                    continue
                txt = (btn.inner_text() or "").strip().lower()
                aria = (btn.get_attribute("aria-label") or "").strip().lower()
                if txt == "applied" or aria == "applied" or txt.startswith("applied "):
                    return True
                if "you've applied" in txt or "application submitted" in txt:
                    return True
            except Exception:
                continue
        content = (page.content() or "").lower()
        for kw in _ALREADY_APPLIED_KEYWORDS:
            if kw in content:
                return True
        if "you've applied to this job" in content or "you applied to this job" in content:
            return True
    except Exception:
        pass
    return False


def _smartapply_surface_ready(page) -> bool:
    """True when SmartApply is open as URL, iframe, or modal on this page."""
    try:
        url = (page.url or "").lower()
        if SMARTAPPLY_DOMAIN in url or "indeedapply" in url:
            return True
        # iframes hosting SmartApply
        for frame in page.frames:
            try:
                furl = (frame.url or "").lower()
                if SMARTAPPLY_DOMAIN in furl or "indeedapply" in furl:
                    return True
            except Exception:
                continue
        # modal / widget DOM
        for sel in (
            "iframe[src*='smartapply.indeed.com']",
            "iframe[src*='indeedapply']",
            "iframe[id*='indeed-apply']",
            "iframe[name*='indeed-apply']",
            "[data-testid*='indeed-apply']",
            ".indeed-apply-popup",
            "#indeed-apply-popup",
            "div[class*='indeed-apply']",
        ):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                continue
        # body text cues while still on viewjob (slow SPA)
        try:
            body = page.query_selector("body")
            text = ((body.inner_text() if body else "") or "")[:3000].lower()
            if "contact information" in text and "resume" in text and "continue" in text:
                return True
            if "add a resume for the employer" in text or "select a resume" in text:
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _find_open_smartapply_page(context, main_page):
    """Return a Page that looks like SmartApply (new tab or main), else None."""
    pages = []
    try:
        pages = list(context.pages)
    except Exception:
        pages = [main_page]
    # Prefer non-main pages first (new tab), then main
    ordered = [p for p in pages if p is not main_page] + [main_page]
    for p in ordered:
        try:
            if _smartapply_surface_ready(p):
                return p
        except Exception:
            continue
    return None


def _finish_smartapply(page, sb, job_id, title) -> tuple:
    """Run SmartApply and map already-applied → applied success."""
    success, application_link = _automate_smartapply(page, sb, job_id, title)
    if success:
        return True, application_link, ""
    # Prefer explicit status flag when set
    status = ""
    try:
        from jobbots.core.shared_modules.indeed import smartapply as _sa
        status = (getattr(_sa, "_last_smartapply_status", "") or "").strip().lower()
    except Exception:
        status = ""
    if status == "already_applied" or "already applied" in (status or ""):
        return True, application_link or page.url, "Already applied to this job"
    if status in ("skipped_cover_letter", "skipped_cover_letter_screen") or "cover letter" in (status or ""):
        return False, application_link or page.url, "Cover letter screen — skipped by policy"
    # CAPTCHA / CF must keep the word "captcha" so Phase-II requeues (not dead)
    if "captcha" in (status or "") or "cloudflare" in (status or ""):
        return (
            False,
            application_link or page.url,
            f"CAPTCHA failed or still blocking ({status or 'challenge'}); requeue for retry",
        )
    # Fallback: notice still visible
    try:
        if _is_already_applied_notice(page) or _is_already_applied_on_page(page):
            return True, application_link or page.url, "Already applied to this job"
    except Exception:
        pass
    # If page still shows challenge UI, classify as captcha even without status flag
    try:
        from jobbots.core.shared_modules.indeed.smartapply import _captcha_still_blocking
        if _captcha_still_blocking(page):
            return (
                False,
                application_link or page.url,
                "CAPTCHA still visible after SmartApply exit; requeue for retry",
            )
    except Exception:
        pass
    return False, application_link or page.url, "SmartApply form automation failed"


def _apply_to_single_job(main_page, sb, job_id, title, company,
                         location, job_href, search_url) -> tuple:
    """Navigate to the job page and attempt to apply.
    Returns (applied, application_link, reason)."""
    from .smartapply import (
        _captcha_still_blocking,
        _captcha_failure_reason,
        _is_submitted,
    )

    context = main_page.context

    job_link, job_href = _preferred_job_urls(job_id, job_href)
    pane_url = _search_pane_job_url(search_url, job_id)
    nav_candidates = [job_link]
    if pane_url and pane_url not in nav_candidates:
        nav_candidates.append(pane_url)
    if job_href and job_href != job_link:
        nav_candidates.append(job_href)

    smartapply_direct = None
    apply_btn = None
    is_easy = False
    nav_error = None
    nav_url = job_link

    if _page_ready_for_job_apply(main_page, job_id, title, nav_candidates):
        nav_loop = [None] + nav_candidates
    else:
        nav_loop = nav_candidates

    for candidate in nav_loop:
        if candidate is None:
            nav_url = main_page.url
        else:
            nav_url = candidate
            try:
                _goto_page(main_page, nav_url, timeout=15000)
                time.sleep(_T_ACTION)
            except Exception as e:
                nav_error = e
                continue

        # CAPTCHA check after navigating to job page
        captcha_seen = check_and_handle_captcha(main_page, sb, context=f"job page {job_id}",
                                                run_in_background=run_in_background)
        main_page = try_recover_page(main_page)
        if captcha_seen and _captcha_still_blocking(main_page):
            return False, job_link, _captcha_failure_reason("job page")
        if not _page_has_job_detail(main_page):
            _open_job_detail_from_results(main_page, job_id, title)
            main_page = try_recover_page(main_page)
        if _is_already_applied_on_page(main_page):
            print_lg(f"  [Indeed] Job '{title}' is already applied on Indeed page.")
            return True, job_link, "Already applied to this job"
        smartapply_direct = _get_smartapply_link_from_page(main_page)
        apply_btn, is_easy = _find_apply_button(main_page)
        if apply_btn is None and not smartapply_direct:
            apply_btn, is_easy = _accessible_apply_button(main_page)
        if apply_btn is not None or smartapply_direct:
            break

    if apply_btn is None and not smartapply_direct and nav_error and len(nav_candidates) == 1:
        return False, job_link, f"Navigation error: {nav_error}"

    if apply_btn is None and not smartapply_direct:
        if os.getenv("JOB_QUEUE_VERIFY_APPLY_TYPE", "").strip().lower() in {"1","true","yes","on"}:
            # A verify visit with no Indeed apply control is an external lead.
            # Save it rather than leaving it in an unresolved/manual state.
            _save_job_on_indeed(main_page, job_id, title)
            return False, job_link, "Company-site bookmarked (verify: no Easy Apply/SmartApply)"
        debug_lines = _debug_visible_apply_elements(main_page)
        if debug_lines:
            print_lg("  [Indeed] Visible apply-like elements on page:")
            for line in debug_lines:
                print_lg(f"    {line}")
        # Last chance: already applied with no Apply CTA
        if _is_already_applied_on_page(main_page):
            print_lg(f"  [Indeed] No Apply CTA but already-applied markers for '{title}'.")
            return True, job_link, "Already applied to this job"
        _screenshot(main_page, job_id, "Apply button not found")
        return False, job_link, "Apply button not found"

    application_link = job_link

    verify_apply_type = os.getenv("JOB_QUEUE_VERIFY_APPLY_TYPE", "").strip().lower() in {"1","true","yes","on"}
    if verify_apply_type and not is_easy and not smartapply_direct:
        # The page has verified an external/company-site listing. Save it as a
        # lead and stop; never apply through the employer website.
        _save_job_on_indeed(main_page, job_id, title)
        print_lg(f"  [Indeed] Verify route: '{title}' is company-site → saved as lead.")
        return False, job_link, "Company-site bookmarked (verify: external apply)"

    if os.getenv("JOB_QUEUE_BOOKMARK_FIRST", "").strip().lower() in {"1","true","yes","on"}:
        _save_job_on_indeed(main_page, job_id, title)
        if os.getenv("JOB_QUEUE_BOOKMARK_ONLY", "").strip().lower() in {"1","true","yes","on"}:
            return False, job_link, "Company-site bookmarked"
        # Metro-Van lease-and-verify: submit ONLY through Indeed Easy Apply /
        # SmartApply. If neither is present, the job is external/company-site —
        # it's already bookmarked above, so return without ever submitting
        # through a company website.

    # Save/bookmark company-site jobs on Indeed first
    if not is_easy and not smartapply_direct and save_company_site_jobs:
        print_lg(f"  [Indeed] Bookmarking company site job '{title}' on Indeed...")
        _save_job_on_indeed(main_page, job_id, title)

    # About to click apply — tailor whenever we have an apply control that may
    # open SmartApply. Do NOT gate only on is_easy: misclassified Easy Apply
    # buttons previously skipped tailor and then accepted Indeed's leftover
    # Cloud9 (or other) pre-selected resume.
    if apply_btn is not None or smartapply_direct:
        if not is_easy and not smartapply_direct:
            print_lg(
                "  [Resume Tailor] Apply control present but not flagged Easy Apply — "
                "still preparing tailored resume before click."
            )
        _ensure_tailored_resume_before_easy_apply(main_page, job_id, title, company)

    # ── Click apply — try to detect new tab ──────────────────────────────
    new_tab = None
    try:
        with context.expect_page(timeout=20000) as new_page_info:
            if apply_btn:
                apply_btn.click(force=True)
            elif smartapply_direct:
                main_page.evaluate(f"window.open('{smartapply_direct}', '_blank')")

        new_tab = new_page_info.value
        try:
            new_tab.wait_for_load_state('domcontentloaded', timeout=15000)
        except Exception:
            pass

    except Exception:
        # No new tab within expect_page window — still may open SPA/iframe/modal
        # or a late tab. Do NOT fail at 5s; that is the flaky "no redirect" path.
        try:
            if apply_btn:
                # Click may have been missed if expect_page timed out first
                try:
                    apply_btn.click(force=True, timeout=3000)
                except Exception:
                    pass
            elif smartapply_direct:
                try:
                    main_page.evaluate(f"window.open('{smartapply_direct}', '_blank')")
                except Exception:
                    pass
        except Exception:
            pass

        captcha_seen = check_and_handle_captcha(main_page, sb, "post-apply-click same window",
                                                run_in_background=run_in_background)
        main_page = try_recover_page(main_page)
        if captcha_seen and _captcha_still_blocking(main_page):
            return False, job_link, _captcha_failure_reason("post apply click")

        # Extended poll: URL change, iframe/modal, other tabs, already-applied
        poll_seconds = int(os.getenv("INDEED_APPLY_REDIRECT_WAIT_SECONDS", "25") or "25")
        sa_page = None
        for i in range(max(poll_seconds, 5)):
            time.sleep(1)
            main_page = try_recover_page(main_page)
            if _is_already_applied_on_page(main_page):
                print_lg(f"  [Indeed] Already applied after Apply click for '{title}'.")
                return True, job_link, "Already applied to this job"
            sa_page = _find_open_smartapply_page(context, main_page)
            if sa_page is not None:
                print_lg(f"  [Indeed] SmartApply surface detected after {i + 1}s (url/iframe/tab).")
                break
            try:
                cur = (main_page.url or "").lower()
            except Exception:
                cur = ""
            # Indeed SSO hop — keep waiting for SmartApply
            if _is_indeed_property_url(cur) and ("/auth" in cur or "account" in cur):
                print_lg(f"  [Indeed] Indeed auth intermediate — waiting ({i + 1}s)…")
                continue
            # True external host (not Indeed)
            if cur and cur != (nav_url or "").lower() and not _is_indeed_property_url(cur) and "indeed.com" not in cur:
                is_signin, reason = _is_sign_in_page(main_page)
                if is_signin and skip_sign_in_jobs:
                    return False, main_page.url, f"External sign-in wall: {reason}"
                print_lg(f"  ↗ External redirect: {main_page.url}")
                # Greenhouse / Lever apply pages: fill + submit (not bookmark).
                try:
                    from modules.ats_apply import apply_on_page, is_greenhouse_or_lever_url, page_looks_like_ats_apply
                    if is_greenhouse_or_lever_url(main_page.url) or page_looks_like_ats_apply(main_page):
                        ok, result_url, ats_reason = apply_on_page(
                            main_page, title=title, company=company
                        )
                        return ok, result_url or main_page.url, ats_reason
                except Exception as ats_exc:
                    print_lg(f"  [ATS] external redirect apply failed: {ats_exc}")
                if save_company_site_jobs:
                    return True, main_page.url, "External company-site lead opened (non-ATS)"
                return False, main_page.url, "External company-site apply; skipped"

        if sa_page is not None:
            success, application_link, reason = _finish_smartapply(sa_page, sb, job_id, title)
            return success, application_link, reason

        # One more already-applied check before declaring failure
        if _is_already_applied_on_page(main_page):
            return True, job_link, "Already applied to this job"

        # Same-window SmartApply / submit (SPA never opens a new tab). Prefer
        # finishing apply over the flaky "no redirect" dead path.
        try:
            if _smartapply_surface_ready(main_page):
                print_lg("  [Indeed] SmartApply surface on same page after click.")
                return _finish_smartapply(main_page, sb, job_id, title)
        except Exception:
            pass
        try:
            if _is_submitted(main_page):
                return True, job_link, "Application submitted on same page (no new tab)"
            if _captcha_still_blocking(main_page):
                return (
                    False,
                    job_link,
                    "CAPTCHA still visible after apply click; requeue for retry",
                )
        except Exception:
            pass

        _screenshot(main_page, job_id, "Apply clicked but no redirect")
        return False, job_link, "Apply clicked but no redirect detected"

    # ── Process new tab ───────────────────────────────────────────────────
    tab_url = new_tab.url
    application_link = tab_url

    # CAPTCHA check in new tab
    captcha_seen = check_and_handle_captcha(new_tab, sb, "new apply tab", run_in_background=run_in_background)
    new_tab = try_recover_page(new_tab)
    if captcha_seen and _captcha_still_blocking(new_tab):
        try:
            new_tab.close()
        except Exception:
            pass
        return False, application_link, _captcha_failure_reason("new apply tab")
    tab_url = new_tab.url
    application_link = tab_url

    # Wait briefly if new tab is Indeed auth intermediate before SmartApply
    if _is_indeed_property_url(tab_url) and SMARTAPPLY_DOMAIN not in (tab_url or "").lower():
        for _ in range(12):
            time.sleep(1)
            try:
                tab_url = new_tab.url
            except Exception:
                break
            if SMARTAPPLY_DOMAIN in (tab_url or "").lower() or _smartapply_surface_ready(new_tab):
                break
            if _is_already_applied_on_page(new_tab) or _is_already_applied_on_page(main_page):
                try:
                    new_tab.close()
                except Exception:
                    pass
                return True, job_link, "Already applied to this job"

    if SMARTAPPLY_DOMAIN in (tab_url or "").lower() or _smartapply_surface_ready(new_tab):
        success, application_link, reason = _finish_smartapply(new_tab, sb, job_id, title)
        try:
            new_tab.close()
        except Exception:
            pass
        # Ensure we're back on the main page
        try:
            all_pages = context.pages
            if main_page not in all_pages and all_pages:
                main_page = all_pages[0]
        except Exception:
            pass
        return success, application_link, reason

    is_signin, reason = _is_sign_in_page(new_tab)
    if is_signin and skip_sign_in_jobs:
        try:
            new_tab.close()
        except Exception:
            pass
        return False, tab_url, f"External sign-in wall: {reason}"
    elif is_signin:
        print_lg(f"  ⚠ External sign-in page ({reason}). skip_sign_in_jobs=False.")
    print_lg(f"  ↗ External apply: {application_link}")

    # Greenhouse / Lever: fill + submit on the opened tab instead of bookmark-only.
    try:
        from modules.ats_apply import apply_on_page, is_greenhouse_or_lever_url, page_looks_like_ats_apply
        if is_greenhouse_or_lever_url(tab_url) or page_looks_like_ats_apply(new_tab):
            ok, result_url, ats_reason = apply_on_page(new_tab, title=title, company=company)
            try:
                new_tab.close()
            except Exception:
                pass
            try:
                all_pages = context.pages
                if main_page not in all_pages and all_pages:
                    main_page = all_pages[0]
            except Exception:
                pass
            return ok, result_url or application_link, ats_reason
    except Exception as ats_exc:
        print_lg(f"  [ATS] external tab apply failed: {ats_exc}")

    success = bool(save_company_site_jobs)
    external_reason = None if success else "External company-site apply; skipped"
    if success:
        external_reason = "External company-site lead opened (non-ATS)"

    try:
        new_tab.close()
    except Exception:
        pass

    # Ensure we're back on the main page
    try:
        all_pages = context.pages
        if main_page not in all_pages and all_pages:
            main_page = all_pages[0]
    except Exception:
        pass

    if success:
        # Non-ATS external: treated as bookmark/lead by loop (not a real submit).
        return False, application_link, external_reason or "External company-site lead opened (non-ATS)"
    if external_reason:
        return False, application_link, external_reason
    return False, application_link, "SmartApply failed"


# ─────────────────────────────────────────────────────────────────────────────
# Pagination  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _has_next_page(page) -> bool:
    try:
        page = try_recover_page(page)
        el = page.query_selector(
            "a[data-testid='pagination-page-next'], "
            "a[aria-label='Next Page'], "
            "a.np[aria-label*='Next'], "
            "[aria-label='Next']"
        )
        return el is not None and el.is_visible()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(applied: int, failed: int, skipped: int, external: int = 0) -> None:
    total = applied + external
    time_saved = (applied * 80) + (external * 20) + (skipped * 10) + 60
    time_msg = f"  Time saved: ~{round(time_saved / 60)} min ({time_saved} sec)\n" if time_saved > 60 else ""
    summary = (
        f"\n{'=' * 70}\n"
        f"  IT-Indeed Bot — Session Summary\n{'=' * 70}\n"
        f"  Applied (SmartApply) : {applied}\n"
        f"  External links saved : {external}\n"
        f"  Total                : {total}\n"
        f"  Failed               : {failed}\n"
        f"  Skipped              : {skipped}\n"
        + time_msg + f"{'=' * 70}\n"
    )
    print_lg(summary)
    # Send progress update via Telegram
    try:
        import os
        from jobbots.core.alerts import send_telegram_alert
        bot_name = os.environ.get("BOT_NAME", _bot_name)
        telegram_summary = (
            f"📊 *{bot_name.upper()} Progress Update*\n"
            f"• Applied (SmartApply): {applied}\n"
            f"• External links saved: {external}\n"
            f"• Total: {total}\n"
            f"• Failed: {failed}\n"
            f"• Skipped: {skipped}\n"
            f"{time_msg.strip()}"
        )
        send_telegram_alert(telegram_summary, bot_name=bot_name, force=True)
    except Exception as e:
        print_lg(f"[Warning] Failed to send Telegram progress update: {e}")

    if _randomly_answered_questions:
        print_lg("\n[IT-Indeed] Questions answered by fallback:")
        for item in _randomly_answered_questions:
            print_lg(f"  {item}")
    print_lg(f'\n{"=" * 70}')


def load_resume_state(bot_name: str, default_terms: list[str]) -> tuple[list[str], str | None]:
    import json
    import os
    from datetime import datetime
    state_file = "data/resume_state.json"
    if not os.path.exists(state_file):
        return default_terms, None
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("date") == today and state.get("bot_name") == bot_name:
            remaining = state.get("remaining_terms", [])
            filtered = [t for t in remaining if t in default_terms]
            if filtered:
                print(f"[ResumeState] Resuming '{bot_name}' with remaining terms: {filtered}")
                return filtered, state.get("location_query")
    except Exception as e:
        print(f"[ResumeState] Error loading resume state: {e}")
    return default_terms, None

def save_resume_state(
    bot_name: str,
    remaining_terms: list[str],
    location_query: str | None = None,
) -> None:
    import json
    import os
    from datetime import datetime
    state_file = "data/resume_state.json"
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        with open(state_file, "w") as f:
            json.dump({
                "date": today,
                "bot_name": bot_name,
                "remaining_terms": remaining_terms,
                "location_query": location_query,
            }, f, indent=2)
        print(f"[ResumeState] Saved remaining terms: {remaining_terms}")
    except Exception as e:
        print(f"[ResumeState] Error saving resume state: {e}")

def clear_resume_state() -> None:
    import os
    state_file = "data/resume_state.json"
    if os.path.exists(state_file):
        try:
            os.remove(state_file)
            print("[ResumeState] Cleared resume state file.")
        except Exception as e:
            print(f"[ResumeState] Error clearing resume state: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Main single-pass entry point  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────
