#!/usr/bin/env python3
"""Run Indeed General against an already-open logged-in browser via CDP attach."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))

    from scripts._bootstrap_capmonster_env import bootstrap_capmonster_env

    bootstrap_capmonster_env(repo)

    os.environ.update(
        {
            "BOT_NAME": "indeed_general",
            "BROWSER_VENDOR": "existing-cdp",
            "JOB_PROFILE": "General",
            "SKIP_USER_START": "1",
            "RUN_IN_BACKGROUND": "0",
            "DISABLE_PYAUTOGUI_ALERTS": "1",
            "SMARTAPPLY_FORCE_RECAPTCHA_PRE_SUBMIT": "1",
            "NSTBROWSER_PROFILE_ID": "",
        }
    )

    from core.captcha_runtime import captcha_bootstrap_message

    print("[Bootstrap] existing-cdp mode: attach only; no browser/profile launch.", flush=True)
    print(captcha_bootstrap_message(os.environ), flush=True)

    from core.browser.open_chrome import discover_existing_cdp_url

    cdp_url, notes = discover_existing_cdp_url()

    if not cdp_url:
        print("[Bootstrap] No reachable existing CDP endpoint. Refusing to start the bot.", flush=True)
        for note in notes[-12:]:
            print(f"[Bootstrap] {note}", flush=True)
        raise SystemExit(2)

    os.environ["EXISTING_CDP_URL"] = cdp_url
    print(f"[Bootstrap] verified existing CDP endpoint: {cdp_url}", flush=True)

    from bots.indeed_general import main as run_bot

    run_bot()


if __name__ == "__main__":
    main()
