"""
Thin proxy for workopolis portal. Delegates dynamically to the workopolis_bot module
in the appropriate master folder.
"""
import os

from jobbots.core.portals.proxy_helper import get_module
from jobbots.core.session_registry import record_bot_session_not_ready, record_bot_session_ready

def _wait_for_manual_login(page, sb, timeout_minutes=5):
    base = os.environ.get("WORKOPOLIS_BASE_URL", "https://www.workopolis.com/")
    print(f"[Workopolis] Opening {base} -- please log in.")
    print(f"[Workopolis] Waiting up to {timeout_minutes} minute(s)...")
    try:
        page.goto(base, timeout=15000)
    except Exception as e:
        print(f"[Workopolis] Could not open Workopolis: {e}")
    mod = get_module("modules.workopolis_bot")
    ok = bool(mod._wait_for_workopolis_login(page, max_wait_s=timeout_minutes * 60))
    bot_name = os.environ.get("BOT_NAME", "workopolis")
    if ok:
        record_bot_session_ready(bot_name, portal="workopolis")
    else:
        record_bot_session_not_ready(bot_name, reason="login_timeout")
    return ok

def run_workopolis_loop(page, sb):
    mod = get_module("modules.workopolis_bot")
    return mod.run_workopolis_loop(page, sb)

def __getattr__(name):
    mod = get_module("modules.workopolis_bot")
    return getattr(mod, name)
