"""
Thin proxy for glassdoor_it portal. Delegates dynamically to the glassdoor_bot module
in the master it_indeed folder.
"""
import os

from jobbots.core.portals.proxy_helper import get_module
from jobbots.core.session_registry import record_bot_session_not_ready, record_bot_session_ready

def _wait_for_manual_login(page, sb, timeout_minutes=5):
    base = os.environ.get("GLASSDOOR_BASE_URL", "https://www.glassdoor.ca")
    jobs_url = base.rstrip("/") + "/Job/index.htm"
    print(f"[Glassdoor] Opening {jobs_url} -- please log in.")
    print(f"[Glassdoor] Waiting up to {timeout_minutes} minute(s)...")
    try:
        page.goto(jobs_url, timeout=15000)
    except Exception as e:
        print(f"[Glassdoor] Could not open Glassdoor: {e}")
    mod = get_module("modules.glassdoor_bot")
    ok = bool(mod._wait_for_glassdoor_login(page, max_wait_s=timeout_minutes * 60))
    bot_name = os.environ.get("BOT_NAME", "glassdoor_it")
    if ok:
        record_bot_session_ready(bot_name, portal="glassdoor")
    else:
        record_bot_session_not_ready(bot_name, reason="login_timeout")
    return ok

def run_glassdoor_loop(page, sb):
    mod = get_module("modules.glassdoor_bot")
    return mod.run_glassdoor_loop(page, sb)

def __getattr__(name):
    mod = get_module("modules.glassdoor_bot")
    return getattr(mod, name)
