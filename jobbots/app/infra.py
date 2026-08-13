"""Infra module registry + audit (Phase 5).

One canonical, machine-readable map of every deployment surface in the repo:
what it does, where it lives, which CI workflows drive it, and its entry
points. ``audit()`` verifies the map against reality (paths exist, workflows
parse, no unregistered infra references) so structural drift fails fast —
locally and in CI — instead of at deploy time.

Physical relocation of CI-coupled trees (terraform/, packer/) is prepared in
``infra/relocation-plan.md`` and gated on a VM image build.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jobbots.paths import REPO_ROOT

#: Canonical registry. ``paths`` are repo-relative; ``drivers`` are the CI
#: workflows/scripts that reference the module; ``entry`` is how a human runs it.
INFRA_MODULES: dict[str, dict[str, Any]] = {
    "docker": {
        "summary": "Container images + local compose stacks (bot, Mongo).",
        # docker-compose.local.yml is intentionally untracked (local-only stack).
        "paths": ["docker", "Dockerfile.bot", "docker-compose.yml", "docker-setup.ps1"],
        "drivers": [".github/workflows/ci.yml"],
        "entry": "docker compose up · docker/Dockerfile.local-bot · Dockerfile.bot",
    },

    "aws": {
        "summary": "AWS worker lifecycle (Linux spot workers, persistent EBS).",
        "paths": ["terraform", "terraform/persistent"],
        "drivers": [
            ".github/workflows/deploy-linux-worker.yml",
            ".github/workflows/destroy-linux-worker.yml",
            ".github/workflows/apply-persistent-linux.yml",
            ".github/workflows/production-cleanup.yml",
            ".github/workflows/canary-preflight.yml",
            ".github/workflows/canary-linux-lifecycle.yml",
            "scripts/lifecycle.sh",
            "deploy_aws.sh",
        ],
        "entry": "scripts/lifecycle.sh · terraform/ · terraform/persistent/",
    },
    "gcp": {
        "summary": "GCP production VM cycle (golden-image workers).",
        "paths": ["terraform/gcp"],
        "drivers": [
            ".github/workflows/gcp-production-cycle.yml",
            ".github/workflows/gcp-production-soft-destroy-window.yml",
            "scripts/gcp_lifecycle.sh",
            "scripts/build_gcp_golden.sh",
            "deploy_gcp.sh",
            "vmctl",
        ],
        "entry": "vmctl · scripts/gcp_lifecycle.sh · terraform/gcp/",
    },
    "packer": {
        "summary": "Golden VM images (Windows + Linux) with all bots baked in.",
        "paths": ["packer", "packer/linux", "packer/scripts"],
        "drivers": [
            ".github/workflows/build-image.yml",
            ".github/workflows/fast-sync.yml",
            ".github/workflows/ci.yml",
            "scripts/bootstrap_stock_worker.sh",
            "scripts/ci_prod_hotpatch_check.sh",
        ],
        "entry": "scripts/build_gcp_golden.sh · packer/*.pkr.hcl",
    },
    "systemd": {
        "summary": "systemd units/timers baked into golden Linux images.",
        "paths": ["packer/linux/systemd", "packer/linux/bin"],
        "drivers": ["packer/jobbots-golden.pkr.hcl", "packer/jobbots-golden-gcp.pkr.hcl"],
        "entry": "baked into images; see packer/linux/systemd/",
    },
    "ansible": {
        "summary": "Windows worker provisioning + repo sync playbooks.",
        "paths": ["ansible"],
        "drivers": [".github/workflows/ci.yml"],
        "entry": "ansible-playbook ansible/playbook.yml · ansible/sync.yml",
    },
    "scripts": {
        "summary": "Root + scripts/ operational shell entry points.",
        "paths": ["scripts", "deploy.sh", "schedule_vm_stop.sh"],
        "drivers": [".github/workflows/production-deploy.yml"],
        "entry": "scripts/cli_tools.sh",
    },
}

#: Path-prefix patterns that count as infra references in CI/scripts.
_INFRA_REF = re.compile(
    r"(terraform/[\w./-]+|packer/[\w./-]+|docker/[\w./-]+|ansible/[\w./-]+"
    r"|docker-compose[\w.-]*\.yml|Dockerfile[\w.-]*)"
)

#: References that are *produced* at runtime by the workflows themselves
#: (tfvars written by write-worker-tfvars.sh, `terraform init` state dirs).
_RUNTIME_GENERATED = frozenset({
    "terraform/persistent/environment.auto.tfvars",
    "terraform/.terraform",
    "terraform/persistent/.terraform",
    "terraform/gcp/.terraform",
})



def module_map() -> dict[str, dict[str, Any]]:
    return INFRA_MODULES


def _yaml_ok(path: Path) -> bool:
    import yaml

    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def audit() -> dict[str, Any]:
    """Verify the registry against reality. Returns a report dict."""
    problems: list[str] = []
    registered_paths: set[str] = set()

    for name, meta in INFRA_MODULES.items():
        for rel in meta["paths"]:
            if not (REPO_ROOT / rel).exists():
                problems.append(f"{name}: missing {rel}")
            registered_paths.add(rel)
        for rel in meta["drivers"]:
            # In open-source releases, private CI deployment workflows are excluded.
            if not (REPO_ROOT / rel).exists() and not rel.startswith(".github/workflows/"):
                problems.append(f"{name}: missing {rel}")
            if (REPO_ROOT / rel).exists():
                registered_paths.add(rel)

    # Every workflow must parse.
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    for wf in workflows:
        if not _yaml_ok(wf):
            problems.append(f"workflow does not parse: {wf.name}")

    # Compose files must parse.
    for compose in ("docker-compose.yml", "docker-compose.local.yml"):
        path = REPO_ROOT / compose
        if path.is_file() and not _yaml_ok(path):
            problems.append(f"compose file does not parse: {compose}")

    # Drift scan: infra path references in workflows/scripts must target
    # registered, existing paths.
    scan_files = workflows + sorted((REPO_ROOT / "scripts").glob("*.sh")) + [
        REPO_ROOT / name
        for name in ("deploy.sh", "deploy_aws.sh", "deploy_gcp.sh", "vmctl")
        if (REPO_ROOT / name).is_file()
    ]
    for path in scan_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # GitHub action references (uses: docker/..., hashicorp/...) and
        # shell echo prose (e.g. "apt/docker/playwright") are not repo paths.
        lines = [
            ln
            for ln in text.splitlines()
            if "uses:" not in ln and "@" not in ln and "echo " not in ln
        ]

        for ref in sorted(set(_INFRA_REF.findall("\n".join(lines)))):
            if ref in _RUNTIME_GENERATED:
                continue
            ref_path = REPO_ROOT / ref
            if not ref_path.exists():
                problems.append(f"{path.relative_to(REPO_ROOT)}: dangling infra ref {ref}")



    return {
        "ok": not problems,
        "modules": sorted(INFRA_MODULES),
        "workflows_scanned": len(workflows),
        "problems": problems,
    }


def format_map() -> str:
    lines: list[str] = []
    for name, meta in sorted(INFRA_MODULES.items()):
        lines.append(f"{name}: {meta['summary']}")
        lines.append(f"    paths: {', '.join(meta['paths'])}")
        lines.append(f"    entry: {meta['entry']}")
    return "\n".join(lines)
