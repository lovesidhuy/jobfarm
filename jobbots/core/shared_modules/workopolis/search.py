from ._bootstrap import *  # noqa: F403

def _is_logged_in(page) -> bool:
    try:
        # Check if the sign in menu/avatar is visible, or if the sign in button is NOT visible
        if page.query_selector("[data-testid='headerSignInMenu']"):
            return True
        if page.query_selector("[data-testid='headerProfilePageLink']"):
            return True
        if page.query_selector("[data-testid='headerSignInJobSeekerButton']"):
            return False
    except Exception:
        pass
    return False


def _wait_for_workopolis_login(page, sb=None, timeout_minutes=5, max_wait_s=None) -> bool:
    if max_wait_s is not None:
        timeout_minutes = max(1, int(max_wait_s) // 60)
    autonomous = str(os.environ.get("SKIP_USER_START") or os.environ.get("AUTONOMOUS_SUPERVISOR") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if autonomous:
        try:
            timeout_minutes = float(os.environ.get("WORKOPOLIS_LOGIN_WAIT_MINUTES", "0.75") or "0.75")
        except Exception:
            timeout_minutes = 0.75
    print_lg(f"[Workopolis] Opening homepage — detecting existing session.\n"
             f"[Workopolis] Waiting up to {timeout_minutes} minute(s)…")
    try:
        page.goto(WORKOPOLIS_HOME, timeout=20000)
    except Exception as e:
        print_lg(f"[Workopolis] Could not open Workopolis homepage: {e}")

    check_and_handle_captcha(page, sb, "Workopolis homepage - login",
                             run_in_background=run_in_background)
    page = try_recover_page(page)

    # Fast path: already signed in on homepage — never click Sign-in (that
    # routes through Indeed /auth and hides the valid session).
    try:
        if _is_logged_in(page):
            print_lg("[Workopolis] ✓ Login detected on homepage (existing session).")
            return True
    except Exception:
        pass

    # Manual/interactive only: help the user reach Indeed sign-in. Autonomous
    # farm must stay on homepage so cookie sessions remain detectable.
    if not autonomous:
        try:
            signin_btn = page.query_selector("[data-testid='headerSignInJobSeekerButton']")
            if signin_btn and signin_btn.is_visible():
                print_lg("[Workopolis] Clicking Job Seeker Sign-in button to navigate to partner sign-in...")
                signin_btn.click()
                time.sleep(1.0)
                partner_btn = page.query_selector("[data-testid='partnerSignInUpButton']")
                if partner_btn and partner_btn.is_visible():
                    print_lg("[Workopolis] Clicking Partner Sign-in button to load Indeed login...")
                    partner_btn.click()
        except Exception as e:
            print_lg(f"[Workopolis] Sign-in routing helper warning: {e}")

    deadline = time.time() + timeout_minutes * 60
    last_home_bounce = 0.0
    while time.time() < deadline:
        try:
            # Bounce off Indeed auth pages back to Workopolis home for detection
            try:
                cur = (page.url or "").lower()
                if ("indeed.com" in cur and ("/auth" in cur or "login" in cur or "signin" in cur)) and (
                    time.time() - last_home_bounce
                ) > 8:
                    print_lg("[Workopolis] On Indeed auth — bouncing to Workopolis homepage for session detect…")
                    page.goto(WORKOPOLIS_HOME, timeout=20000)
                    last_home_bounce = time.time()
                    time.sleep(1.5)
            except Exception:
                pass
            if _is_logged_in(page):
                print_lg("[Workopolis] ✓ Login detected.")
                return True
        except Exception as _login_err:
            err_str = str(_login_err).lower()
            if any(k in err_str for k in ("closed", "target", "browser has been")):
                page = try_recover_page(page)
            else:
                print_lg(f"[Workopolis] Login check error: {_login_err}")
                break
        time.sleep(2)
    print_lg("[Workopolis] ✗ Login timeout — continuing as guest.")
    return False


def _ensure_workopolis_logged_in(page, sb=None) -> bool:
    try:
        if not _is_logged_in(page):
            return _wait_for_workopolis_login(page, sb=sb)
    except Exception:
        pass
    return True


def _build_search_url(term: str, location: str, page_num: int = 0) -> str:
    # Page parameters: Workopolis uses pagination cursor, so for page_num > 0,
    # the caller will click next page button instead of constructing URL parameters.
    # We construct the page 1 URL sorted by date.
    return f"https://www.workopolis.com/search?q={quote_plus(term)}&l={quote_plus(location)}&s=d"


def _apply_distance_filter(page, distance_text="25 kilometers") -> None:
    try:
        combo = page.query_selector("button[aria-label='Distance'], button[name='Distance'], [role='combobox'][name='Distance']")
        if not combo:
            combo = page.query_selector("//button[contains(., 'Distance')]")
        if combo:
            combo.click()
            time.sleep(0.5)
            item = page.query_selector(f"//div[@role='menuitemradio' and contains(., '{distance_text}')]")
            if not item:
                item = page.query_selector(f"//button[contains(., '{distance_text}')]")
            if item:
                item.click()
                time.sleep(1.5)
                print_lg(f"[Workopolis] Applied Distance filter: {distance_text}")
    except Exception as e:
        print_lg(f"[Workopolis] Distance filter error: {e}")


def _apply_job_type_filter(page, job_type_text="Full-time") -> None:
    try:
        combo = page.query_selector("button[aria-label='Job Type'], button[name='Job Type'], [role='combobox'][name='Job Type']")
        if not combo:
            combo = page.query_selector("//button[contains(., 'Job Type')]")
        if combo:
            combo.click()
            time.sleep(0.5)
            item = page.query_selector(f"//div[@role='menuitemradio' and contains(., '{job_type_text}')]")
            if not item:
                item = page.query_selector(f"//button[contains(., '{job_type_text}')]")
            if item:
                item.click()
                time.sleep(1.5)
                print_lg(f"[Workopolis] Applied Job Type filter: {job_type_text}")
    except Exception as e:
        print_lg(f"[Workopolis] Job Type filter error: {e}")


def _go_to_next_page(page, page_num: int) -> bool:
    target_page = page_num + 1  # caller increments page_num before asking for next page
    selector = f"[data-testid='paginationBlock{target_page}']"
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            print_lg(f"[Workopolis] Clicking next page button (page {target_page})...")
            el.click()
            time.sleep(2.0)
            return True
    except Exception as e:
        print_lg(f"[Workopolis] Failed to click page button {target_page}: {e}")
    
    # Fallback to generic Next arrows
    for sel in ["[aria-label='Next']", "a[aria-label='Next page']", "button[aria-label='Next page']"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                print_lg("[Workopolis] Clicking generic Next page button...")
                el.click()
                time.sleep(2.0)
                return True
        except Exception:
            continue
    return False


def _find_job_cards(page) -> list:
    selectors = [
        "[data-testid='searchSerpJob']",
        "[data-testid='expandedSearchTitleCard']",
        "div.job_seen_beacon",
        "[data-testid='jobListing']",
        "div[data-jk]",
        "li[data-jk]",
        "a[href*='/jobsearch/viewjob/']",
        "a[href*='/job/']",
        "a[href*='/joblisting/']",
        "a[data-testid='job-card-title-link']"
    ]
    for sel in selectors:
        try:
            cards = page.query_selector_all(sel)
            if cards:
                return cards
        except Exception:
            continue
    return []


def _extract_card_info(card) -> tuple:
    """
    Returns (job_id, title, company, location, has_easy_apply, job_href).
    """
    job_id = card.get_attribute("data-jk") or card.get_attribute("data-jobid") or ""
    title = "Unknown"
    company = "Unknown"
    location = "Unknown"
    job_href = ""
    has_easy_apply = False

    try:
        href = card.get_attribute("href")
        if not href:
            link_el = (
                card.query_selector("a[href*='/jobsearch/viewjob/']")
                or card.query_selector("a[href*='/job/']")
                or card.query_selector("a[href*='/joblisting/']")
                or card.query_selector("a")
            )
            if link_el:
                href = link_el.get_attribute("href")
        
        if href:
            job_href = href
            if not job_id:
                parsed_url = urlparse(href)
                params = parse_qs(parsed_url.query)
                if "jk" in params:
                    job_id = params["jk"][0]
                elif "jobId" in params:
                    job_id = params["jobId"][0]
                elif parsed_url.path:
                    job_id = parsed_url.path.rstrip("/").split("/")[-1]
    except Exception:
        pass

    if not job_id:
        job_id = f"work_{abs(hash(job_href or '')) & 0xffffffff:08x}"

    try:
        title_el = card.query_selector(
            "[data-testid='searchSerpJobTitle'], "
            "[data-testid='expandedSearchCardHeader'], "
            "h2, h3, [data-testid='job-title'], a[data-testid='job-card-title-link']"
        )
        if title_el:
            title = title_el.inner_text().strip()
        else:
            card_text = card.inner_text().strip()
            title = card_text.split("\n")[0] if card_text else "Unknown"
    except Exception:
        pass

    try:
        comp_el = card.query_selector(
            "[data-testid='companyName'], "
            "[data-testid='expandedSearchCardCompanyName'], "
            "[data-testid='company-name'], .companyName, .company, span.css-1dg9rry"
        )
        if comp_el:
            company = comp_el.inner_text().strip()
    except Exception:
        pass

    try:
        loc_el = card.query_selector(
            "[data-testid='searchSerpJobLocation'], "
            "[data-testid='expandedSearchCardJobLocation'], "
            "[data-testid='company-location'], .companyLocation, .location"
        )
        if loc_el:
            location = loc_el.inner_text().strip()
    except Exception:
        pass

    try:
        easy_el = card.query_selector(
            "[data-testid='searchSerpJobQuickApply'], "
            "[aria-label='Easy Apply'], :text('Easy Apply'), :text('Quick apply'), .easy-apply"
        )
        if easy_el:
            has_easy_apply = True
        else:
            card_text_lower = card.inner_text().lower()
            if "easy apply" in card_text_lower or "simpliplié" in card_text_lower or "apply with indeed" in card_text_lower:
                has_easy_apply = True
    except Exception:
        pass

    return job_id, title, company, location, has_easy_apply, job_href


def _find_card_by_job(page, job_id: str = "", job_href: str = ""):
    """Find a fresh card handle for a previously snapshotted job."""
    candidates = _find_job_cards(page)
    for candidate in candidates:
        try:
            cand_id, _, _, _, _, cand_href = _extract_card_info(candidate)
            if job_id and cand_id == job_id:
                return candidate
            if job_href and cand_href == job_href:
                return candidate
        except Exception:
            continue
    return None
