from ._bootstrap import *  # noqa: F403

def _is_indeed_property_url(url: str) -> bool:
    """Indeed-owned hosts (incl. secure/smartapply) are never external sign-in walls."""
    u = (url or "").lower()
    # Host substrings — avoid matching random sites that merely mention indeed.
    for host in (
        "://indeed.com",
        "://www.indeed.com",
        "://ca.indeed.com",
        "://secure.indeed.com",
        "://smartapply.indeed.com",
        "://employers.indeed.com",
        "://apis.indeed.com",
        ".indeed.com/",
        ".indeed.com?",
        ".indeed.com#",
    ):
        if host in u:
            return True
    # bare host at start of relative-less absolute forms
    if u.startswith("https://indeed.") or u.startswith("http://indeed."):
        return True
    return False


def _is_sign_in_page(page) -> tuple:
    """Detect *external* employer login walls — not Indeed auth intermediates.

    ``secure.indeed.com/auth?...`` is a normal Indeed SSO hop and must NOT be
    treated as an external wall (Westland false-fail: Login keyword '/auth?').
    """
    try:
        url = page.url.lower()
        if _is_indeed_property_url(url) or SMARTAPPLY_DOMAIN in url:
            return False, ""
        for kw in _SIGNIN_URL_KEYWORDS:
            if kw in url:
                return True, f"Login keyword '{kw}' in URL"
        title = page.title().lower()
        for kw in _SIGNIN_TITLE_KEYWORDS:
            if kw in title:
                return True, f"Login keyword '{kw}' in page title"
        body = page.query_selector('body')
        if body:
            snippet = body.inner_text()[:4000].lower()
            hits = [kw for kw in _SIGNIN_BODY_KEYWORDS if kw in snippet]
            if len(hits) >= 2:
                return True, f"Multiple sign-in cues in body: {hits[:3]}"
    except Exception:
        pass
    return False, ""


def _is_indeed_permission_error(page) -> bool:
    try:
        body = page.query_selector("body")
        text = (body.inner_text() if body else "").lower()
        return (
            "don't have permission to view this page" in text
            or "do not have permission to view this page" in text
            or "you may have entered the wrong information" in text
            or (
                "permission to view this page" in text
                and "contact us" in text
            )
        )
    except Exception:
        return False


