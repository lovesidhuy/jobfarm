#!/usr/bin/env python3
"""Open and hold the Indeed IT SeleniumBase UC browser for manual login.

Leave this process running while the attach-only bot uses the same browser over
CDP. Closing this process closes the browser and invalidates the live session.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def _cdp_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/json/version", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return bool(data.get("webSocketDebuggerUrl") or data.get("Browser"))
    except Exception:
        return False


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))

    from core.supervisor_runtime import merge_dotenv_into_env

    merge_dotenv_into_env(os.environ, repo / ".env")

    profile_dir = Path(
        os.environ.get("CHROME_PROFILE_DIR")
        or repo / "data" / "browser_profiles" / "indeed_it"
    ).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    cdp_port = int(os.environ.get("CDP_PORT") or "9223")
    cdp_url = f"http://127.0.0.1:{cdp_port}"

    print(f"[LoginBrowser] profile={profile_dir}", flush=True)
    print(f"[LoginBrowser] requested CDP={cdp_url}", flush=True)

    from seleniumbase import Driver as SBDriver

    driver = SBDriver(
        browser="chrome",
        uc=True,
        headless=False,
        no_sandbox=True,
        user_data_dir=str(profile_dir),
        chromium_arg=[
            f"--remote-debugging-port={cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=ChromeWhatsNewUI",
        ],
    )

    caps_addr = ""
    try:
        caps_addr = (
            driver.capabilities.get("goog:chromeOptions", {}).get("debuggerAddress", "")
            if getattr(driver, "capabilities", None)
            else ""
        )
    except Exception:
        caps_addr = ""
    if caps_addr:
        cdp_url = "http://" + caps_addr

    print(f"[LoginBrowser] live CDP={cdp_url}", flush=True)
    if not _cdp_reachable(cdp_url):
        print("[LoginBrowser] WARNING: CDP is not reachable yet; keeping browser open anyway.", flush=True)

    driver.get("https://ca.indeed.com/")
    print("[LoginBrowser] Log into Indeed in the opened Chrome window.", flush=True)
    print("[LoginBrowser] Leave this process running, then run:", flush=True)
    print(
        f"  EXISTING_CDP_URL={cdp_url} automation_monorepo/.venv/bin/python -u "
        "automation_monorepo/scripts/run_indeed_it_existing_cdp.py",
        flush=True,
    )
    print("[LoginBrowser] Press Ctrl-C here only after the bot is finished.", flush=True)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[LoginBrowser] Closing browser.", flush=True)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
