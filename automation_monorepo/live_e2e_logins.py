#!/usr/bin/env python3
"""
Live E2E login flow for all supervised bots: real Chrome, CDP, Playwright bridge,
per-bot profile dirs (session cookies), IMAP OTP where implemented (Indeed/Glassdoor).
Bot order, ports, and profile paths match ``supervisor.py`` via ``core/supervised_bots.py``.

  cd automation_monorepo
  python3 -m pip install seleniumbase playwright
  python3 -m playwright install chromium
  python3 live_e2e_logins.py [--only indeed_it,glassdoor_general,...]
  python3 live_e2e_logins.py --portal indeed   # all Indeed bots only
  python3 live_e2e_logins.py --portal workopolis --only workopolis_it
  python3 live_e2e_logins.py --timeout 8

By default this script forces **visible Chrome** (headed), even if
`run_in_background = True` in config/settings.py — so you can watch logins.
Use `--headless` only for unattended/CI.

On **headed** runs, if a bot fails the script **waits for Enter** before calling `quit()` so
Chrome does not vanish immediately (override with `--no-keep-open-on-failure`).
Use `--keep-open` to pause after **every** bot. Non-interactive: set
`LIVE_E2E_KEEP_OPEN_SECONDS` (default 120) for how long to leave the window up.

LinkedIn: set LINKEDIN_EMAIL and LINKEDIN_PASSWORD for a first automatic submit;
otherwise complete login in the browser during the wait window. LinkedIn rarely uses
IMAP OTP in this stack (password + 2FA app is typical).

Requires a graphical session (visible Chrome). Not suitable for headless CI without --headless.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

base_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(base_dir))

from core.supervisor_runtime import merge_dotenv_into_env


def _load_dotenv() -> None:
    env_path = base_dir / ".env"
    merge_dotenv_into_env(os.environ, env_path, override=False)




def _pause_for_inspection(bot_name: str) -> None:
    """Block before tearing down Chrome so you can read the page / devtools."""
    print(
        f"\n[Live E2E] Chrome still open — {bot_name}. "
        "Inspect the window, then press Enter here to close it and continue…\n",
        flush=True,
    )
    if sys.stdin.isatty():
        try:
            input()
        except EOFError:
            delay = int(os.environ.get("LIVE_E2E_KEEP_OPEN_SECONDS", "120"))
            print(f"[Live E2E] EOF on stdin — sleeping {delay}s…", flush=True)
            time.sleep(delay)
    else:
        delay = int(os.environ.get("LIVE_E2E_KEEP_OPEN_SECONDS", "120"))
        print(
            f"[Live E2E] Non-interactive terminal — sleeping {delay}s before closing browser…",
            flush=True,
        )
        time.sleep(delay)


def _apply_live_browser_mode(headless: bool) -> None:
    """
    open_chrome.createBrowserSession passes headless=run_in_background.
    Importing portal modules copies run_in_background — set config *before* those imports.
    """
    import config.settings as st  # noqa: WPS433 — intentional late import after path setup

    st.run_in_background = bool(headless)


def _ensure_canada_defaults() -> None:
    """Set Canada base URLs if not already set — must run before portal imports."""
    if not os.environ.get("INDEED_BASE_URL", "").strip():
        os.environ["INDEED_BASE_URL"] = "https://ca.indeed.com"
    if not os.environ.get("GLASSDOOR_BASE_URL", "").strip():
        os.environ["GLASSDOOR_BASE_URL"] = "https://www.glassdoor.ca"


def main() -> int:
    _load_dotenv()
    _ensure_canada_defaults()

    ap = argparse.ArgumentParser(description="Live CDP + Playwright login smoke for all bots.")
    ap.add_argument(
        "--only",
        type=str,
        default="",
        help="Comma-separated bot names (e.g. indeed_it,linkedin_it). Default: all.",
    )
    ap.add_argument(
        "--portal",
        type=str,
        default=None,
        choices=["indeed", "glassdoor", "linkedin", "workopolis"],
        help="Run every bot that uses this portal (overrides --only if set).",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Minutes to wait per bot for manual / OTP completion (default 5).",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome with no UI (same as run_in_background). Default: visible window.",
    )
    ap.add_argument(
        "--keep-open",
        action="store_true",
        help="After every bot, wait for Enter before closing Chrome (debugging).",
    )
    ap.add_argument(
        "--keep-open-on-failure",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="After a failed bot, wait for Enter before closing Chrome "
        "(default: on for headed runs; off with --headless).",
    )
    args = ap.parse_args()

    env_headless = os.environ.get("LIVE_E2E_HEADLESS", "").strip().lower() in ("1", "true", "yes")
    headless = bool(args.headless or env_headless)
    if args.keep_open_on_failure is None:
        keep_open_on_failure = not headless
    else:
        keep_open_on_failure = bool(args.keep_open_on_failure)
    _apply_live_browser_mode(headless=headless)
    if not headless:
        print(
            "[Live E2E] Visible Chrome — watch the bot window. "
            "(Config `run_in_background` is overridden to False for this script.)"
        )
    else:
        print("[Live E2E] Headless mode — no browser window.")

    # Import only after run_in_background is set so open_chrome and portals see headed mode.
    from core.browser.open_chrome import createBrowserSession  # noqa: E402

    def _portal_handler(portal: str):
        if portal == "indeed":
            from core.portals.indeed import _wait_for_manual_login
        elif portal == "glassdoor":
            from core.portals.glassdoor import _wait_for_manual_login
        elif portal == "workopolis":
            from core.portals.workopolis import _wait_for_manual_login
        else:
            raise KeyError(portal)
        return _wait_for_manual_login

    from core.supervised_bots import apply_bot_runtime_env_overwrite, supervised_bot_configs
    from core.supervisor_runtime import apply_imap_env_for_profile

    def run_one(bot: dict, timeout_minutes: int) -> bool:
        print(
            f"\n{'=' * 60}\n  LIVE LOGIN  {bot['bot_name']}  ({bot['portal']})  "
            f"CDP {bot['cdp_port']}\n{'=' * 60}"
        )

        apply_bot_runtime_env_overwrite(bot)
        apply_imap_env_for_profile(os.environ, bot["profile"])

        handler = _portal_handler(bot["portal"])
        sb = page = context = browser = pw = None
        ok = False
        try:
            sb, page, context, browser, pw = createBrowserSession(bot_name=bot["bot_name"])
            ok = bool(handler(page, sb, timeout_minutes=timeout_minutes))
            print(f">>> [{bot['bot_name']}] Result: {'SUCCESS' if ok else 'FAILED'}")
            return ok
        except Exception as e:
            print(f">>> [{bot['bot_name']}] ERROR: {e}")
            ok = False
            return False
        finally:
            pause = (not headless) and (
                args.keep_open or (keep_open_on_failure and not ok)
            )
            if pause:
                _pause_for_inspection(bot["bot_name"])
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
            time.sleep(1)

    specs = supervised_bot_configs(base_dir)
    if args.portal:
        specs = [s for s in specs if s["portal"] == args.portal]
    elif args.only.strip():
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        specs = [s for s in specs if s["bot_name"] in want]
        missing = want - {s["bot_name"] for s in specs}
        if missing:
            print(f"Unknown bot name(s): {missing}", file=sys.stderr)
            return 2

    if not specs:
        print("No bots selected.", file=sys.stderr)
        return 2

    results: list[tuple[str, bool]] = []
    for bot in specs:
        ok = run_one(bot, timeout_minutes=args.timeout)
        results.append((bot["bot_name"], ok))
        time.sleep(2)

    failed = [n for n, o in results if not o]
    print("\n" + "=" * 60)
    if not failed:
        print("ALL LIVE LOGIN CHECKS REPORTED SUCCESS (or best-effort complete).")
        return 0
    print(f"Finished with failures: {', '.join(failed)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
