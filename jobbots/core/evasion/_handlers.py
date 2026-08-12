from __future__ import annotations

import os
import time
import random

from jobbots.core.portals.training_logger_legacy import log_training_event, page_dom_snapshot

from jobbots.core.evasion._config import (
    pyautogui,
    _CF_TIMEOUT_DEFAULT,
    _RECAPTCHA_TIMEOUT_DEFAULT,
    _CAPMONSTER_TIMEOUT,
    _CAPMONSTER_TURNSTILE_TIMEOUT,
    _CLOUDFLARE_SOLVER,
    _ALLOW_GUI_FALLBACK,
    _ALLOW_MANUAL_FALLBACK,
    _USE_CAPMONSTER,
    _POLL_INTERVAL,
    _elapsed,
    _cap_log,
    _truthy,
    _is_autonomous,
    _load_manual_cf_click_point,
    _save_manual_cf_click_point,
    print_lg,
)

from jobbots.core.evasion._focus import (
    _focus_bot_os_window,
    _humanize_move_and_click,
)

from jobbots.core.evasion._capmonster import (
    solve_recaptcha_with_capmonster,
    solve_turnstile_with_capmonster,
    get_last_turnstile_challenge_diag,
    update_last_turnstile_challenge_diag,
)

from jobbots.core.evasion._capsolver import (
    solve_recaptcha_with_capsolver,
    solve_turnstile_with_capsolver,
    solve_cloudflare_challenge_with_capsolver,
    solve_hcaptcha_with_capsolver,
    _capsolver_client_key,
)

from jobbots.core.evasion._detection import (
    is_cloudflare_challenge,
    is_recaptcha_challenge,
    is_recaptcha_widget_present,
    _get_latest_live_page,
    _wait_for_turnstile_widget,
    _cloudflare_heuristic_checkbox_box,
    _finalize_cf_attempt,
    _is_page_alive,
    is_indeed_waf_ip_block,
    _indeed_submit_button_ready,
    _page_url,
    _same_url_family,
)

# ── Import SeleniumBase module-level CAPTCHA functions ────────────────────────
_sb_uc_gui_click_cf      = None
_sb_uc_gui_click_rc      = None
_sb_uc_gui_click_captcha = None
_sb_uc_gui_handle_cf     = None
_sb_uc_gui_handle_rc     = None


def _cf_stop_after_hard_capmonster_reject() -> bool:
    return _truthy(os.getenv("CAPTCHA_CF_STOP_AFTER_HARD_CAPMONSTER_REJECT", "1"))


def _capmonster_cf_hard_reject(diag: dict | None) -> bool:
    if not diag:
        return False
    final_status = str(diag.get("final_status") or "")
    capmonster_result = str(diag.get("capmonster_result") or "")
    return (
        final_status in {
            "CF_CLEARANCE_APPLIED_NOT_ACCEPTED",
            "TOKEN_RESCUE_RETURNED_NO_USABLE_TOKEN",
            "TOKEN_RESCUE_INJECTED_NOT_ACCEPTED",
        }
        or capmonster_result == "ERROR_CAPTCHA_UNSOLVABLE"
    )


try:
    from seleniumbase.core.browser_launcher import (
        uc_gui_click_cf      as _sb_uc_gui_click_cf,
        uc_gui_click_rc      as _sb_uc_gui_click_rc,
        uc_gui_click_captcha as _sb_uc_gui_click_captcha,
        uc_gui_handle_cf     as _sb_uc_gui_handle_cf,
        uc_gui_handle_rc     as _sb_uc_gui_handle_rc,
    )
    print_lg("[CAPTCHA] ✓ SeleniumBase CAPTCHA functions loaded.")
except ImportError as e:
    print_lg(f"[CAPTCHA] ⚠ SeleniumBase CAPTCHA functions not available: {e}")


def close_debugger_traps(page) -> int:
    """
    Dismiss Chrome's 'Paused in debugger' / 'debugger paused in another tab'
    info-bar banner.  If left unhandled this banner can freeze the tab that
    owns the CDP session, silently stalling a bot.

    Returns the number of traps dismissed.
    """
    if page is None:
        return 0
    dismissed = 0
    try:
        cdp = page.context.new_cdp_session(page)
        try:
            cdp.send("Debugger.disable")
            dismissed += 1
            _cap_log("Dismissed CDP debugger trap via Debugger.disable.")
        except Exception:
            pass
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
    except Exception:
        pass

    try:
        for sel in [
            "button:has-text('Resume')",
            "[aria-label='Resume script execution']",
            "#resume-button",
        ]:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(timeout=1500)
                dismissed += 1
                _cap_log(f"Clicked debugger resume button: {sel}")
                break
    except Exception:
        pass

    return dismissed


def _sync_sb_driver(sb, page=None) -> None:
    """
    Sync the ChromeDriver session so get_page_source() sees the current DOM.

    When Playwright navigates (page.goto), ChromeDriver's internal state may be
    stale. If a Playwright page is supplied, focus that tab first and switch
    ChromeDriver to the matching window handle. This is important for GUI
    CAPTCHA clicks because SeleniumBase moves the real mouse in the active tab.

    NOTE: We pass blind=True to all CAPTCHA click functions which skips the
    page-type check entirely, so this sync is a belt-and-suspenders measure.
    """
    if sb is None:
        return
    try:
        target_url = _page_url(page) if page is not None else ""
        if page is not None:
            try:
                page.bring_to_front()
                time.sleep(0.2)
            except Exception:
                pass

        handles = sb.window_handles
        if handles and target_url:
            matched = False
            for handle in reversed(handles):
                try:
                    sb.switch_to.window(handle)
                    if sb.current_url == target_url:
                        matched = True
                        break
                except Exception:
                    continue
            if not matched:
                for handle in reversed(handles):
                    try:
                        sb.switch_to.window(handle)
                        if _same_url_family(sb.current_url, target_url):
                            matched = True
                            break
                    except Exception:
                        continue
            if not matched:
                print_lg(f"[CAPTCHA] ⚠ Could not match SeleniumBase tab for CAPTCHA URL: {target_url}")
        elif handles:
            sb.switch_to.window(handles[-1])
        sb.execute_script("return document.readyState")
    except Exception:
        pass


