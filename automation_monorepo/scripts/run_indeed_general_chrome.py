#!/usr/bin/env python3
"""Launch Indeed General with shared CF/CapMonster bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from core.bootstrap_bot_launch import bootstrap_bot_launch

    bootstrap_bot_launch(
        repo=repo,
        bot_name="indeed_general",
        bot_import="bots.indeed_general",
        cdp_port="9223",
        job_profile="General",
        profile_subdir="indeed_general",
        extra_secret_names=("IMAP_EMAIL_GENERAL", "IMAP_APP_PASSWORD_GENERAL"),
    )


if __name__ == "__main__":
    main()
