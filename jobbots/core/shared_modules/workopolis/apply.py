from ._bootstrap import *  # noqa: F403

def _is_placeholder_url(url: str) -> bool:
    url = (url or "").strip().lower()
    return not url or url in {"about:blank", "about:srcdoc"} or url.startswith(("javascript:", "data:"))


def _is_real_application_url(url: str) -> bool:
    if _is_placeholder_url(url):
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_smartapply_url(url: str) -> bool:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return False
    return host == smartapply.SMARTAPPLY_DOMAIN


def _is_indeed_smartapply_auth_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        if host != "secure.indeed.com" or not parsed.path.startswith("/auth"):
            return False
        query = unquote_plus(parsed.query or "").lower()
        return (
            "smartapply.indeed.com" in query
            or "indapply-login-smartapply" in query
        )
    except Exception:
        return False


def _is_apply_flow_url(url: str) -> bool:
    return _is_smartapply_url(url) or _is_indeed_smartapply_auth_url(url) or "ca.indeed.com/applystart" in url or "indeed.com/apply" in url


def _is_apply_flow_page(page) -> bool:
    try:
        return _is_apply_flow_url(page.url or "")
    except Exception:
        return False


def _find_apply_flow_page_in_context(context):
    try:
        pages = list(context.pages)
    except Exception:
        pages = []
    for candidate in pages:
        try:
            if candidate.is_closed():
                continue
            if _is_apply_flow_url(candidate.url or ""):
                return candidate
        except Exception:
            continue
    return None


def _recover_smartapply_page(apply_page, context=None):
    try:
        if apply_page and not apply_page.is_closed():
            current_url = apply_page.url or ""
            if _is_apply_flow_url(current_url):
                return apply_page
    except Exception:
        current_url = ""

    if context is None:
        try:
            context = apply_page.context
        except Exception:
            context = None

    context_candidate = _find_apply_flow_page_in_context(context) if context else None
    if context_candidate:
        return context_candidate

    try:
        recovered = try_recover_page(
            apply_page,
            prefer_page=_is_apply_flow_page,
            allow_any=False,
        )
    except Exception:
        recovered = apply_page
    try:
        recovered_url = recovered.url or ""
    except Exception:
        recovered_url = ""
    if not _is_apply_flow_url(recovered_url):
        try:
            if apply_page and not apply_page.is_closed():
                apply_page.wait_for_url(
                    lambda url: bool(url) and _is_apply_flow_url(url),
                    timeout=8000,
                )
                return apply_page
        except Exception:
            pass
        context_candidate = _find_apply_flow_page_in_context(context) if context else None
        if context_candidate:
            return context_candidate
        print_lg("  [Workopolis] Apply tab not recovered — failing this job cleanly.")
    return recovered


def _focus_for_captcha(page, label: str = ""):
    try:
        page.bring_to_front()
        time.sleep(0.3)
        if label:
            print_lg(f"  [Workopolis] Focused {label} for CAPTCHA handling.")
    except Exception:
        pass
    return page


def _wait_for_indeed_smartapply_login(apply_page, sb, job_id: str,
                                      timeout_s: int = 600):
    try:
        current_url = apply_page.url or ""
    except Exception:
        current_url = ""

    is_signin, sign_reason = smartapply._is_sign_in_page(apply_page)
    if not (_is_indeed_smartapply_auth_url(current_url) or is_signin):
        return apply_page, current_url, True

    print_lg(
        "  [Workopolis] Indeed login required for SmartApply — "
        "sign in in the apply tab; bot will resume automatically."
    )
    log_training_event(
        "workopolis_smartapply_login_wait",
        job_id=job_id,
        url=current_url,
        reason=sign_reason,
    )
    _focus_for_captcha(apply_page, "Indeed SmartApply login tab")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        apply_page = _recover_smartapply_page(apply_page)
        try:
            current_url = apply_page.url or ""
        except Exception:
            current_url = ""

        if is_cloudflare_challenge(apply_page):
            check_and_handle_captcha(
                apply_page, sb,
                f"Indeed SmartApply login {job_id}",
                run_in_background=run_in_background,
            )
            apply_page = _recover_smartapply_page(apply_page)
            try:
                current_url = apply_page.url or ""
            except Exception:
                current_url = ""

        if _is_smartapply_url(current_url) or "ca.indeed.com/applystart" in current_url:
            print_lg("  [Workopolis] ✓ Indeed login completed — resuming SmartApply.")
            return apply_page, current_url, True

        is_signin, _ = smartapply._is_sign_in_page(apply_page)
        if not (_is_indeed_smartapply_auth_url(current_url) or is_signin):
            print_lg("  [Workopolis] Left login wall but not on SmartApply page.")
            return apply_page, current_url, True

    print_lg("  [Workopolis] ✗ Login timeout waiting for Indeed login.")
    return apply_page, current_url, False


