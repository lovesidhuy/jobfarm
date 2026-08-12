#!/usr/bin/env python3
"""One-time (or re-auth) Google OAuth login for personal Drive + Sheets.

Opens a browser, you approve access with your Google account, and saves
``token.json`` in the monorepo root. Future runs of the daily reporter reuse
and refresh that token — files upload to **your** My Drive (your quota).

Usage
-----
  python scripts/google_oauth_login.py
  python scripts/google_oauth_login.py --test-upload
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Google OAuth login for jobbots reporter")
    ap.add_argument(
        "--test-upload",
        action="store_true",
        help="After login, upload a tiny test file to GOOGLE_DRIVE_FOLDER_ID",
    )
    ap.add_argument(
        "--client-file",
        default="",
        help="Path to OAuth client JSON (default: client_secret.json)",
    )
    args = ap.parse_args()

    if args.client_file:
        os.environ["GOOGLE_OAUTH_CLIENT_FILE"] = args.client_file

    from core.google_sheets_reporter import (
        _get_oauth_user_credentials,
        _oauth_token_path,
        upload_to_google_drive,
        write_to_google_sheet,
        get_daily_stats,
    )
    from core.secret_manager import get_secret

    print("=== Google OAuth login (desktop) ===")
    print("Scopes: spreadsheets + drive")
    print("Token file:", _oauth_token_path())
    print("Folder ID:", (get_secret("GOOGLE_DRIVE_FOLDER_ID", "") or "")[:16] + "…")
    print("Sheet ID:", (get_secret("GOOGLE_SPREADSHEET_ID", "") or "")[:16] + "…")
    print()

    creds = _get_oauth_user_credentials(interactive=True)
    if not creds:
        print("FAIL: could not obtain OAuth credentials")
        return 1

    print("OK: credentials valid")
    print("  token file:", _oauth_token_path(), "exists=", _oauth_token_path().is_file())

    if args.test_upload:
        print("\n=== Test Drive upload + Sheet row ===")
        # Ensure Drive upload is on for this test
        os.environ["GOOGLE_DRIVE_UPLOAD"] = "1"
        link = upload_to_google_drive(
            "jobbots oauth test upload\n",
            f"jobbots_oauth_test.txt",
        )
        print("Drive link:", link)
        ok = write_to_google_sheet(get_daily_stats())
        print("Sheet write:", ok)
        if not link:
            return 2

    print("\nDone. Future reporter runs will reuse token.json (no browser).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
