"""Shared CapMonster + secret prefetch for attach-mode launch scripts."""
from __future__ import annotations

import os
from pathlib import Path

from core.captcha_runtime import apply_standard_captcha_env


def bootstrap_capmonster_env(repo: Path) -> None:
    from core.bootstrap_bot_launch import prefetch_launch_secrets

    prefetch_launch_secrets(repo)
    apply_standard_captcha_env(os.environ)

    key_ok = bool(
        (
            os.environ.get("CAPMONSTER_CLIENT_KEY")
            or os.environ.get("CAPMONSTER_API_KEY")
            or os.environ.get("capkey")
            or ""
        ).strip()
    )
    print(
        f"[Bootstrap] CapMonster reCAPTCHA: enabled={os.environ.get('USE_CAPMONSTER_CAPTCHA_SOLVER')}, "
        f"key={'yes' if key_ok else 'NO'}",
        flush=True,
    )
