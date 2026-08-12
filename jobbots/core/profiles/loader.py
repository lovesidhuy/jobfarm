"""Layered profile loader.

Configuration precedence (later layers win for overrides):

    default settings
        ↓
    profile manifest  (profiles/<owner>/<name>/profile.yaml + searches.yaml)
        ↓
    environment variables  (only when the manifest allows the override)
        ↓
    secret manager  (automation_monorepo/core/secret_manager.get_secret,
                     Infisical → .env fallback)

Phase-1 contract: manifests *reference* the existing runtime config modules
(``automation_monorepo/config/{general,it}/*.py``), the per-profile QA answer
bank JSON, and the resume files. They never duplicate or replace the frozen
Q&A configuration — ``JOB_PROFILE`` / ``BOT_NAME`` keep selecting those
exactly as production does today.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from jobbots.paths import PROFILES_ROOT, REPO_ROOT


@dataclass(frozen=True)
class Profile:
    """A resolved job-hunt profile (e.g. Jane/it)."""

    owner: str
    name: str
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    searches: dict[str, Any] = field(default_factory=dict)

    @property
    def job_profile(self) -> str:
        """Value production code expects in JOB_PROFILE (``IT`` / ``General``)."""
        return str(self.manifest.get("job_profile") or self.name).strip()

    @property
    def bot_name(self) -> str:
        return str(self.manifest.get("bot_name") or f"indeed_{self.name}").strip()

    @property
    def config_module(self) -> str:
        return str(self.manifest.get("config_module") or f"config.{self.name}")

    @property
    def answer_bank(self) -> Path | None:
        rel = self.manifest.get("answer_bank")
        return (REPO_ROOT / rel) if rel else None

    @property
    def resumes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, rel in (self.manifest.get("resumes") or {}).items():
            out[str(key)] = str(REPO_ROOT / rel)
        return out

    @property
    def env_overrides(self) -> dict[str, str]:
        """Env var overrides declared by the manifest (secrets stay out of YAML)."""
        return {str(k): str(v) for k, v in (self.manifest.get("env") or {}).items()}

    def validate(self) -> list[str]:
        """Referential-integrity checks. Returns a list of problems (empty = ok)."""
        problems: list[str] = []
        bank = self.answer_bank
        if bank is not None and not bank.is_file():
            problems.append(f"answer bank missing: {bank}")
        for key, path in self.resumes.items():
            if not Path(path).is_file():
                problems.append(f"resume '{key}' missing: {path}")
        return problems


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def profile_dir(owner: str, name: str, *, profiles_root: Path | None = None) -> Path:
    base = profiles_root or PROFILES_ROOT
    p = base / owner / name
    if not p.is_dir() and (base / "example" / name).is_dir():
        return base / "example" / name
    return p


def load_profile(
    name: str,
    *,
    owner: str = "example",
    profiles_root: Path | None = None,
) -> Profile:
    """Load a profile manifest by name (``general`` | ``it``)."""
    root = profile_dir(owner, name, profiles_root=profiles_root)
    if not root.is_dir():
        raise FileNotFoundError(f"profile not found: {root}")
    return Profile(
        owner=owner,
        name=name,
        root=root,
        manifest=_read_yaml(root / "profile.yaml"),
        searches=_read_yaml(root / "searches.yaml"),
    )


def available_profiles(
    *, owner: str = "example", profiles_root: Path | None = None
) -> list[str]:
    """Return manifest names present for ``owner``."""
    base = profiles_root or PROFILES_ROOT
    owner_root = base / owner
    if not owner_root.is_dir():
        owner_root = base / "example"
    if not owner_root.is_dir():
        return []
    return [p.name for p in sorted(owner_root.iterdir()) if p.is_dir() and (p / "profile.yaml").is_file()]


def profile_env(profile: Profile, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Compute the environment a bot for this profile runs with.

    Layering: current environment → manifest-declared overrides. The two
    identity variables always follow the manifest, exactly as
    ``core/supervised_bots.py`` assigns them today.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env["JOB_PROFILE"] = profile.job_profile
    env["BOT_NAME"] = profile.bot_name
    env.update(profile.env_overrides)
    return env


def resolve_secret(name: str, default: str = "") -> str:
    """Top configuration layer: secret manager (Infisical → .env fallback)."""
    from jobbots.paths import ensure_monorepo_on_path

    ensure_monorepo_on_path()
    from jobbots.core.secret_manager import get_secret


    return get_secret(name, default)
