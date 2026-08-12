"""Session health checker for job automation portals (Indeed, Glassdoor, LinkedIn).

Checks MongoDB, NSTBrowser API, and launches headless browsers to verify if
sessions are active. If not, alerts the user to log in and sync their Mac profile.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
import json
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

# Add project root to path
base_dir = _MONOREPO_ROOT
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from jobbots.core.secret_manager import get_secret
from jobbots.core.alerts import send_telegram_alert
from jobbots.core.session_registry import record_bot_session_ready, record_bot_session_not_ready
from jobbots.core.supervised_bots import supervised_bot_config_by_name, supervised_bot_configs
from jobbots.core.health_controller import is_mongodb_available
from jobbots.core.browser.nst_profile_safety import resolve_configured_profile_id


def check_mongodb() -> bool:
    """Verify MongoDB is reachable."""
    try:
        available = is_mongodb_available()
        print(f"[SessionCheck] MongoDB reachable: {available}")
        return available
    except Exception as e:
        print(f"[SessionCheck] MongoDB check error: {e}")
        return False


def check_nstbrowser_api() -> bool:
    """Verify NSTbrowser Local API is responsive."""
    api_host = get_secret("NSTBROWSER_API_HOST", "127.0.0.1").strip()
    api_port = get_secret("NSTBROWSER_API_PORT", "8848").strip()
    api_key = get_secret("NSTBROWSER_API_KEY", "").strip()
    
    if not api_key:
        print("[SessionCheck] NSTBROWSER_API_KEY not configured. Skipping API check.")
        return False

    url = f"http://{api_host}:{api_port}/api/v2/browsers"
    headers = {"x-api-key": api_key}
    
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            # code 0 or 200 represents success in NST API
            code = res_data.get("code")
            is_ok = code in (0, 200)
            print(f"[SessionCheck] NSTbrowser API responsive: {is_ok} (code: {code})")
            return is_ok
    except Exception as e:
        print(f"[SessionCheck] NSTbrowser API connection failed at {url}: {e}")
        return False


def verify_portal_login(bot_name: str) -> bool:
    """Launch the bot's browser profile in headless mode and verify if session cookies are valid.
    
    If login has expired, writes false to registry and sends a Telegram alert to the user.
    """
    print(f"\n[SessionCheck] Starting pre-flight login check for {bot_name}...")
    
    # 1. Resolve configuration
    try:
        cfg = supervised_bot_config_by_name(bot_name)
    except KeyError:
        print(f"[SessionCheck] Error: bot {bot_name} not found in configs.")
        return False
        
    portal = cfg.get("portal", "")
    from jobbots.core.browser.nst_accounts import resolve_profile_id
    try:
        _, profile_id, _ = resolve_profile_id(bot_name)
    except Exception as e:
        print(f"[SessionCheck] Error resolving profile: {e}")
        profile_id = ""
    
    if not profile_id:
        print(f"[SessionCheck] Error: No NST profile ID configured for {bot_name}.")
        record_bot_session_not_ready(bot_name, reason="missing_profile_id")
        return False

    # Apply configuration environment overrides
    from jobbots.core.supervised_bots import apply_bot_runtime_env_overwrite
    apply_bot_runtime_env_overwrite(cfg)
    
    # Force headless mode settings for pre-check
    import config.settings as st
    original_bg = st.run_in_background
    st.run_in_background = True
    
    # Ensure correct base URLs are set
    indeed_base = os.environ.get("INDEED_BASE_URL") or "https://ca.indeed.com"
    glassdoor_base = os.environ.get("GLASSDOOR_BASE_URL") or "https://www.glassdoor.ca"
    
    from jobbots.core.browser.open_chrome import createBrowserSession
    
    sb = page = context = browser = pw = None
    logged_in = False
    
    try:
        # Start browser session
        sb, page, context, browser, pw = createBrowserSession(bot_name=bot_name)
        
        # Navigate to target check page based on portal
        if portal == "indeed" or portal == "workopolis":
            check_url = f"{indeed_base}/"
            print(f"[SessionCheck] Navigating to {check_url}...")
            page.goto(check_url, timeout=30000)
            page.wait_for_timeout(5000)
            
            # Check for Indeed login indicators
            current_url = page.url.lower()
            if any(kw in current_url for kw in ('login', 'signin', '/auth', 'register', 'checkpoint')):
                logged_in = False
            else:
                # Look for logged-in UI elements
                for sel in (
                    "[data-testid='UserDropdownButton']",
                    "button[aria-label*='Account']",
                    "#UserDropdown",
                    "div.gnav-header-user",
                    "a[aria-label*='Profile']",
                    "a[href*='/myjobs']",
                    "a[href*='/messages']",
                ):
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible(timeout=500):
                            logged_in = True
                            break
                    except Exception:
                        pass
                        
        elif portal == "glassdoor":
            check_url = f"{glassdoor_base}/member/profile/index.htm"
            print(f"[SessionCheck] Navigating to {check_url}...")
            page.goto(check_url, timeout=30000)
            page.wait_for_timeout(5000)
            
            current_url = page.url.lower()
            if "/profile/login" in current_url or "login" in current_url:
                logged_in = False
            else:
                # Look for Sign In elements on screen
                has_signin = False
                for sel in (
                    "button:has-text('Sign in')",
                    "a:has-text('Sign in')",
                    "button:has-text('Sign In')",
                    "a:has-text('Sign In')",
                    "a[href*='/profile/login']"
                ):
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible(timeout=500):
                            has_signin = True
                            break
                    except Exception:
                        pass
                
                # If we're on a profile page or don't see any sign-in buttons, assume logged in
                if not has_signin and ("/member/profile" in current_url or "profile" in current_url):
                    logged_in = True
                    
        elif portal == "linkedin":
            check_url = "https://www.linkedin.com/feed/"
            print(f"[SessionCheck] Navigating to {check_url}...")
            page.goto(check_url, timeout=30000)
            page.wait_for_timeout(5000)
            
            current_url = page.url.lower()
            if any(kw in current_url for kw in ('login', 'signin', 'checkpoint/lg/login', 'signup')):
                logged_in = False
            else:
                # Look for Sign In or Guest elements
                has_signin = False
                for sel in (
                    "a:has-text('Sign in')",
                    "a[href*='/login']",
                    "button[data-tracking-control-name='guest_home_nav-header-signin']"
                ):
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible(timeout=500):
                            has_signin = True
                            break
                    except Exception:
                        pass
                
                # Verify feed elements
                has_feed = False
                for sel in ("div.feed-identity-module", "a[href*='/in/']", "button.global-nav__primary-link"):
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible(timeout=500):
                            has_feed = True
                            break
                    except Exception:
                        pass
                
                if not has_signin and has_feed:
                    logged_in = True
                    
        print(f"[SessionCheck] Login status for {bot_name}: {'SUCCESS' if logged_in else 'EXPIRED'}")
        
    except Exception as e:
        print(f"[SessionCheck] Error during verification for {bot_name}: {e}")
        logged_in = False
        
    finally:
        # Restore configuration background setting
        st.run_in_background = original_bg
        
        # Clean up browser
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        try:
            if sb:
                sb.quit()
        except Exception:
            pass
            
    # 3. Record results
    if logged_in:
        record_bot_session_ready(bot_name, portal=portal)
        return True
    else:
        record_bot_session_not_ready(bot_name, reason="session_expired")
        # Goad/provoke user via Telegram alert
        message = (
            f"🔑 Session EXPIRED / Not logged in!\n"
            f"Please open the NSTBrowser app on your Mac, select profile '{bot_name}' (ID: {profile_id}), "
            f"log in to {portal}.com, and verify that the profile syncs to the cloud. "
            f"The bot cannot continue until this profile has a valid logged-in session."
        )
        send_telegram_alert(message, bot_name=bot_name, alert_type="login_expired", force=True)
        return False


def run_preflight_checks(only_bots: list[str] | None = None) -> dict[str, Any]:
    """Run all pre-flight health checks and session checks.
    
    Returns a status dict indicating which resources and bots are ready.
    """
    print("[SessionCheck] Starting preflight check suite...")
    
    results = {
        "mongodb": check_mongodb(),
        "nstbrowser_api": check_nstbrowser_api(),
        "bots": {}
    }
    
    # If API and DB are not reachable, we cannot continue reliably
    if not results["mongodb"]:
        print("[SessionCheck] WARNING: MongoDB is down. Fallback to local files will be required.")
    if not results["nstbrowser_api"]:
        print("[SessionCheck] WARNING: NSTBrowser API is down. Headless bot launching will fail.")

    configs = supervised_bot_configs(base_dir)
    for cfg in configs:
        name = cfg["bot_name"]
        if only_bots and name not in only_bots:
            continue
        # Only verify if bot is enabled
        if results["nstbrowser_api"]:
            is_ok = verify_portal_login(name)
            results["bots"][name] = is_ok
        else:
            print(f"[SessionCheck] Skipping login check for {name} due to NST API failure.")
            results["bots"][name] = False
            
    return results


if __name__ == "__main__":
    run_preflight_checks()
