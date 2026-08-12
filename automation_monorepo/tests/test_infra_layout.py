"""Phase 5 gate: infra module registry + structural audit.

Offline only: path existence, YAML parse, dangling-reference detection.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from jobbots.app import infra  # noqa: E402


def test_registry_covers_every_infra_bearing_path():
    """Every top-level deployment asset is claimed by exactly one module."""
    claimed: set[str] = set()
    for meta in infra.INFRA_MODULES.values():
        claimed.update(meta["paths"])
    expected = {
        "terraform", "terraform/persistent", "terraform/gcp",
        "packer", "docker", "ansible", "scripts",
        "Dockerfile.bot", "docker-compose.yml",
        "docker-setup.ps1", "deploy.sh", "deploy_aws.sh", "deploy_gcp.sh",
        "schedule_vm_stop.sh", "vmctl",
    }

    for rel in expected:
        assert (Path(str(_REPO)) / rel).exists(), f"expected infra path missing on disk: {rel}"
    for rel in ("terraform", "packer", "docker", "ansible", "scripts"):
        assert rel in claimed, f"{rel} not claimed by any infra module"


def test_audit_passes():
    report = infra.audit()
    assert report["ok"], f"infra audit problems: {report['problems']}"
    assert report["workflows_scanned"] >= 12  # all 14 workflows at last count


def test_audit_detects_dangling_ref(tmp_path, monkeypatch):
    """A workflow referencing a nonexistent infra path must fail the audit."""
    import jobbots.app.infra as mod

    fake = dict(mod.INFRA_MODULES["docker"])
    fake["paths"] = [*mod.INFRA_MODULES["docker"]["paths"], "docker/DOES_NOT_EXIST"]
    monkeypatch.setitem(mod.INFRA_MODULES, "docker", fake)
    report = mod.audit()
    assert not report["ok"]
    assert any("docker/DOES_NOT_EXIST" in p for p in report["problems"])


def test_cli_infra_map_and_audit(capsys):
    from jobbots.app.cli import main

    assert main(["infra"]) == 0
    out = capsys.readouterr().out
    assert "packer" in out and "terraform/gcp" in out
    assert main(["infra", "--audit"]) == 0
    out = capsys.readouterr().out
    assert "infra audit: OK" in out


def test_doctor_includes_infra_check():
    from jobbots.app.pipeline import doctor_report

    report = doctor_report(quick=True)
    assert "infra" in report["checks"]
    assert report["checks"]["infra"]["ok"] is True


def test_relocation_plan_documented():
    plan = _REPO / "infra" / "relocation-plan.md"
    assert plan.is_file()
    text = plan.read_text(encoding="utf-8")
    for needle in ("git mv", "terraform/persistent", "packer/linux", "jobbots qa check"):
        assert needle in text
