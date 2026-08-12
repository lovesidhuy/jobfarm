"""Smoke tests for the multi-bot supervisor (`supervisor.py`).

Run from the monorepo root (this directory):

    python3 _smoke_supervisor.py

Validates BOT_CONFIGs, subprocess env construction (including IMAP profile
routing), and a dry-run of ``run_bot`` per bot with ``subprocess.Popen``
mocked (no real browser or bot execution).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import supervisor as sup  # noqa: E402  (needs ROOT on path)
from core.supervised_bots import supervised_bot_config_by_name  # noqa: E402
from core.supervisor_runtime import resolve_bot_python  # noqa: E402


def _validate_bot_configs() -> None:
    assert sup.BOT_CONFIGS, "BOT_CONFIGS empty"
    names = [c["bot_name"] for c in sup.BOT_CONFIGS]
    assert len(names) == len(set(names)), f"duplicate bot_name: {names}"

    ports = [c["cdp_port"] for c in sup.BOT_CONFIGS]
    assert len(ports) == len(set(ports)), f"duplicate cdp_port: {ports}"

    for cfg in sup.BOT_CONFIGS:
        for key in ("script", "bot_name", "cdp_port", "bot_instance_id", "profile_dir", "profile"):
            assert key in cfg, f"cfg missing {key!r}: {cfg}"

        script = sup.base_dir / "bots" / cfg["script"]
        assert script.is_file(), f"bot script not found: {script}"

        assert cfg["profile_dir"], f"empty profile_dir for {cfg['bot_name']}"


def _validate_imap_routing() -> None:
    """IT-ish profiles pick IMAP_*_IT; others pick IMAP_*_GENERAL.

    Uses the merged env (``os.environ`` + optional ``.env``) so the assertion
    stays valid when a local ``.env`` overrides credentials.
    """
    cfg_it = next(c for c in sup.BOT_CONFIGS if c["bot_name"] == "indeed_it")
    env_it = sup.build_subprocess_env(cfg_it, "run-smoke")
    expect_mail_it = env_it.get("IMAP_EMAIL_IT", "").strip() or env_it.get("IMAP_EMAIL", "").strip()
    expect_pwd_it = env_it.get("IMAP_APP_PASSWORD_IT", "").strip() or env_it.get(
        "IMAP_APP_PASSWORD", ""
    ).strip()
    assert env_it["IMAP_EMAIL"] == expect_mail_it
    assert env_it["IMAP_APP_PASSWORD"] == expect_pwd_it

    cfg_gen = supervised_bot_config_by_name("indeed_general")
    env_gen = sup.build_subprocess_env(cfg_gen, "run-smoke")
    expect_mail_gen = env_gen.get("IMAP_EMAIL_GENERAL", "").strip() or env_gen.get(
        "IMAP_EMAIL", ""
    ).strip()
    expect_pwd_gen = env_gen.get("IMAP_APP_PASSWORD_GENERAL", "").strip() or env_gen.get(
        "IMAP_APP_PASSWORD", ""
    ).strip()
    assert env_gen["IMAP_EMAIL"] == expect_mail_gen
    assert env_gen["IMAP_APP_PASSWORD"] == expect_pwd_gen


def _validate_run_bot_wiring() -> None:
    """Each config: mocked Popen receives correct argv, cwd, and env keys."""

    class _Proc:
        returncode = 0

        def wait(self, timeout=None) -> None:
            return None

    for cfg in sup.BOT_CONFIGS:
        mock_db = MagicMock()
        mock_db.connected = False
        mock_db.start_run.return_value = f"run-id-{cfg['bot_name']}"
        mock_proc = _Proc()

        with patch.object(sup, "MongoStore", return_value=mock_db):
            # kill_bot_chromes shells out via PowerShell on Windows runners and
            # would consume the Popen mock, inflating its call count. Skip it
            # here — its behaviour is verified separately in production runs.
            with patch.object(sup, "kill_bot_chromes", return_value=0):
                with patch.object(sup.subprocess, "Popen", autospec=True) as popen:
                    popen.return_value = mock_proc
                    sup.run_bot(cfg)

        mock_db.start_run.assert_called_once()
        mock_db.end_run.assert_called_once()

        popen.assert_called_once()
        args, kwargs = popen.call_args
        argv = args[0]
        assert argv[0] == str(resolve_bot_python(sup.base_dir))
        assert argv[1] == "-u"
        assert argv[2] == str(sup.base_dir / "bots" / cfg["script"])
        assert kwargs["cwd"] == str(sup.base_dir)

        env = kwargs["env"]
        assert env["BOT_NAME"] == cfg["bot_name"]
        assert env["CDP_PORT"] == cfg["cdp_port"]
        assert env["BOT_INSTANCE_ID"] == str(cfg["bot_instance_id"])
        assert env["CHROME_PROFILE_DIR"] == cfg["profile_dir"]
        assert env["JOB_PROFILE"] == cfg["profile"]
        assert env["CURRENT_RUN_ID"] == f"run-id-{cfg['bot_name']}"
        assert env.get("SKIP_USER_START") == "1"
        assert env.get("AUTONOMOUS_SUPERVISOR") == "1"


def _validate_portal_imports() -> None:
    """Each portal module must import under both JOB_PROFILEs.

    A missing symbol in ``config/it/search.py`` or ``config/general/search.py``
    (e.g. ``glassdoor_search_terms``) only manifests at portal import time
    and crashes the onboarder / supervisor on the VM. This catches it in CI.
    """
    import importlib
    import os

    # Portal modules require playwright + seleniumbase. Skip when those
    # heavy deps aren't installed (local dev) — CI always has them.
    try:
        import playwright  # noqa: F401
        import seleniumbase  # noqa: F401
    except ImportError:
        print("  (skipped: playwright/seleniumbase not installed locally)")
        return

    portal_modules = (
        "core.portals.indeed",
        "core.portals.indeed_it",
        "core.portals.glassdoor",
        "core.portals.glassdoor_it",
        "core.portals.workopolis",
        "core.portals.workopolis_it",
    )
    for profile in ("IT", "GENERAL"):
        os.environ["JOB_PROFILE"] = profile
        # config.search / config.questions / etc. read JOB_PROFILE at import.
        for mod in (
            "config.search",
            "config.questions",
            "config.personals",
            "config.resume",
        ):
            if mod in sys.modules:
                del sys.modules[mod]
        if "config" in sys.modules:
            del sys.modules["config"]
        for mod in portal_modules:
            try:
                if mod in sys.modules:
                    importlib.reload(sys.modules[mod])
                else:
                    importlib.import_module(mod)
            except Exception as e:
                raise AssertionError(
                    f"portal import failed for JOB_PROFILE={profile} module={mod}: {e!r}"
                )


def main() -> int:
    _validate_bot_configs()
    print("supervisor smoke: BOT_CONFIGS + bot scripts OK")

    _validate_portal_imports()
    print("supervisor smoke: portal modules import under IT + GENERAL OK")

    _validate_imap_routing()
    print("supervisor smoke: build_subprocess_env (IMAP routing) OK")

    _validate_run_bot_wiring()
    print("supervisor smoke: run_bot wiring (mocked Popen) OK")

    print("\nSUPERVISOR SMOKE PASSED: main supervisor wiring is healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