def _env_truthy(name: str, fallback) -> bool:
    value = os.getenv(name)
    if value is None:
        return _truthy(fallback)
    return _truthy(value)


def _cloudflare_solver_setting() -> str:
    return (os.getenv("CAPTCHA_CLOUDFLARE_SOLVER") or str(_CLOUDFLARE_SOLVER)).strip().lower()


def _use_capmonster_setting() -> bool:
    value = os.getenv("USE_CAPMONSTER_CAPTCHA_SOLVER")
    if value is None:
        value = os.getenv("CAPTCHA_USE_CAPMONSTER")
    if value is None:
        value = os.getenv("USE_CAPMONSTER")
    if value is None:
        return _truthy(_USE_CAPMONSTER)
    return _truthy(value)


def _capmonster_key_present() -> bool:
    for name in ("CAPMONSTER_CLIENT_KEY", "CAPMONSTER_API_KEY", "capkey"):
        if (os.getenv(name) or "").strip():
            return True
    return False


def _recover_seleniumbase_session(sb):
    if sb is not None:
        return sb
    try:
        from jobbots.core.browser import open_chrome as _open_chrome
        session = getattr(_open_chrome, "_session", None)
        if isinstance(session, dict):
            recovered = session.get("sb")
            if recovered is not None:
                return recovered
    except Exception:
        pass
    return None


def _cf_patient_wait_seconds() -> int:
    value = os.getenv("CAPTCHA_CF_PATIENT_WAIT")
    if value is None:
        try:
            from config.settings import captcha_cf_patient_wait
            return max(0, int(captcha_cf_patient_wait))
        except ImportError:
            return 15
    try:
        return max(0, int(value))
    except ValueError:
        return 15


def _cf_skip_reload() -> bool:
    value = os.getenv("CAPTCHA_CF_SKIP_RELOAD")
    if value is None:
        try:
            from config.settings import captcha_cf_skip_reload
            return _truthy(captcha_cf_skip_reload)
        except ImportError:
            return True
    return _truthy(value)


def _cf_capmonster_viable() -> bool:
    """CapMonster cf_clearance mode needs a proxy; skip CF API calls when none is set."""
    if not _use_capmonster_setting() or not _capmonster_key_present():
        return False
    if _truthy(os.getenv("BYPASS_PROXY", "0")):
        explicit = (os.getenv("CAPMONSTER_PROXY_URL") or os.getenv("CAPMONSTER_PROXY") or "").strip()
        if not explicit:
            return False
    return True


def _passive_wait_for_cloudflare_clear(page, seconds: int, start: float) -> bool:
    if seconds <= 0:
        return False
    _cap_log(f"Passive wait: giving Cloudflare up to {seconds}s to self-clear before active solvers…", start)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        latest_page = _get_latest_live_page(page)
        if not is_cloudflare_challenge(latest_page):
            try:
                resolved_url = latest_page.url
            except Exception:
                resolved_url = "?"
            _cap_log(f"✓ Cloudflare cleared during passive wait on: {resolved_url}", start)
            return True
    return False


def _wait_cf_clear_after_click(page, polls: int = 15, interval: float = 1.0) -> bool:
    for _ in range(polls):
        time.sleep(interval)
        try:
            if not is_cloudflare_challenge(_get_latest_live_page(page)):
                return True
        except Exception:
            pass
    return False


def _solve_cloudflare_playwright_click(page, retries: int = 4) -> bool:
    """
    Click Cloudflare Turnstile / Indeed verification checkbox via Playwright CDP.
    Uses viewport coordinates — works on Nstbrowser where PyAutoGUI screen coords fail.
    """
    page_widget_selectors = (
        ".cf-turnstile",
        "[data-sitekey]",
        "[role='checkbox']",
        "input[type='checkbox']",
    )
    iframe_selectors = (
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "iframe[title*='challenge' i]",
        "iframe[title*='turnstile' i]",
    )

    for attempt in range(1, retries + 1):
        latest_page = _get_latest_live_page(page)
        try:
            latest_page.bring_to_front()
        except Exception:
            pass
        time.sleep(0.4)

        for sel in page_widget_selectors:
            try:
                loc = latest_page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click(timeout=3000, force=True)
                    print_lg(f"[CAPTCHA] Playwright clicked page widget: {sel}")
                    if _wait_cf_clear_after_click(latest_page):
                        print_lg("[CAPTCHA] ✓ Cloudflare cleared via Playwright widget click.")
                        return True
            except Exception:
                pass

        for iframe_sel in iframe_selectors:
            try:
                frame = latest_page.frame_locator(iframe_sel).first
                for inner in ("input[type='checkbox']", "[role='checkbox']", "label"):
                    inner_loc = frame.locator(inner).first
                    if inner_loc.count() == 0:
                        continue
                    try:
                        if inner_loc.is_visible(timeout=1000):
                            inner_loc.click(timeout=3000, force=True)
                            print_lg(
                                f"[CAPTCHA] Playwright clicked Turnstile frame "
                                f"{iframe_sel} -> {inner}"
                            )
                            if _wait_cf_clear_after_click(latest_page):
                                print_lg("[CAPTCHA] ✓ Cloudflare cleared via Playwright frame click.")
                                return True
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            label = latest_page.get_by_text("Verify you are human", exact=False).first
            if label.count() > 0 and label.is_visible(timeout=1000):
                bb = label.bounding_box()
                if bb:
                    cx = max(8.0, bb["x"] - 40)
                    cy = bb["y"] + bb["height"] / 2
                    print_lg(
                        f"[CAPTCHA] Playwright clicking left of 'Verify you are human' "
                        f"@ ({cx:.0f},{cy:.0f})"
                    )
                    latest_page.mouse.click(cx, cy)
                    if _wait_cf_clear_after_click(latest_page):
                        print_lg("[CAPTCHA] ✓ Cloudflare cleared via Indeed verify-label click.")
                        return True
        except Exception:
            pass

        box = _wait_for_turnstile_widget(latest_page, retries=4, wait=0.4)
        if not box:
            box = _cloudflare_heuristic_checkbox_box(latest_page)
        if box:
            cx = box["x"] + box["width"] / 2 + random.uniform(-2, 2)
            cy = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
            print_lg(
                f"[CAPTCHA] Playwright viewport click attempt {attempt}/{retries} "
                f"@ ({cx:.0f},{cy:.0f}) source={box.get('source', 'bbox')}"
            )
            try:
                latest_page.mouse.move(cx, cy)
                time.sleep(random.uniform(0.05, 0.15))
                latest_page.mouse.click(cx, cy)
            except Exception as e:
                print_lg(f"[CAPTCHA] Playwright mouse.click failed: {e}")
            if _wait_cf_clear_after_click(latest_page):
                print_lg("[CAPTCHA] ✓ Cloudflare cleared via Playwright viewport click.")
                return True

        time.sleep(1.5)
    return False


