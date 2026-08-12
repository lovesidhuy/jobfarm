"""Phase 4 gate: profile runtime cutover.

Proves ``jobbots.core.profiles`` runtime activation produces *bit-identical*
identity env to the legacy production path (``ensure_bot_runtime_defaults`` /
``apply_bot_runtime_env_overwrite``), for every supervised bot row, and that
manifest/registry drift fails loudly. No browser, no network, no AI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"
for _p in (str(_REPO), str(_MONOREPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jobbots.core.profiles import (  # noqa: E402
    IDENTITY_KEYS,
    activate_profile,
    assert_manifest_matches_registry,
    bot_env,
    load_profile,
)
from jobbots.core.supervised_bots import (  # noqa: E402
    apply_bot_runtime_env_overwrite,
    ensure_bot_runtime_defaults,
    supervised_bot_config_by_name,
    supervised_bot_configs,
)

_MANIFEST_FOR_JOB_PROFILE = {"IT": "it", "GENERAL": "general"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Snapshot/restore os.environ; neutralize the browser/secret side-effect
    tail of the legacy runtime functions (identically for both paths) so the
    identity-key comparison is hermetic. The delegates are still the same
    production functions — activation calls nothing else."""
    saved = dict(os.environ)
    from jobbots.core import supervised_bots as sb
    from jobbots.core import captcha_runtime as cr

    monkeypatch.setattr(sb, "_stamp_browser_profile_ids", lambda *a, **k: None)
    monkeypatch.setattr(sb, "_ensure_infisical_secrets_in_env", lambda *a, **k: None)
    monkeypatch.setattr(cr, "apply_standard_captcha_env", lambda *a, **k: None)
    monkeypatch.setattr(cr, "apply_standard_captcha_env_overwrite", lambda *a, **k: None)
    yield
    os.environ.clear()
    os.environ.update(saved)




def _legacy_env(bot_name: str) -> dict[str, str]:
    """Identity env exactly as the production bot wrapper sets it."""
    for key in IDENTITY_KEYS:
        os.environ.pop(key, None)
    ensure_bot_runtime_defaults(bot_name)
    return {key: os.environ.get(key, "") for key in IDENTITY_KEYS}


def test_manifests_match_registry_no_drift():
    for name in ("it", "general"):
        profile = load_profile(name)
        problems = assert_manifest_matches_registry(profile)
        assert not problems, f"{name}: {problems}"


def test_every_bot_row_covered_by_a_manifest():
    """Every supervised bot's job_profile maps to a manifest of the same name."""
    rows = supervised_bot_configs(include_disabled=True)
    assert len(rows) >= 8  # full supervised farm, paused bots included

    for row in rows:
        job_profile = str(row.get("job_profile") or "").upper()
        manifest_name = _MANIFEST_FOR_JOB_PROFILE[job_profile]
        profile = load_profile(manifest_name)
        assert profile.job_profile.upper() == job_profile
        # The bot's portal must be enabled by its profile's manifest.
        portals = [str(p).lower() for p in (profile.manifest.get("portals") or [])]
        assert str(row.get("portal") or "").lower() in portals


def test_bot_env_matches_legacy_for_all_bots(clean_env):
    for row in supervised_bot_configs(include_disabled=True):
        bot_name = row["bot_name"]
        manifest_name = _MANIFEST_FOR_JOB_PROFILE[str(row["job_profile"]).upper()]
        profile = load_profile(manifest_name)
        new_env = bot_env(profile, bot_name=bot_name)
        legacy_env = _legacy_env(bot_name)
        for key in IDENTITY_KEYS:
            assert new_env[key] == legacy_env[key], f"{bot_name}.{key}: {new_env[key]!r} != {legacy_env[key]!r}"


def test_activate_profile_matches_legacy_mutation(clean_env):
    for key in IDENTITY_KEYS:
        os.environ.pop(key, None)
    activate_profile("it")  # default bot: indeed_it
    new_env = {key: os.environ.get(key, "") for key in IDENTITY_KEYS}
    for key in IDENTITY_KEYS:
        os.environ.pop(key, None)
    ensure_bot_runtime_defaults("indeed_it")
    legacy_env = {key: os.environ.get(key, "") for key in IDENTITY_KEYS}
    assert new_env == legacy_env


def test_activate_profile_overwrite_matches_legacy(clean_env):
    activate_profile("general", bot_name="workopolis_general", overwrite=True)
    new_env = {key: os.environ.get(key, "") for key in IDENTITY_KEYS}
    apply_bot_runtime_env_overwrite(supervised_bot_config_by_name("workopolis_general"))
    legacy_env = {key: os.environ.get(key, "") for key in IDENTITY_KEYS}
    assert new_env == legacy_env


def test_activate_profile_rejects_registry_drift(monkeypatch):
    """If manifest and registry disagree, activation must fail loudly."""
    profile = load_profile("it")
    drifted = type(profile)(
        owner=profile.owner,
        name=profile.name,
        root=profile.root,
        manifest={**profile.manifest, "job_profile": "General"},
        searches=profile.searches,
    )
    problems = assert_manifest_matches_registry(drifted)
    assert any("job_profile drift" in p for p in problems)

    import jobbots.core.profiles.runtime as runtime

    monkeypatch.setattr(runtime, "load_profile", lambda *a, **k: drifted)
    with pytest.raises(ValueError, match="drifted"):
        runtime.activate_profile("it")


def test_manifest_env_overlay_never_overrides_real_env(clean_env):
    profile = load_profile("it")
    overlays = profile.env_overrides
    assert overlays  # manifest declares at least one overlay
    key = sorted(overlays)[0]
    os.environ[key] = "real-env-wins"
    env = bot_env(profile)
    assert env[key] == "real-env-wins"
