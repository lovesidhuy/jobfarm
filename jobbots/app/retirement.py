"""Retirement audit (Phase 6): duplication + dead-code evidence, one command.

Three detectors, all read-only and offline:

* ``duplicate_groups()`` — byte-identical ``*.py`` files under ``master/``
  (the vendored copies across the two master trees and their nested
  ``Auto_job_applier_glassdoor`` duplicates).
* ``unreferenced_modules()`` — tracked modules nothing references (import
  graph + string scan over every tracked file). Zero-reference modules are
  deletion *candidates*; actual deletion is gated per the freeze rules.
* ``shim_inventory()`` — compatibility shims that must stay until the VM
  double-run parity gate passes (alias shim, master bridges, marker files).

``jobbots audit --write`` emits ``docs/RETIREMENT_MANIFEST.md`` — the
ready-to-execute removal list for after sustained VM parity.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from jobbots.paths import REPO_ROOT

#: Shims that keep old import paths working. Removal gate: sustained VM
#: double-run parity (old path applies for real, new path compares).
SHIM_PATHS = [
    "automation_monorepo/core/__init__.py",
    "master/gen_indeed/Auto_indeed/modules/_monorepo_bridge.py",
    "master/gen_indeed/Auto_indeed/Auto_job_applier_glassdoor/modules/_monorepo_bridge.py",
    "master/it_indeed cwgeopy/Auto_indeed/modules/_monorepo_bridge.py",
    "master/it_indeed cwgeopy/Auto_indeed/Auto_job_applier_glassdoor/modules/_monorepo_bridge.py",
]

#: Deliberate keeps despite the "legacy" name (still referenced).
LEGACY_KEEPS = [
    "jobbots/core/portals/mongo_storage_legacy.py",
    "jobbots/core/portals/training_logger_legacy.py",
]


def _tracked_files(suffix: str = ".py") -> list[str]:
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.endswith(suffix) and "__pycache__" not in ln]


def duplicate_groups(root: str = "master") -> list[dict[str, Any]]:
    """Byte-identical Python files under *root*, grouped by content hash."""
    by_hash: dict[str, list[str]] = defaultdict(list)
    sizes: dict[str, int] = {}
    for rel in _tracked_files():
        if not rel.startswith(root + "/"):
            continue
        data = (REPO_ROOT / rel).read_bytes()
        by_hash[hashlib.sha1(data).hexdigest()].append(rel)
        sizes[rel] = len(data)
    groups = []
    for digest, paths in by_hash.items():
        if len(paths) > 1:
            groups.append(
                {
                    "sha1": digest[:12],
                    "count": len(paths),
                    "bytes": sizes[paths[0]],
                    "paths": sorted(paths),
                }
            )
    return sorted(groups, key=lambda g: (-g["count"], -g["bytes"]))


def unreferenced_modules() -> list[dict[str, Any]]:
    """Tracked modules (jobbots/core, master shims) with zero references.

    A reference is any tracked file (excluding the module itself) containing
    the module's dotted name or stem as a substring. Conservative by design:
    dynamic/string references count as references, so false positives are
    *kept* — nothing referenced is ever reported as dead. Substring search
    over a single joined blob keeps this O(corpus) instead of O(n×m) regex.
    """
    files = _tracked_files()
    self_text: dict[str, str] = {}
    blob_parts: list[str] = []
    for rel in files:
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        self_text[rel] = text
        blob_parts.append(text)  # contents only — never the path itself
    blob = "\n".join(blob_parts)


    candidates: list[dict[str, Any]] = []
    scopes = ("jobbots/core/", "master/")
    for rel in files:
        if not rel.startswith(scopes):
            continue
        stem = Path(rel).stem
        if stem in {"__init__", "_bootstrap"}:
            continue
        dotted = rel[:-3].replace("/", ".")
        # The alias shim makes bare `core.X` equivalent to `jobbots.core.X`.
        aliases = {dotted, stem}
        if dotted.startswith("jobbots.core."):
            aliases.add(dotted[len("jobbots.core."):])
            aliases.add("core." + dotted[len("jobbots.core."):])
        own = self_text[rel]
        dead = True
        for alias in aliases:
            if alias not in blob:
                continue
            # Present somewhere; referenced only if it appears outside itself.
            if blob.count(alias) > own.count(alias):
                dead = False
                break
        if dead:
            candidates.append({"path": rel, "bytes": (REPO_ROOT / rel).stat().st_size})
    return sorted(candidates, key=lambda c: c["path"])



def shim_inventory() -> list[dict[str, str]]:
    out = []
    for rel in SHIM_PATHS:
        exists = (REPO_ROOT / rel).is_file()
        out.append({"path": rel, "present": str(exists)})
    return out


def manifest() -> dict[str, Any]:
    dups = duplicate_groups()
    unref = unreferenced_modules()
    return {
        "duplicate_groups": dups,
        "duplicate_files_total": sum(g["count"] for g in dups),
        "duplicate_bytes_total": sum(g["bytes"] * (g["count"] - 1) for g in dups),
        "unreferenced_modules": unref,
        "legacy_keeps": LEGACY_KEEPS,
        "shims": shim_inventory(),
        "removal_gate": (
            "Delete only after sustained VM double-run parity: old path applies "
            "for real, new path compares; zero wrong-profile applications; "
            "verified-application rate unchanged per profile."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Retirement manifest (generated by `jobbots audit --write`)",
        "",
        f"Removal gate: {report['removal_gate']}",
        "",
        "## Byte-identical duplicates under master/ "
        f"({report['duplicate_files_total']} files, "
        f"~{report['duplicate_bytes_total'] // 1024} KiB redundant)",
        "",
    ]
    for g in report["duplicate_groups"]:
        lines.append(f"### {g['count']}× {g['bytes'] // 1024} KiB — sha1 {g['sha1']}")
        for p in g["paths"]:
            lines.append(f"- `{p}`")
        lines.append("")
    lines.append("## Unreferenced modules (deletion candidates — evidence only)")
    lines.append("")
    lines.append(
        "> ⚠️ This scan sees imports and string references only. Scripts meant "
        "to be run directly by path (`python scripts/foo.py`) look unreferenced "
        "here. Review every entry by hand; removal additionally requires the "
        "removal gate above."
    )
    lines.append("")
    if report["unreferenced_modules"]:
        for m in report["unreferenced_modules"]:
            lines.append(f"- `{m['path']}` ({m['bytes']} B)")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Legacy keeps (referenced — do not remove)")
    for rel in report["legacy_keeps"]:
        lines.append(f"- `{rel}`")
    lines.append("")
    lines.append("## Compatibility shims (remove only after the removal gate)")
    for s in report["shims"]:
        lines.append(f"- `{s['path']}` (present: {s['present']})")
    lines.append("")
    return "\n".join(lines)