def _solve_cloudflare_pyautogui(page, context: str = "", retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            latest_page = _get_latest_live_page(page)
            focused = _focus_bot_os_window(page=latest_page)
            latest_page.bring_to_front()
            time.sleep(0.4)
            if os.name == "nt" and not focused:
                print_lg(
                    f"[CAPTCHA] pyautogui: Chrome window focus failed; "
                    f"skipping click attempt {attempt}/{retries}."
                )
                time.sleep(1)
                continue

            saved_point = _load_manual_cf_click_point()
            if saved_point:
                screen_x = int(saved_point["x"]) + random.randint(-3, 3)
                screen_y = int(saved_point["y"]) + random.randint(-3, 3)
                print_lg(
                    f"[CAPTCHA] pyautogui CF click attempt {attempt}/{retries} "
                    f"@ ({screen_x},{screen_y}) [saved manual point]"
                )
                _humanize_move_and_click(screen_x, screen_y)
                for _ in range(8):
                    time.sleep(1)
                    try:
                        if not is_cloudflare_challenge(latest_page):
                            print_lg("[CAPTCHA] ✓ Cloudflare cleared via saved manual click point.")
                            return True
                    except Exception:
                        pass
                print_lg(f"[CAPTCHA] saved manual point did not clear CF on attempt {attempt}.")

            box = _wait_for_turnstile_widget(latest_page)
            if not box:
                box = _cloudflare_heuristic_checkbox_box(latest_page)
                if box:
                    print_lg(
                        f"[CAPTCHA] pyautogui: using Cloudflare heuristic target "
                        f"(attempt {attempt}/{retries}, source={box.get('source')})."
                    )
                else:
                    print_lg(
                        f"[CAPTCHA] pyautogui: Turnstile widget not ready after retries "
                        f"(attempt {attempt}/{retries})."
                    )
                    if attempt < retries:
                        time.sleep(2)
                        continue
                    return False

            geom = latest_page.evaluate(
                "({x: window.screenX, y: window.screenY, "
                "chromeH: window.outerHeight - window.innerHeight, "
                "chromeW: window.outerWidth  - window.innerWidth})"
            )
            screen_x = int(geom["x"]) + int(geom.get("chromeW", 0)) // 2 + int(box["x"]) + 30
            screen_y = (
                int(geom["y"])
                + int(geom.get("chromeH", 0))
                + int(box["y"])
                + int(box["height"] / 2)
            )
            if box.get("source"):
                screen_x += random.randint(-4, 4)
                screen_y += random.randint(-4, 4)
            print_lg(
                f"[CAPTCHA] pyautogui CF click attempt {attempt}/{retries} "
                f"@ ({screen_x},{screen_y}) [target={box}]"
            )

            _humanize_move_and_click(screen_x, screen_y)

            for _ in range(12):
                time.sleep(1)
                try:
                    if not is_cloudflare_challenge(latest_page):
                        print_lg("[CAPTCHA] ✓ Cloudflare cleared via pyautogui.")
                        return True
                except Exception:
                    pass
            print_lg(f"[CAPTCHA] pyautogui attempt {attempt}: still on CF page.")

        except Exception as e:
            print_lg(f"[CAPTCHA] pyautogui attempt {attempt} raised: {e}")
        time.sleep(1)
    return False


def _wait_for_cloudflare_manual_clear(page, timeout: int, context: str = "") -> bool:
    if not _env_truthy("CAPTCHA_ALLOW_MANUAL_FALLBACK", _ALLOW_MANUAL_FALLBACK):
        print_lg("[CAPTCHA] manual Cloudflare fallback disabled by config.")
        return False

    ctx_tag = f" [{context}]" if context else ""
    print_lg(f"[CAPTCHA] Cloudflare still present{ctx_tag}; waiting {timeout}s for manual solve.")
    print_lg("[CAPTCHA] Please click the visible Cloudflare checkbox in the browser window.")

    deadline = time.time() + timeout
    last_mouse_pos = None
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        try:
            try:
                last_mouse_pos = pyautogui.position()
            except Exception:
                pass
            latest_page = _get_latest_live_page(page)
            if not is_cloudflare_challenge(latest_page):
                try:
                    resolved_url = latest_page.url
                except Exception:
                    resolved_url = "?"
                _save_manual_cf_click_point(last_mouse_pos, latest_page, context=context)
                print_lg(f"[CAPTCHA] ✓ Cloudflare cleared manually on: {resolved_url}")
                return True
        except Exception as e:
            print_lg(f"[CAPTCHA] manual Cloudflare wait check failed: {e}")

        remaining = int(deadline - time.time())
        if remaining > 0 and remaining % 15 == 0:
            print_lg(f"[CAPTCHA] Still waiting for Cloudflare to clear... ({remaining}s left)")

    print_lg("[CAPTCHA] ✗ Cloudflare still present after manual wait; page is not handled.")
    return False


def handle_cloudflare_challenge(page, sb, timeout: int = _CF_TIMEOUT_DEFAULT,
                                run_in_background: bool = False) -> bool:
    start = time.time()
    latest_page = _get_latest_live_page(page)
    if not is_cloudflare_challenge(latest_page):
        _cap_log("No active Cloudflare challenge detected — leaving page untouched.", start)
        return True

    _cap_log("⚠ Cloudflare challenge detected — attempting bypass.", start)
    log_training_event("captcha_attempt_started", captcha_type="cloudflare",
                       timeout=timeout, page=page_dom_snapshot(page, limit=30))

    sb = _recover_seleniumbase_session(sb)
    solver = _cloudflare_solver_setting()
    allow_gui = _env_truthy("CAPTCHA_ALLOW_GUI_FALLBACK", _ALLOW_GUI_FALLBACK)
    allow_manual = _env_truthy("CAPTCHA_ALLOW_MANUAL_FALLBACK", _ALLOW_MANUAL_FALLBACK)

    if is_indeed_waf_ip_block(page):
        _cap_log(
            "Indeed hard WAF block detected (IP/network flagged). "
            "No Turnstile checkbox exists — clicks and CapMonster cannot clear this. "
            "Switch network or enable a residential PROXY_URL, then retry.",
            start,
        )
        log_training_event(
            "captcha_attempt_finished",
            captcha_type="cloudflare",
            status="waf_ip_block",
            method="none",
            elapsed_seconds=round(time.time() - start, 1),
            page=page_dom_snapshot(page, limit=40),
        )
        return False

    _cap_log(
        "Cloudflare solver config: "
        f"solver={solver or 'none'}, allow_gui={allow_gui}, "
        f"allow_manual={allow_manual}, sb={'yes' if sb is not None else 'no'}, "
        f"skip_reload={_cf_skip_reload()}",
        start,
    )

    if _passive_wait_for_cloudflare_clear(page, _cf_patient_wait_seconds(), start):
        log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                           status="cleared", method="passive_wait",
                           elapsed_seconds=round(time.time() - start, 1),
                           page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
        return True

    is_nstbrowser = os.getenv("BROWSER_VENDOR", "").strip().lower() in ("nstbrowser", "nst")

    def _attempt_playwright_cf() -> bool:
        if not (solver == "seleniumbase" or allow_gui):
            return False
        try:
            vendor_tag = "Nstbrowser" if is_nstbrowser else "browser"
            _cap_log(f"Trying Playwright CDP click solver on {vendor_tag}...", start)
            attempt_start = time.time()
            if _solve_cloudflare_playwright_click(page):
                _cap_log(f"Cloudflare cleared after Playwright click in {_elapsed(attempt_start)}.", start)
                log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                   status="cleared", method="playwright_click",
                                   elapsed_seconds=round(time.time() - start, 1),
                                   page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                return True
            _cap_log(f"Playwright click did not clear Cloudflare in {_elapsed(attempt_start)}.", start)
        except Exception as e:
            _cap_log(f"Playwright click solver failed: {e}", start)
        return False

    def _attempt_pyautogui_cf() -> bool:
        if is_nstbrowser:
            return False
        if not (solver == "seleniumbase" or allow_gui):
            return False
        try:
            _cap_log("Trying pyautogui mouse-click solver...", start)
            attempt_start = time.time()
            if _solve_cloudflare_pyautogui(page):
                _cap_log(f"Cloudflare cleared after pyautogui click in {_elapsed(attempt_start)}.", start)
                log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                   status="cleared", method="pyautogui_click",
                                   elapsed_seconds=round(time.time() - start, 1),
                                   page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                return True
            _cap_log(f"pyautogui did not clear Cloudflare in {_elapsed(attempt_start)}.", start)
        except Exception as e:
            _cap_log(f"pyautogui click solver failed: {e}", start)
        return False

    # Playwright CDP clicks work on Nstbrowser; PyAutoGUI screen coords do not.
    if _attempt_playwright_cf():
        return True

    # ── Strategy 1: SeleniumBase UC GUI click/handlers (free, no proxy) ──
    if is_nstbrowser:
        _cap_log("Skipping SeleniumBase GUI Cloudflare solvers (not supported on Nstbrowser).", start)
    elif sb is not None and (solver == "seleniumbase" or allow_gui):
        # blind=True skips _on_a_cf_turnstile_page() check (stale in our arch)
        if _sb_uc_gui_click_cf is not None:
            try:
                _cap_log("Trying uc_gui_click_cf(blind=True)...", start)
                attempt_start = time.time()
                _sync_sb_driver(sb, page)
                _sb_uc_gui_click_cf(sb, blind=True)
                if _finalize_cf_attempt(page, "uc_gui_click_cf"):
                    _cap_log(f"Cloudflare cleared after uc_gui_click_cf in {_elapsed(attempt_start)}.", start)
                    log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                       status="cleared", method="uc_gui_click_cf",
                                       elapsed_seconds=round(time.time() - start, 1),
                                       page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                    return True
                _cap_log(f"uc_gui_click_cf did not clear Cloudflare in {_elapsed(attempt_start)}.", start)
            except Exception as e:
                _cap_log(f"uc_gui_click_cf failed after {_elapsed(attempt_start)}: {e}", start)

        if _sb_uc_gui_handle_cf is not None:
            try:
                _cap_log("Trying uc_gui_handle_cf...", start)
                attempt_start = time.time()
                _sync_sb_driver(sb, page)
                _sb_uc_gui_handle_cf(sb)
                if _finalize_cf_attempt(page, "uc_gui_handle_cf"):
                    _cap_log(f"Cloudflare cleared after uc_gui_handle_cf in {_elapsed(attempt_start)}.", start)
                    log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                       status="cleared", method="uc_gui_handle_cf",
                                       elapsed_seconds=round(time.time() - start, 1),
                                       page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                    return True
                _cap_log(f"uc_gui_handle_cf did not clear Cloudflare in {_elapsed(attempt_start)}.", start)
            except Exception as e:
                _cap_log(f"uc_gui_handle_cf failed after {_elapsed(attempt_start)}: {e}", start)

        if _sb_uc_gui_click_captcha is not None:
            try:
                _cap_log("Trying uc_gui_click_captcha(blind=True)...", start)
                attempt_start = time.time()
                _sync_sb_driver(sb, page)
                _sb_uc_gui_click_captcha(sb, blind=True)
                if _finalize_cf_attempt(page, "uc_gui_click_captcha"):
                    _cap_log(f"Cloudflare cleared after uc_gui_click_captcha in {_elapsed(attempt_start)}.", start)
                    log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                       status="cleared", method="uc_gui_click_captcha",
                                       elapsed_seconds=round(time.time() - start, 1),
                                       page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                    return True
                _cap_log(f"uc_gui_click_captcha did not clear Cloudflare in {_elapsed(attempt_start)}.", start)
            except Exception as e:
                _cap_log(f"uc_gui_click_captcha failed after {_elapsed(attempt_start)}: {e}", start)

    elif sb is not None and not allow_gui:
        _cap_log("SeleniumBase GUI Cloudflare fallback disabled by config.", start)
    elif sb is None and (solver == "seleniumbase" or allow_gui):
        _cap_log("SeleniumBase GUI Cloudflare requested but no SeleniumBase session is available.", start)

    # ── Strategy 2: PyAutoGUI mouse-click solver (local Chrome only) ──
    if _attempt_pyautogui_cf():
        return True

    # ── Strategy 3: CapSolver CF (Turnstile token + AntiCloudflare cf_clearance) ──
    if _capsolver_client_key():
        # Managed challenges (Indeed "Additional Verification Required") often have
        # no Turnstile sitekey in the DOM — prefer AntiCloudflareTask first when
        # CAPTCHA_CLOUDFLARE_SOLVER=capsolver, then fall back to Turnstile token.
        prefer_clearance = solver in {"capsolver", "anticloudflare", "cf_clearance"}
        if prefer_clearance:
            _cap_log("Trying CapSolver AntiCloudflare (cf_clearance)...", start)
            log_training_event("captcha_attempt_method", captcha_type="cloudflare",
                               method="capsolver_anticloudflare",
                               page=page_dom_snapshot(page, limit=30))
            if solve_cloudflare_challenge_with_capsolver(page, timeout=120):
                if not is_cloudflare_challenge(_get_latest_live_page(page)):
                    log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                       status="cleared", method="capsolver_anticloudflare",
                                       elapsed_seconds=round(time.time() - start, 1),
                                       page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                    return True
                _cap_log("CapSolver cf_clearance applied but challenge still visible.", start)
            else:
                _cap_log("CapSolver AntiCloudflare did not clear Cloudflare; trying Turnstile...", start)

        _cap_log("Trying CapSolver Turnstile solver...", start)
        log_training_event("captcha_attempt_method", captcha_type="cloudflare",
                           method="capsolver_turnstile",
                           page=page_dom_snapshot(page, limit=30))
        if solve_turnstile_with_capsolver(page, timeout=120):
            if not is_cloudflare_challenge(_get_latest_live_page(page)):
                log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                   status="cleared", method="capsolver_turnstile",
                                   elapsed_seconds=round(time.time() - start, 1),
                                   page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                return True
        _cap_log("CapSolver Turnstile did not clear Cloudflare; trying fallback...", start)

    # CapMonster fallback (only if active)
    if solver in {"capmonster", "turnstile", "capmonster_turnstile"} and _cf_capmonster_viable():
            # Read retry count (default 2)
            retries = 2
            try:
                from config.settings import captcha_cf_capmonster_retries
                retries = int(captcha_cf_capmonster_retries)
            except Exception:
                pass
            retries = int(os.getenv("CAPTCHA_CF_CAPMONSTER_RETRIES", str(retries)))

            solved = False
            for attempt in range(1, retries + 2):
                _cap_log(f"Trying CapMonster Turnstile (attempt {attempt}/{retries + 1})...", start)
                log_training_event("captcha_attempt_method", captcha_type="cloudflare",
                                   method=f"capmonster_turnstile_attempt_{attempt}",
                                   page=page_dom_snapshot(page, limit=30))
                if solve_turnstile_with_capmonster(page, timeout=_CAPMONSTER_TURNSTILE_TIMEOUT):
                    log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                       status="cleared", method=f"capmonster_turnstile",
                                       elapsed_seconds=round(time.time() - start, 1),
                                       page=page_dom_snapshot(_get_latest_live_page(page), limit=30))
                    solved = True
                    break
                diag = get_last_turnstile_challenge_diag()
                if diag:
                    log_training_event("captcha_attempt_method", captcha_type="cloudflare",
                                       method=f"capmonster_turnstile_diag_attempt_{attempt}",
                                       challenge_diag=diag,
                                       page=page_dom_snapshot(page, limit=30))
                _cap_log(f"CapMonster Turnstile attempt {attempt} did not clear Cloudflare.", start)
                if _cf_stop_after_hard_capmonster_reject() and _capmonster_cf_hard_reject(diag):
                    _cap_log(
                        "Stopping CapMonster retries for this Cloudflare challenge: "
                        "clearance/token attempts were rejected for this browser/proxy session.",
                        start,
                    )
                    break
                if attempt < retries + 1:
                    time.sleep(2)
            if solved:
                return True

    # Non-GUI fallback: page reload — skipped by default because it often re-triggers CF
    attempt_start = time.time()
    if _cf_skip_reload():
        _cap_log("Skipping page.reload() for Cloudflare (CAPTCHA_CF_SKIP_RELOAD enabled).", start)
    elif not _is_page_alive(page):
        _cap_log("Skipping non-GUI page.reload() — page/browser already closed.", start)
    else:
        try:
            _cap_log("Trying non-GUI page.reload()...", start)
            attempt_start = time.time()
            page.reload(wait_until="domcontentloaded", timeout=10000)
            time.sleep(2)
            if not is_cloudflare_challenge(page):
                update_last_turnstile_challenge_diag(
                    resolved_after_reload=True,
                    resolved_after_capmonster=False,
                    final_status="RECOVERED_BY_RELOAD_NOT_SOLVED",
                )
                _cap_log(f"✓ Cloudflare resolved after reload in {_elapsed(attempt_start)}.", start)
                log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                                   status="cleared", method="reload",
                                   elapsed_seconds=round(time.time() - start, 1),
                                   challenge_diag=get_last_turnstile_challenge_diag(),
                                   page=page_dom_snapshot(page, limit=30))
                return True
        except Exception as e:
            _cap_log(f"page.reload() failed after {_elapsed(attempt_start)}: {e}", start)

    latest_page = _get_latest_live_page(page)
    if not is_cloudflare_challenge(latest_page):
        try:
            resolved_url = latest_page.url
        except Exception:
            resolved_url = "?"
        _cap_log(f"✓ Cloudflare challenge resolved on: {resolved_url}", start)
        log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                           status="cleared", method="latest_live_page",
                           elapsed_seconds=round(time.time() - start, 1),
                           resolved_url=resolved_url,
                           page=page_dom_snapshot(latest_page, limit=30))
        return True

    if not allow_manual:
        _cap_log("Auto-bypass failed; manual Cloudflare fallback disabled by config.", start)
        log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                           status="failed_no_manual",
                           method="automatic_only",
                           elapsed_seconds=round(time.time() - start, 1),
                           page=page_dom_snapshot(latest_page, limit=40))
        return False

    _cap_log(f"Auto-bypass failed — waiting {timeout}s for manual solve.", start)
    log_training_event("captcha_manual_wait_started", captcha_type="cloudflare",
                       timeout=timeout, page=page_dom_snapshot(latest_page, limit=30))
    print_lg(f"\n{'─'*60}")
    print_lg("[CAPTCHA] ⚠ CLOUDFLARE CHECKPOINT — MANUAL ACTION NEEDED:")
    print_lg("[CAPTCHA]    1. Go to the browser window")
    print_lg("[CAPTCHA]    2. If a checkbox is visible, click it")
    print_lg("[CAPTCHA]    3. If the real Indeed page is already visible, do nothing")
    print_lg("[CAPTCHA]    4. Bot will auto-continue once the page is clear")
    print_lg(f"[CAPTCHA]    Waiting up to {timeout}s…")
    print_lg(f"{'─'*60}\n")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        latest_page = _get_latest_live_page(page)
        if not is_cloudflare_challenge(latest_page):
            try:
                resolved_url = latest_page.url
            except Exception:
                resolved_url = "?"
            _cap_log(f"✓ Cloudflare challenge resolved on: {resolved_url}", start)
            log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                               status="cleared", method="manual_wait",
                               elapsed_seconds=round(time.time() - start, 1),
                               resolved_url=resolved_url,
                               page=page_dom_snapshot(latest_page, limit=30))
            return True
        remaining = int(deadline - time.time())
        if remaining % 15 == 0 and remaining > 0:
            _cap_log(f"Still waiting for Cloudflare to clear... ({remaining}s left)", start)

    _cap_log(f"✗ Cloudflare timed out after {_elapsed(start)} — continuing.", start)
    log_training_event("captcha_attempt_finished", captcha_type="cloudflare",
                       status="timed_out", method="all_methods",
                       elapsed_seconds=round(time.time() - start, 1),
                       page=page_dom_snapshot(_get_latest_live_page(page), limit=40))
    return False


