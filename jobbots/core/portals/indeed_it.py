"""
Thin proxy for indeed_it portal. Delegates dynamically to the indeed_bot module
in the master it_indeed folder.
"""
import os

from jobbots.core.portals.proxy_helper import get_module
from jobbots.core.session_registry import record_bot_session_not_ready, record_bot_session_ready

def _wait_for_manual_login(page, sb, timeout_minutes=5):
    mod = get_module("modules.indeed_bot")
    ok = bool(mod._wait_for_manual_login(page, sb, timeout_minutes=timeout_minutes))
    bot_name = os.environ.get("BOT_NAME", "indeed_it")
    if ok:
        record_bot_session_ready(bot_name, portal="indeed")
    else:
        record_bot_session_not_ready(bot_name, reason="login_timeout")
    return ok

def _init_ai_client():
    mod = get_module("modules.indeed_bot")
    return mod._init_ai_client()

def _ai_answer(question, hint="", job_context="", options=None, **kwargs):
    mod = get_module("modules.indeed_bot")
    return mod._ai_answer(question, hint=hint, job_context=job_context, options=options)

def run_indeed_loop(page, sb):
    mod = get_module("modules.indeed_bot")
    return mod.run_indeed_loop(page, sb)

def __getattr__(name):
    mod = get_module("modules.indeed_bot")
    return getattr(mod, name)
