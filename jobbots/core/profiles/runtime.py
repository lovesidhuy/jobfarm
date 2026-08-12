"""Profile runtime cutover (Phase 4).

Bots and tooling can now read identity through ``jobbots.core.profiles``
instead of reaching into ``core.supervised_bots`` directly. **No logic is
duplicated**: activation delegates to the canonical supervised-bot registry
(``ensure_bot_runtime_defaults`` / ``apply_bot_runtime_env_overwrite``), so
BOT_NAME / CDP_PORT / BOT_INSTANCE_ID / CHROME_PROFILE_DIR / JOB_PROFILE are
bit-identical to production by construction.

The profile manifests are cross-checked against the registry on every
activation — a drift between ``profiles/Jane/<name>/profile.yaml`` and
``supervised_bots._BOT_ROWS`` fails loudly here, never in a browser session.

Config layering (unchanged): defaults → profile manifest → env vars →
secret manager. Manifest env overlays are applied with ``setdefault``
semantics so real environment variables keep winning.
"""
from __future__ import annotations

import os
from typing import Any

from jobbots.core.profiles.loader import Profile, load_profile

#: Identity keys every bot runtime must carry (canonical registry values).
IDENTITY_KEYS = ("BOT_NAME", "CDP_PORT", "BOT_INSTANCE_ID", "CHROME_PROFILE_DIR", "JOB_PROFILE")


def _registry_cfg(bot_name: str) -> dict[str, Any]:
    from jobbots.core.supervised_bots import supervised_bot_config_by_name

    return supervised_bot_config_by_name(bot_name)


def assert_manifest_matches_registry(profile: Profile) -> list[str]:
    """Cross-check a manifest against the canonical supervised-bot registry.

    Returns a list of drift problems (empty = manifest agrees with registry).
    """
    problems: list[str] = []
    try:
        cfg = _registry_cfg(profile.bot_name)
    except Exception as exc:
        return [f"bot_name {profile.bot_name!r} not in supervised registry: {exc}"]
    reg_profile = str(cfg.get("job_profile") or "")
    if reg_profile != profile.job_profile:
        problems.append(
            f"job_profile drift: manifest={profile.job_profile!r} registry={reg_profile!r}"
        )
    enabled = [str(p).lower() for p in (profile.manifest.get("portals") or [])]
    reg_portal = str(cfg.get("portal") or "").lower()
    if enabled and reg_portal and reg_portal not in enabled:
        problems.append(
            f"registry portal {reg_portal!r} not in manifest portals {enabled}"
        )
    problems.extend(profile.validate())
    return problems


def bot_env(profile: Profile, *, bot_name: str | None = None) -> dict[str, str]:
    """Non-mutating runtime env for a bot of this profile.

    Values come from the canonical supervised-bot registry (same source
    ``ensure_bot_runtime_defaults`` reads), with manifest env overlays applied
    underneath any pre-existing process env (env vars keep winning).
    """
    name = bot_name or profile.bot_name
    cfg = _registry_cfg(name)
    env: dict[str, str] = {}
    for key, value in (profile.env_overrides or {}).items():
        env[key] = os.environ.get(key, value)  # manifest below real env
    env.update(
        {
            "BOT_NAME": str(cfg["bot_name"]),
            "CDP_PORT": str(cfg["cdp_port"]),
            "BOT_INSTANCE_ID": str(cfg["bot_instance_id"]),
            "CHROME_PROFILE_DIR": str(cfg["profile_dir"]),
            "JOB_PROFILE": str(cfg["job_profile"]),
        }
    )
    return env


def activate_profile(
    name: str,
    *,
    owner: str = "example",
    bot_name: str | None = None,
    overwrite: bool = False,
) -> Profile:
    """Activate a profile in the current process — registry-delegating.

    Cross-checks the manifest against the registry, then applies identity
    exactly as production does (``ensure_bot_runtime_defaults`` or, with
    ``overwrite=True``, ``apply_bot_runtime_env_overwrite``).
    """
    profile = load_profile(name, owner=owner)
    problems = assert_manifest_matches_registry(profile)
    if problems:
        raise ValueError(
            f"profile {owner}/{name} drifted from the supervised registry: {problems}"
        )

    target_bot = bot_name or profile.bot_name
    if overwrite:
        from jobbots.core.supervised_bots import apply_bot_runtime_env_overwrite

        apply_bot_runtime_env_overwrite(_registry_cfg(target_bot))
    else:
        from jobbots.core.supervised_bots import ensure_bot_runtime_defaults

        ensure_bot_runtime_defaults(target_bot)

    # Manifest env overlays: setdefault — real env vars keep precedence.
    for key, value in profile.env_overrides.items():
        os.environ.setdefault(key, value)
    return profile