def handle_recaptcha_challenge(page, sb, timeout: int = _RECAPTCHA_TIMEOUT_DEFAULT,
                                run_in_background: bool = False) -> bool:
    sb = _recover_seleniumbase_session(sb)
    start = time.time()
    _cap_log("⚠ reCAPTCHA image challenge detected.", start)
    allow_manual = _env_truthy("CAPTCHA_ALLOW_MANUAL_FALLBACK", _ALLOW_MANUAL_FALLBACK)
    use_capmonster = _use_capmonster_setting()
    _cap_log(
        "reCAPTCHA solver config: "
        f"use_capmonster={use_capmonster}, capmonster_key={'yes' if _capmonster_key_present() else 'unknown'}, "
        f"allow_manual={allow_manual}, sb={'yes' if sb is not None else 'no'}",
        start,
    )

    if _capsolver_client_key():
        _cap_log("Trying CapSolver reCAPTCHA first...", start)
        log_training_event("captcha_attempt_method", captcha_type="recaptcha_challenge",
                           method="capsolver_recaptcha",
                           page=page_dom_snapshot(page, limit=30))
        if solve_recaptcha_with_capsolver(page, timeout=120):
            _cap_log("reCAPTCHA solved by CapSolver.", start)
            log_training_event("captcha_attempt_finished", captcha_type="recaptcha_challenge",
                               status="cleared", method="capsolver_recaptcha",
                               elapsed_seconds=round(time.time() - start, 1),
                               page=page_dom_snapshot(page, limit=30))
            return True
        _cap_log("CapSolver reCAPTCHA did not clear challenge; checking fallbacks...", start)
    elif use_capmonster:
        _cap_log("Trying CapMonster first.", start)
        log_training_event("captcha_attempt_method", captcha_type="recaptcha_challenge",
                           method="capmonster_recaptcha",
                           page=page_dom_snapshot(page, limit=30))
        if solve_recaptcha_with_capmonster(page, timeout=_CAPMONSTER_TIMEOUT):
            _cap_log("reCAPTCHA solved by CapMonster.", start)
            log_training_event("captcha_attempt_finished", captcha_type="recaptcha_challenge",
                               status="cleared", method="capmonster_recaptcha",
                               elapsed_seconds=round(time.time() - start, 1),
                               page=page_dom_snapshot(page, limit=30))
            return True
        _cap_log("CapMonster reCAPTCHA did not clear challenge.", start)

    try:
        challenge_frame = page.frame_locator("iframe[title*='recaptcha challenge']").first
        verify_btn = challenge_frame.locator("#recaptcha-verify-button")
        if verify_btn.is_visible(timeout=2000):
            verify_btn.click(timeout=3000)
            time.sleep(2)
            if not is_recaptcha_challenge(page):
                _cap_log("✓ reCAPTCHA verified via Playwright.", start)
                return True
    except Exception:
        pass

    _cap_log("Skipping SeleniumBase reCAPTCHA GUI clicks to avoid opening fresh image tiles.", start)

    if _indeed_submit_button_ready(page):
        _cap_log("Submit button is ready while challenge is visible — returning to submit flow.", start)
        return True

    if not allow_manual:
        _cap_log("reCAPTCHA manual fallback disabled by config.", start)
        return False

    print_lg(f"\n{'─'*60}")
    print_lg("[CAPTCHA] ⚠ IMAGE CAPTCHA — MANUAL ACTION NEEDED:")
    print_lg("[CAPTCHA]    1. Look at the browser window")
    print_lg("[CAPTCHA]    2. Select ALL matching images (buses/hydrants/etc.)")
    print_lg("[CAPTCHA]    3. Click 'Verify' in the browser")
    print_lg("[CAPTCHA]    4. Bot will auto-continue once solved")
    print_lg(f"[CAPTCHA]    Waiting up to {timeout}s…")
    print_lg(f"{'─'*60}\n")

    deadline     = time.time() + timeout
    manual_start = time.time()
    while time.time() < deadline:
        time.sleep(1)
        if _indeed_submit_button_ready(page):
            _cap_log("Submit button is ready — returning to submit flow.", start)
            return True
        if not is_recaptcha_challenge(page):
            _cap_log(f"✓ reCAPTCHA challenge resolved manually in {_elapsed(manual_start)}.", start)
            return True
        remaining = int(deadline - time.time())
        if remaining % 15 == 0 and remaining > 0:
            _cap_log(f"Still waiting for image solve... ({remaining}s left)", start)

    if _indeed_submit_button_ready(page):
        _cap_log("Submit button became ready before desktop CAPTCHA alert.", start)
        return True

    if not run_in_background and not _is_autonomous():
        try:
            pyautogui.alert(
                "reCAPTCHA image challenge still not solved!\n\n"
                "Please go to the browser, solve the image grid, and click Verify.\n"
                "Then click OK here to give the bot more time to detect.\n",
                "CAPTCHA – Image Challenge",
                "OK",
            )
            for _ in range(15):
                time.sleep(2)
                if _indeed_submit_button_ready(page):
                    _cap_log("Submit button became ready after CAPTCHA alert.", start)
                    return True
                if not is_recaptcha_challenge(page):
                    print_lg("[CAPTCHA] ✓ reCAPTCHA resolved after alert!")
                    return True
        except Exception:
            pass

    _cap_log(f"✗ reCAPTCHA timed out after {_elapsed(start)} — continuing anyway.", start)
    return False


