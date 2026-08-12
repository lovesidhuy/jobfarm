#!/usr/bin/env python3
"""Run Indeed IT bot to apply directly to a list of job links/keys from a JSON file."""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))

    parser = argparse.ArgumentParser(description="Run Indeed IT direct links applier bot")
    parser.add_argument(
        "json_path", 
        type=str, 
        nargs="?",
        default="data/apify_indeed_it/indeed_it_new_jobs_combined.json",
        help="Path to the JSON file containing job links/keys (relative to monorepo root or absolute)"
    )
    args = parser.parse_args()

    # Verify JSON file exists
    json_path = Path(args.json_path)
    if not json_path.is_absolute():
        json_path = repo / json_path
        
    if not json_path.is_file():
        print(f"Error: JSON file not found at: {json_path}", file=sys.stderr)
        print("Please check the path or run the scraper first.", file=sys.stderr)
        sys.exit(1)

    os.environ.update(
        {
            "INDEED_DIRECT_LINKS_PATH": str(json_path.resolve()),
            "BROWSER_VENDOR": "nstbrowser",
            "NSTBROWSER_PROFILE_ID": "cf393220-4ce0-4903-b935-49a490a88c66",
        }
    )

    # Now launch the bot using the standard bootstrap launcher
    from core.bootstrap_bot_launch import bootstrap_bot_launch

    bootstrap_bot_launch(
        repo=repo,
        bot_name="indeed_it",
        bot_import="bots.indeed_it",
        cdp_port="9223",
        job_profile="IT",
        profile_subdir="indeed_it",
        extra_secret_names=("IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT"),
    )

if __name__ == "__main__":
    main()
