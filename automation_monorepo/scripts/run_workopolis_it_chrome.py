#!/usr/bin/env python3
"""Launch Workopolis IT with shared CF/CapMonster bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from core.bootstrap_bot_launch import bootstrap_bot_launch

    bootstrap_bot_launch(
        repo=repo,
        bot_name="workopolis_it",
        bot_import="bots.workopolis_it",
        cdp_port="9230",
        job_profile="IT",
        profile_subdir="workopolis_it",
        extra_secret_names=("IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT"),
    )


if __name__ == "__main__":
    main()