def _click_recaptcha_checkbox_if_visible(page, timeout: int = 3000) -> bool:
    try:
        recaptcha_frame = page.frame_locator("iframe[title='reCAPTCHA']").first
        checkbox = recaptcha_frame.locator("#recaptcha-anchor")
        if checkbox.is_visible(timeout=timeout):
            checkbox.click(timeout=timeout)
            return True
    except Exception:
        pass
    return False


def handle_recaptcha_widget(page, sb, timeout: int = _RECAPTCHA_TIMEOUT_DEFAULT,
                            run_in_background: bool = False) -> bool:
    sb = _recover_seleniumbase_session(sb)
    print_lg("  [CAPTCHA] reCAPTCHA v2 checkbox widget detected.")
    allow_gui    = _env_truthy("CAPTCHA_ALLOW_GUI_FALLBACK", _ALLOW_GUI_FALLBACK)
    allow_manual = _env_truthy("CAPTCHA_ALLOW_MANUAL_FALLBACK", _ALLOW_MANUAL_FALLBACK)
    use_capmonster = _use_capmonster_setting()

    if _capsolver_client_key():
        print_lg("  [CAPTCHA] Trying CapSolver token solve first...")
        log_training_event("captcha_attempt_method", captcha_type="recaptcha_widget",
                           method="capsolver_recaptcha",
                           page=page_dom_snapshot(page, limit=30))
        if solve_recaptcha_with_capsolver(page, timeout=120):
            log_training_event("captcha_attempt_finished", captcha_type="recaptcha_widget",
                               status="cleared", method="capsolver_recaptcha",
                               page=page_dom_snapshot(page, limit=30))
            return True
        print_lg("  [CAPTCHA] CapSolver did not clear reCAPTCHA widget; trying checkbox fallback.")
    elif use_capmonster:
        print_lg("  [CAPTCHA] Trying CapMonster token solve first...")
        log_training_event("captcha_attempt_method", captcha_type="recaptcha_widget",
                           method="capmonster_recaptcha",
                           page=page_dom_snapshot(page, limit=30))
        if solve_recaptcha_with_capmonster(page, timeout=_CAPMONSTER_TIMEOUT):
            log_training_event("captcha_attempt_finished", captcha_type="recaptcha_widget",
                               status="cleared", method="capmonster_recaptcha",
                               page=page_dom_snapshot(page, limit=30))
            return True
        print_lg("  [CAPTCHA] CapMonster did not clear reCAPTCHA widget; trying checkbox fallback.")
    else:
        print_lg("  [CAPTCHA] No API solver active; use the browser if manual verification appears.")

    clicked = False
    try:
        if _click_recaptcha_checkbox_if_visible(page, timeout=5000):
            print_lg("  [CAPTCHA] ✓ Playwright clicked reCAPTCHA checkbox via frame_locator!")
            clicked = True
    except Exception as e:
        print_lg(f"  [CAPTCHA] Playwright frame_locator click failed: {e}")

    if not clicked and allow_gui:
        _sync_sb_driver(sb, page)
        for fn, name, kwargs in [
            (_sb_uc_gui_click_rc,      "uc_gui_click_rc(blind=True)",      {"blind": True}),
            (_sb_uc_gui_click_captcha, "uc_gui_click_captcha(blind=True)", {"blind": True}),
            (_sb_uc_gui_handle_rc,     "uc_gui_handle_rc",                 {}),
        ]:
            if fn is None or sb is None:
                continue
            try:
                fn(sb, **kwargs)
                print_lg(f"  [CAPTCHA] ✓ Checkbox click fired via {name}")
                clicked = True
                break
            except Exception as e:
                print_lg(f"  [CAPTCHA] {name} raised: {e}")
    elif not clicked and not allow_gui:
        print_lg("  [CAPTCHA] SeleniumBase reCAPTCHA GUI fallback disabled by config.")

    time.sleep(4)

    if is_recaptcha_challenge(page):
        print_lg("  [CAPTCHA] Image challenge appeared (buses / fire hydrants / pumps…)")
        return handle_recaptcha_challenge(page, sb, timeout=timeout, run_in_background=run_in_background)

    if not is_recaptcha_widget_present(page):
        print_lg("  [CAPTCHA] ✓ reCAPTCHA widget solved automatically!")
        return True

    time.sleep(4)
    if is_recaptcha_challenge(page):
        return handle_recaptcha_challenge(page, sb, timeout=timeout, run_in_background=run_in_background)
    if not is_recaptcha_widget_present(page):
        print_lg("  [CAPTCHA] ✓ reCAPTCHA widget solved!")
        return True

    if not allow_manual:
        print_lg("  [CAPTCHA] reCAPTCHA manual checkbox fallback disabled by config.")
        return False

    print_lg(f"  [CAPTCHA] Auto-solve failed — waiting {timeout}s for manual checkbox solve…")

    if not run_in_background and not _is_autonomous():
        try:
            pyautogui.alert(
                "reCAPTCHA 'I am not a robot' checkbox is blocking form submission!\n\n"
                "Please:\n"
                "  1. Click the checkbox in the browser window\n"
                "  2. If an image grid appears, solve it (select buses / pumps / etc.)\n"
                "  3. Click OK here ONLY after the checkbox shows a green tick.\n",
                "CAPTCHA – Checkbox Verification",
                "OK",
            )
        except Exception:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        if is_recaptcha_challenge(page):
            print_lg("  [CAPTCHA] Image challenge appeared — solving…")
            return handle_recaptcha_challenge(page, sb, timeout=timeout // 2,
                                             run_in_background=run_in_background)
        if not is_recaptcha_widget_present(page):
            print_lg("  [CAPTCHA] ✓ reCAPTCHA widget resolved.")
            return True

    print_lg("  [CAPTCHA] ✗ reCAPTCHA widget timed out — continuing anyway.")
    return False