def _wait_for_smartapply_tab(context, original_page, known_pages: set,
                            timeout: float = 12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for candidate in list(context.pages):
            try:
                if candidate.is_closed():
                    continue
                if candidate == original_page or candidate in known_pages:
                    continue
                url = candidate.url or ""
                if _is_placeholder_url(url):
                    time.sleep(0.4)
                    try:
                        url = candidate.url or ""
                    except Exception:
                        url = ""
                if _is_real_application_url(url):
                    return candidate
            except Exception:
                continue
        time.sleep(0.3)
    return None


def _click_job_card(page, card) -> bool:
    try:
        card.scroll_into_view_if_needed()
        time.sleep(0.2)
        card.click()
        time.sleep(1.0)
        return True
    except Exception as e:
        print_lg(f"  [Workopolis] Error clicking job card: {e}")
        return False


def _wait_for_detail_panel(page, timeout_ms=5000) -> bool:
    try:
        page.wait_for_selector("[data-testid='viewJobHeadingContainer']", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _find_apply_button(page):
    selectors = [
        "[data-testid='viewJobHeadingContainer'] [data-testid='viewJobHeaderFooterApplyButton']",
        "[data-testid='viewJobHeaderFooterApplyButton']",
        "[data-testid*='Apply' i]",
        "[aria-label*='Apply' i]",
        "[aria-label*='Quick apply' i]",
        "[aria-label*='Easily apply' i]",
        "button:has-text('Apply')",
        "button:has-text('Apply now')",
        "button:has-text('Easy Apply')",
        "button:has-text('Easily apply')",
        "button:has-text('Quick apply')",
        "button:has-text('Apply with Indeed')",
        "a:has-text('Apply')",
        "a:has-text('Apply now')",
        "a:has-text('Easy Apply')",
        "a:has-text('Easily apply')",
        "a:has-text('Quick apply')",
        "a:has-text('Apply with Indeed')",
        "[role='button']:has-text('Apply')",
        "[role='button']:has-text('Easy Apply')",
        "[role='button']:has-text('Quick apply')",
        "a[href*='apply']",
        "a[href*='indeed.com/apply']",
        "a[href*='smartapply']",
        "button:has-text('Postuler')",
        "a:has-text('Postuler')",
        "[role='button']:has-text('Postuler')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=500):
                return loc
        except Exception:
            continue

    try:
        handles = page.query_selector_all("button, a, [role='button'], input[type='button'], input[type='submit']")
    except Exception:
        handles = []
    for el in handles:
        try:
            text = " ".join((el.inner_text() or el.get_attribute("value") or "").split()).lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            data_test = (el.get_attribute("data-testid") or "").lower()
            href = (el.get_attribute("href") or "").lower()
            combined = f"{text} {aria} {data_test} {href}"
            if (
                el.is_visible()
                and any(k in combined for k in (
                    "apply",
                    "quick apply",
                    "easy apply",
                    "easily apply",
                    "postuler",
                    "smartapply",
                ))
            ):
                return el
        except Exception:
            continue
    return None


def _load_direct_job_page_for_apply(page, job_href: str) -> bool:
    if not job_href:
        return False
    try:
        direct_url = urljoin(WORKOPOLIS_HOME, job_href)
        print_lg("  [Workopolis] Apply button missing in panel — trying direct job URL.")
        page.goto(direct_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1.2)
        return True
    except Exception as e:
        print_lg(f"  [Workopolis] Direct job URL fallback failed: {e}")
        return False


def _get_job_description(page) -> str:
    """Extract the job description from a Workopolis (Indeed-partner) detail page."""
    # Workopolis often lazy-loads the JD; give the panel a moment.
    try:
        page.wait_for_timeout(800)
    except Exception:
        pass

    # Expand truncated descriptions when present.
    for expand_sel in (
        "button:has-text('Show more')",
        "button:has-text('See more')",
        "button[aria-label*='more' i]",
        "a:has-text('Show more')",
        "[data-testid*='showMore' i]",
    ):
        try:
            btn = page.query_selector(expand_sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(400)
                break
        except Exception:
            continue

    selectors = [
        "#jobDescriptionText",
        "div.jobsearch-JobComponent-description",
        "[data-testid='jobDescriptionText']",
        "[data-testid='jobsearch-JobComponent-description']",
        "[data-testid*='jobDescription' i]",
        "[id*='jobDesc' i]",
        "div#jobDescription",
        ".jobsearch-jobDescriptionText",
        "[class*='jobDescription' i]",
        "[class*='JobDescription' i]",
        "section[aria-label*='description' i]",
        "div[aria-label*='description' i]",
        ".job-snippet",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            text = " ".join((el.inner_text() or "").split())
            if len(text) >= 80:
                return text
        except Exception:
            continue

    # Fallback: largest readable text block in the right-hand detail pane.
    for pane_sel in (
        "[data-testid='jobsearch-ViewJobPage']",
        "[data-testid*='JobComponent' i]",
        "main",
        "#viewJobSSRRoot",
        "div.jobsearch-ViewJobLayout--embedded",
    ):
        try:
            pane = page.query_selector(pane_sel)
            if not pane:
                continue
            text = " ".join((pane.inner_text() or "").split())
            # Strip chrome / apply chrome noise; keep a useful JD slice.
            if len(text) >= 200:
                return text[:6000]
        except Exception:
            continue
    return ""


def _description_from_queue_job() -> str:
    """Use Phase-I queue payload description when the SERP page has no JD."""
    import json
    raw = (os.getenv("JOB_QUEUE_DIRECT_JOB") or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("description", "job_description", "about"):
        text = " ".join(str(data.get(key) or "").split())
        if len(text) >= 40:
            return text
    return ""


def _resolve_job_description(page, title: str, company: str, location: str) -> str:
    """Best-effort JD for gating + resume tailor (page → queue → title stub)."""
    description = _get_job_description(page)
    if len(description) >= 80:
        return description

    queued = _description_from_queue_job()
    if len(queued) >= 40:
        print_lg("  [Workopolis] Using queue job description for resume tailor.")
        return queued

    # Last resort: enough context for the tailor service to still produce a PDF
    # instead of silently falling back to the generic IT resume.
    stub = (
        f"Job title: {title or 'Unknown'}. "
        f"Company: {company or 'Unknown'}. "
        f"Location: {location or 'Unknown'}. "
        "IT role; tailor the resume for this posting based on the title and company."
    )
    if description:
        stub = f"{stub} Page excerpt: {description[:1500]}"
    print_lg(
        "  [Workopolis] Job description thin/missing on page — "
        "using title/company stub for resume tailor."
    )
    return stub


def _is_external_apply_button(el, page) -> bool:
    if not el:
        return False
    try:
        text = (el.inner_text() or "").lower()
        aria = (el.get_attribute("aria-label") or "").lower()
        href = (el.get_attribute("href") or "").lower()
        if any(k in text for k in ("company site", "employer's website", "site de l'entreprise", "site de l'employeur")):
            return True
        if any(k in aria for k in ("company site", "employer's website", "site de l'entreprise", "site de l'employeur")):
            return True
        if href and not any(k in href for k in ("indeed.com", "workopolis.com", "smartapply")):
            if href.startswith("http"):
                return True
    except Exception:
        pass
    return False


def _close_extra_pages(context, keep_pages) -> None:
    keep_pages = set(keep_pages or [])
    for candidate in list(context.pages):
        if candidate in keep_pages:
            continue
        try:
            if candidate.is_closed():
                continue
        except Exception:
            pass
        try:
            url = candidate.url or ""
            if "workopolis.com" in url.lower() or "workopolis.ca" in url.lower() or _is_apply_flow_url(url):
                continue
        except Exception:
            pass
        try:
            candidate.close()
        except Exception:
            pass


def _apply_to_job(page, sb, card, job_id: str, title: str,
                  company: str, location: str, job_href: str) -> tuple:
    """
    Applies to a job:
      1. Click card to load job details.
      2. Wait for Apply button in detail panel.
      3. Click Apply -> SmartApply opens in new tab or navigates inline.
      4. Run SmartApply form automation.
    Returns (success: bool, application_link: str, reason: str)
    """
    # Never carry a tailored document from a previous Workopolis job.
    os.environ.pop("INDEED_TAILORED_RESUME_PATH", None)
    context = page.context
    baseline_pages = set(context.pages)

    try:
        log_training_event(
            "workopolis_apply_started",
            job={"job_id": job_id, "title": title, "company": company,
                 "location": location, "job_link": job_href},
            page=page_dom_snapshot(page, limit=20),
        )

        # Discovery supplies a card; queue workers open the exact saved URL.
        if card is None:
            if not _load_direct_job_page_for_apply(page, job_href):
                return False, job_href, "Could not open queued job URL"
        elif not _click_job_card(page, card):
            return False, job_href, "Could not click job card"

        if not _wait_for_detail_panel(page):
            print_lg("  [Workopolis] Job details panel did not load")

        if os.getenv("JOB_QUEUE_BOOKMARK_FIRST", "").strip().lower() in {"1","true","yes","on"}:
            for sel in ("button[data-testid*='save' i]", "button[aria-label*='save' i]", "button:has-text('Save')"):
                try:
                    btn=page.query_selector(sel)
                    if btn and btn.is_visible(): btn.click(); break
                except Exception: pass
            if os.getenv("JOB_QUEUE_BOOKMARK_ONLY", "").strip().lower() in {"1","true","yes","on"}:
                return False, page.url or job_href, "Company-site bookmarked"

        # Handle CapMonster / Captcha checking on the details page
        check_and_handle_captcha(page, sb, f"Workopolis details {job_id}",
                                 run_in_background=run_in_background)
        page = try_recover_page(page)

        apply_btn = _find_apply_button(page)
        if not apply_btn:
            if _load_direct_job_page_for_apply(page, job_href):
                check_and_handle_captcha(page, sb, f"Workopolis direct details {job_id}",
                                         run_in_background=run_in_background)
                page = try_recover_page(page)
                _wait_for_detail_panel(page, timeout_ms=8000)
                apply_btn = _find_apply_button(page)
        if not apply_btn:
            return False, job_href, "Apply button not found in details panel or direct job page"

        # Pre-click external check
        if _is_external_apply_button(apply_btn, page):
            return False, job_href, "External apply flow"

        # Run Local/AI Easy Apply Gate
        try:
            from jobbots.core.shared_modules.indeed import gates as indeed_gates
        except Exception:
            indeed_gates = None

        if indeed_gates:
            description = _resolve_job_description(page, title, company, location)
            try:
                card_text = " ".join((card.inner_text() or "").split())
            except Exception:
                card_text = ""

            # Phase II queue leases were already screened in discovery. Prefer the
            # local title/EA gate for JOB_QUEUE_DIRECT_JOB so a missing DeepSeek key
            # cannot fail-closed an already-approved apply canary.
            queue_preapproved = False
            raw_q = (os.getenv("JOB_QUEUE_DIRECT_JOB") or "").strip()
            if raw_q:
                try:
                    import json as _json
                    qj = _json.loads(raw_q)
                    if isinstance(qj, dict):
                        gs = str(qj.get("gate_status") or "").strip().lower()
                        score = qj.get("gate_score")
                        try:
                            score_n = float(score) if score is not None else None
                        except (TypeError, ValueError):
                            score_n = None
                        if gs in {"approved", "pass", "passed"} or (
                            score_n is not None and score_n >= 70
                        ):
                            queue_preapproved = True
                except Exception:
                    queue_preapproved = False

            try:
                from config.settings import indeed_easy_apply_gate
            except ImportError:
                indeed_easy_apply_gate = "local"

            use_ai_easy_apply_gate = (
                str(indeed_easy_apply_gate).strip().lower() == "ai"
                and not queue_preapproved
            )
            try:
                if use_ai_easy_apply_gate:
                    approved, gate_reason = indeed_gates._groq_gate_should_save(
                        title, company, location, card_text, description,
                        saving_only=False,
                    )
                    gate_reason = f"AI Easy Apply gate: {gate_reason}"
                else:
                    approved, gate_reason = indeed_gates._local_easy_apply_gate_should_apply(
                        title, company, location, card_text, description
                    )
                    if queue_preapproved and not approved:
                        # Title local reject is still respected; thin-description AI
                        # unavailability must not block Phase-I approved work.
                        if "ai gate unavailable" in (gate_reason or "").lower():
                            approved, gate_reason = True, (
                                f"queue pre-approved (Phase I); local note: {gate_reason}"
                            )
                    if queue_preapproved and approved:
                        gate_reason = f"queue pre-approved + local: {gate_reason}"
            except Exception as e:
                gate_name = "AI" if use_ai_easy_apply_gate else "local"
                if queue_preapproved:
                    approved, gate_reason = True, (
                        f"queue pre-approved (Phase I); {gate_name} gate error ignored: {e}"
                    )
                else:
                    approved, gate_reason = False, f"{gate_name} easy-apply gate error; rejected: {e}"

            if not approved:
                print_lg(f"  ✗ Job gate skipped — {gate_reason}")
                return False, job_href, f"bad title: {gate_reason}"
            print_lg(f"  ✓ Job gate approved — {gate_reason}")

            # Workopolis IT ultimately submits through Indeed SmartApply. Tailor
            # before opening that flow so its normal resume handler uploads this PDF.
            if (os.getenv("JOB_PROFILE") or "IT").strip().lower() == "it":
                from modules.resume_workflow_client import tailor_resume_for_job
                tailored = tailor_resume_for_job(
                    title, company, description, job_id,
                    source="workopolis_it", logger=print_lg,
                )
                if not tailored:
                    print_lg(
                        "  [Workopolis] Resume tailor did not produce a PDF — "
                        "SmartApply will use the configured IT resume."
                    )


        # Click apply — Workopolis often opens Indeed SmartApply in a new tab
        # slowly or via window.open; also try href navigation as fallback.
        apply_page = None
        is_new_tab = False
        apply_href = ""
        try:
            apply_href = (apply_btn.get_attribute("href") or "").strip()
        except Exception:
            apply_href = ""

        def _pick_apply_page_after_click():
            """Prefer a SmartApply/applystart tab if any opened; else current page."""
            time.sleep(0.8)
            flow = _find_apply_flow_page_in_context(context)
            if flow:
                return flow, True
            new_pages = [p for p in context.pages if p not in baseline_pages]
            if new_pages:
                return new_pages[-1], True
            try:
                if _is_apply_flow_url(page.url or ""):
                    return page, False
            except Exception:
                pass
            return None, False

        try:
            with context.expect_page(timeout=12000) as new_page_info:
                apply_btn.click(timeout=8000)
            apply_page = new_page_info.value
            is_new_tab = True
            print_lg("  [Workopolis] Clicked Apply -> opened new tab.")
        except Exception as click_exc:
            print_lg(f"  [Workopolis] Apply click/tab race: {click_exc}")
            apply_page, is_new_tab = _pick_apply_page_after_click()
            if apply_page:
                print_lg(
                    "  [Workopolis] Clicked Apply -> "
                    + ("detected tab after delay." if is_new_tab else "inline navigation.")
                )

        if not apply_page and apply_href and apply_href.startswith("http"):
            try:
                print_lg(f"  [Workopolis] Fallback navigate to apply href: {apply_href[:120]}")
                apply_page = context.new_page()
                is_new_tab = True
                apply_page.goto(apply_href, wait_until="domcontentloaded", timeout=25000)
            except Exception as nav_exc:
                print_lg(f"  [Workopolis] Apply href navigate failed: {nav_exc}")
                try:
                    if apply_page and not apply_page.is_closed():
                        apply_page.close()
                except Exception:
                    pass
                apply_page = None

        if not apply_page:
            # Final multi-second poll for late Indeed popup
            for _ in range(8):
                time.sleep(0.5)
                apply_page, is_new_tab = _pick_apply_page_after_click()
                if apply_page:
                    print_lg("  [Workopolis] Late apply tab detected.")
                    break

        if not apply_page:
            return False, job_href, "No apply flow tab or inline navigation detected after click"

        # Quick check if it navigated to an external URL
        for _ in range(12):
            url = apply_page.url
            if url and url != "about:blank":
                # If still on indeed redirector pages, wait for the redirection to complete
                if "applystart" in url or "indeed.com/apply" in url:
                    time.sleep(0.3)
                    continue
                # If resolved to Indeed SmartApply or Auth, it's a valid Indeed flow
                if _is_smartapply_url(url) or _is_indeed_smartapply_auth_url(url):
                    break
                # Otherwise, it has redirected to an external domain! Skip immediately.
                print_lg(f"  [Workopolis] Detected external URL: {url} - skipping immediately.")
                return False, url, "External apply flow"
            time.sleep(0.3)

        # Wait for the apply page to load
        try:
            apply_page.wait_for_url(
                lambda url: bool(url) and url not in ("about:blank", "about:srcdoc")
                            and not url.startswith(("javascript:", "data:")),
                timeout=15000,
            )
            apply_page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass


        # Captcha / Cloudflare check
        apply_page = _focus_for_captcha(apply_page, "Apply tab")
        check_and_handle_captcha(apply_page, sb, f"Workopolis Apply tab {job_id}",
                                 run_in_background=run_in_background)
        apply_page = _recover_smartapply_page(apply_page, context=context)
        application_link = apply_page.url or job_href

        if not _is_apply_flow_url(application_link):
            return False, application_link or job_href, "External apply flow is not SmartApply"

        # Wait for login if required
        apply_page, application_link, login_ok = _wait_for_indeed_smartapply_login(
            apply_page, sb, job_id)
        if not login_ok:
            return False, application_link or job_href, "Indeed login timed out"

        # Run SmartApply automation
        success = False
        if _is_smartapply_url(application_link) or "ca.indeed.com/applystart" in application_link:
            job_meta = {
                "job_id": job_id, "title": title, "company": company,
                "location": location, "job_link": job_href, "source": "workopolis",
            }
            smartapply._current_job_meta = job_meta
            smartapply_impl._current_job_meta = job_meta
            success, application_link = smartapply._automate_smartapply(
                apply_page, sb, job_id, title)
            smartapply_status = (
                getattr(smartapply_impl, "_last_smartapply_status", "")
                or getattr(smartapply, "_last_smartapply_status", "")
            )
            if not success and smartapply_status == "already_applied":
                return False, application_link or job_href, "Already applied on Indeed"
            if not success and smartapply_status in (
                "skipped_cover_letter",
                "skipped_cover_letter_screen",
            ):
                return False, application_link or job_href, "Cover letter screen — skipped by policy"
            if not success and smartapply_status == "job_title_mismatch":
                return False, application_link or job_href, "SmartApply job title mismatch"

        # Return to search if inline
        if not is_new_tab:
            try:
                return_btn = page.query_selector("button:has-text('Return to job search'), button:has-text('Retour à la recherche')")
                if return_btn and return_btn.is_visible():
                    print_lg("  [Workopolis] Clicking Return to job search...")
                    return_btn.click()
                    time.sleep(2.0)
            except Exception as e:
                print_lg(f"  [Workopolis] Return to job search button error: {e}")

            # Final inline url sanity check
            try:
                if "search" not in page.url.lower():
                    page.go_back(timeout=8000)
                    time.sleep(1.5)
            except Exception:
                pass

        if success:
            return True, application_link, ""
        return False, application_link or job_href, "SmartApply form automation failed"

    finally:
        _close_extra_pages(context, baseline_pages)