def _recover_from_indeed_permission_error(page, attempt: int = 1) -> bool:
    if not _is_indeed_permission_error(page):
        return False

    print_lg(
        "[Indeed] Permission page detected — reopening normal Indeed homepage "
        f"with the current session (attempt {attempt})."
    )
    targets = (
        INDEED_HOME,
        f"{INDEED_HOME}/?from=gnav-homepage",
        f"{INDEED_HOME}/jobs",
    )
    target = targets[min(max(attempt - 1, 0), len(targets) - 1)]
    try:
        _goto_page(page, target, timeout=15000)
        return True
    except Exception as e:
        print_lg(f"[Indeed] Could not recover from permission page via goto: {e}")
        try:
            page.evaluate("(url) => { window.location.href = url; }", target)
            time.sleep(2)
            return True
        except Exception as js_err:
            print_lg(f"[Indeed] JS navigation recovery also failed: {js_err}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Login detection  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _is_logged_in(page) -> bool:
    try:
        if _is_indeed_permission_error(page):
            return False
        url = page.url.lower()
        # Login/auth URLs often still carry a valid session cookie, but the
        # logged-in chrome is only rendered on the homepage. Treat auth URLs as
        # "unknown" here (caller should bounce to INDEED_HOME first).
        if any(kw in url for kw in ('/account/login', '/auth', 'signin', 'register', 'account/create')):
            return False
        for sel in [
            "[data-testid='UserDropdownButton']",
            "button[aria-label*='Account']",
            "#UserDropdown",
            "div.gnav-header-user",
            "a[aria-label*='Profile']",
            "a[href*='/myjobs']",
            "a[href*='/messages']",
            "[data-tn-element='myAccountLink']",
            "button#AccountMenu",
            "a[href*='/account']",
            "#AccountMenu",
        ]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        # Signed-out chrome: explicit Sign in CTA in header
        for sel in (
            "a[href*='/account/login']",
            "a[data-gnav-element-name='SignIn']",
            "a:has-text('Sign in')",
        ):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    # Presence of Sign in alone is not proof of guest if Account
                    # menu also exists; only treat as guest when Account is absent.
                    break
            except Exception:
                pass
        body = page.query_selector("body")
        if body:
            text = body.inner_text()[:4000].lower()
            if "welcome," in text and "messages" in text:
                return True
            # Homepage signed-in markers
            if "sign out" in text or "my jobs" in text or "email alerts" in text:
                if "sign in" not in text[:800]:
                    return True
        el = page.query_selector(
            "div.job_seen_beacon, div[data-testid='slider_container'], li[data-jk]"
        )
        if el:
            return True
    except Exception:
        pass
    return False


def _open_indeed_home(page, sb) -> None:
    """Open Indeed homepage (never /account/login) so session cookies hydrate."""
    if sb is not None:
        print_lg(f"[Indeed] Opening {INDEED_HOME} via uc_open_with_reconnect...")
        sb.uc_open_with_reconnect(INDEED_HOME, 6)
    else:
        _goto_page(page, INDEED_HOME, timeout=15000)


def _wait_for_manual_login(page, sb, timeout_minutes: int = 5) -> bool:
    # Unattended farm: do not burn minutes waiting for a human login when the
    # browser profile session is already cold. Override via INDEED_LOGIN_WAIT_MINUTES.
    autonomous = False
    try:
        if os.environ.get("SKIP_USER_START") == "1" or os.environ.get("AUTONOMOUS_SUPERVISOR") == "1":
            autonomous = True
            timeout_minutes = float(os.environ.get("INDEED_LOGIN_WAIT_MINUTES", "0.75") or "0.75")
    except Exception:
        pass
    print_lg(
        f"\n[Indeed] Opening homepage to detect existing session.\n"
        f"[Indeed] Waiting up to {timeout_minutes} minute(s)…"
    )
    # CRITICAL: never start on /account/login — Indeed does not surface the
    # logged-in chrome there even when cookies are valid. Homepage does.
    try:
        _open_indeed_home(page, sb)
    except Exception as e:
        print_lg(f"[Indeed] Could not open Indeed homepage: {e}")
        return False

    check_and_handle_captcha(page, sb, "Indeed homepage - login",
                             run_in_background=run_in_background)

    # ── Recover page if CF bypass caused the Playwright page to go stale ──
    page = try_recover_page(page)
    permission_recoveries = 0
    if _is_indeed_permission_error(page):
        permission_recoveries += 1
    if _recover_from_indeed_permission_error(page, permission_recoveries):
        check_and_handle_captcha(page, sb, "Indeed homepage - permission recovery",
                                 run_in_background=run_in_background)
        page = try_recover_page(page)

    # If we somehow landed on auth/login, bounce back to homepage once.
    try:
        cur = (page.url or "").lower()
        if any(k in cur for k in ("/account/login", "/auth", "signin")):
            print_lg("[Indeed] Landed on auth/login URL — bouncing to homepage for session detect…")
            _open_indeed_home(page, sb)
            page = try_recover_page(page)
            time.sleep(1.5)
    except Exception:
        pass

    # Fast path: already logged in on homepage
    try:
        for p in page.context.pages:
            if _is_logged_in(p):
                print_lg("[Indeed] ✓ Login detected on homepage (existing session).")
                return True
    except Exception:
        pass

    # If Cloudflare still blocking homepage after recovery…
    if is_cloudflare_challenge(page):
        if not captcha_allow_gui_fallback and not captcha_allow_manual_fallback:
            print_lg(
                "[Indeed] Cloudflare is still blocking the homepage, and GUI/manual "
                "fallbacks are disabled. Stopping this cycle cleanly."
            )
            return False
        print_lg("[Indeed] Cloudflare still visible after recovery — reopening homepage once…")
        try:
            _open_indeed_home(page, sb)
        except Exception as reopen_err:
            print_lg(f"[Indeed] Homepage reopen after Cloudflare failed: {reopen_err}")
        check_and_handle_captcha(page, sb, "Indeed homepage - login retry",
                                 run_in_background=run_in_background)
        page = try_recover_page(page)
        if is_cloudflare_challenge(page) and not captcha_allow_gui_fallback and not captcha_allow_manual_fallback:
            print_lg(
                "[Indeed] Cloudflare remained after retry, and automatic-only mode "
                "has no usable Turnstile sitekey. Stopping this cycle cleanly."
            )
            return False

    # Only open the login form when we are NOT autonomous AND homepage shows
    # signed-out chrome. Autonomous farm must never force /account/login — that
    # page hides valid sessions and breaks SmartApply.
    if not autonomous:
        try:
            if not _is_logged_in(page):
                login_url = f"{INDEED_HOME}/account/login"
                print_lg(f"[Indeed] Not logged in on homepage — opening {login_url} for manual sign-in…")
                if sb is not None:
                    sb.uc_open_with_reconnect(login_url, 6)
                else:
                    _goto_page(page, login_url, timeout=15000)
                # After manual login, return to homepage so detection works.
                print_lg("[Indeed] After signing in, session will be verified on homepage…")
        except Exception as e:
            print_lg(f"[Indeed] Could not open login form: {e}")

    deadline = time.time() + timeout_minutes * 60
    last_home_bounce = 0.0
    while time.time() < deadline:
        try:
            if _is_indeed_permission_error(page):
                permission_recoveries += 1
                if permission_recoveries > 3:
                    print_lg(
                        "[Indeed] Permission page repeated after 3 recovery attempts — "
                        "continuing to searches with the current browser session."
                    )
                    return True
            if _recover_from_indeed_permission_error(page, permission_recoveries):
                page = try_recover_page(page)
                time.sleep(2)
                continue
            # Prefer homepage for detection — bounce off auth URLs periodically
            try:
                cur = (page.url or "").lower()
                if any(k in cur for k in ("/account/login", "/auth", "signin")) and (time.time() - last_home_bounce) > 8:
                    _open_indeed_home(page, sb)
                    page = try_recover_page(page)
                    last_home_bounce = time.time()
                    time.sleep(1.5)
            except Exception:
                pass
            # Check all open pages/tabs in the browser context to detect login success
            for p in page.context.pages:
                try:
                    if _is_logged_in(p):
                        page = p
                        print_lg("[Indeed] ✓ Login detected.")
                        return True
                except Exception:
                    pass
        except Exception as _login_err:
            err_str = str(_login_err).lower()
            if any(k in err_str for k in ("closed", "target", "browser has been")):
                # Page stale again (e.g. Indeed redirected) — recover and retry
                page = try_recover_page(page)
            else:
                print_lg(f"[Indeed] Login check error: {_login_err}")
                break
        time.sleep(2)
    print_lg("[Indeed] ✗ Login timeout — continuing as guest.")
    return False


def _wait_for_user_start() -> None:
    if os.environ.get("SKIP_USER_START") == "1" or os.environ.get("AUTONOMOUS_SUPERVISOR") == "1":
        print_lg("[Indeed] Unattended/autonomous mode: bypassing manual start prompt.")
        return
    print_lg("\n[Indeed] Type  start  and press Enter to begin:")
    while True:
        try:
            cmd = input(">>> ").strip().lower()
        except EOFError:
            cmd = ""
        if cmd in ("", "start", "go", "begin", "run"):
            break
        print_lg(f"[Indeed] Unknown command '{cmd}'. Type 'start' or press Enter.")


# ─────────────────────────────────────────────────────────────────────────────
# Search URL builder
# ─────────────────────────────────────────────────────────────────────────────