def check_and_handle_captcha(page, sb=None, context: str = "",
                             timeout: int = None,
                             run_in_background: bool = False) -> bool:
    sb = _recover_seleniumbase_session(sb)
    ctx_tag = f" [{context}]" if context else ""

    close_debugger_traps(page)

    if is_cloudflare_challenge(page):
        print_lg(f"[CAPTCHA] Cloudflare block detected{ctx_tag}")
        log_training_event("captcha_detected", captcha_type="cloudflare",
                           context=context, page=page_dom_snapshot(page, limit=30))
        handle_cloudflare_challenge(
            page, sb,
            timeout=timeout or _CF_TIMEOUT_DEFAULT,
            run_in_background=run_in_background,
        )
        return True

    if is_recaptcha_challenge(page):
        print_lg(f"[CAPTCHA] reCAPTCHA challenge detected{ctx_tag}")
        log_training_event("captcha_detected", captcha_type="recaptcha_challenge",
                           context=context, page=page_dom_snapshot(page, limit=30))
        log_training_event("captcha_attempt_started", captcha_type="recaptcha_challenge",
                           context=context, page=page_dom_snapshot(page, limit=30))
        solved = handle_recaptcha_challenge(
            page, sb,
            timeout=timeout or _RECAPTCHA_TIMEOUT_DEFAULT,
            run_in_background=run_in_background,
        )
        log_training_event(
            "captcha_attempt_finished",
            captcha_type="recaptcha_challenge",
            status="cleared" if solved else "failed",
            method="capmonster_or_manual",
            context=context,
            page=page_dom_snapshot(_get_latest_live_page(page), limit=30),
        )
        if not solved:
            print_lg("[CAPTCHA] reCAPTCHA was detected but not solved; caller should skip this job if still blocked.")
        return True

    return False


def watch_for_captcha_after_submit(page, sb=None, poll_seconds: int = 3,
                                   max_wait: int = 30,
                                   run_in_background: bool = False) -> bool:
    """Poll briefly after clicking Submit for a CAPTCHA to appear."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(poll_seconds)
        if check_and_handle_captcha(page, sb, context="post-submit",
                                    run_in_background=run_in_background):
            return True
    return False
