"""Phase 6 gate: retirement audit + provably-dead removal.

Offline, read-only detectors. The one executed deletion
(``jobbots/core/portals/captcha_handler_legacy.py``) had zero references in
the entire tracked tree; everything else is evidence gated on VM parity.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest
from jobbots.app import retirement  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (_REPO / "master").exists(),
    reason="master/ tree retired in open-source release",
)

def test_duplicate_detection_quantifies_master_duplication():
    groups = retirement.duplicate_groups()
    assert len(groups) >= 30  # vendored copies across the two master trees
    total_files = sum(g["count"] for g in groups)
    assert total_files >= 70
    # the known 4x vendored AI-connection copies
    four_x = [g for g in groups if g["count"] >= 4]
    assert four_x, "expected the 4-way vendored duplicates"
    paths = {p for g in four_x for p in g["paths"]}
    assert any("openaiConnections.py" in p for p in paths)


def test_no_unreferenced_modules_after_dead_removal():
    """The only zero-reference module was removed in this phase."""
    unref = retirement.unreferenced_modules()
    assert "jobbots/core/portals/captcha_handler_legacy.py" not in {
        m["path"] for m in unref
    }
    assert not (_REPO / "jobbots/core/portals/captcha_handler_legacy.py").exists()


def test_legacy_keeps_are_actually_referenced():
    """Legacy-named files listed as keeps must have references (else they
    would show up as unreferenced candidates)."""
    unref_paths = {m["path"] for m in retirement.unreferenced_modules()}
    for rel in retirement.LEGACY_KEEPS:
        assert (_REPO / rel).is_file()
        assert rel not in unref_paths, f"{rel} is unreferenced — move to candidates"


def test_shims_present_and_tracked():
    shims = retirement.shim_inventory()
    assert len(shims) == 5
    assert all(s["present"] == "True" for s in shims)


def test_manifest_write(tmp_path):
    report = retirement.manifest()
    text = retirement.render_markdown(report)
    assert "Removal gate" in text
    assert "openaiConnections.py" in text
    assert report["removal_gate"]
    out = tmp_path / "manifest.md"
    out.write_text(text, encoding="utf-8")
    assert out.read_text(encoding="utf-8") == text


def test_cli_audit(capsys):
    from jobbots.app.cli import main

    assert main(["audit"]) == 0
    out = capsys.readouterr().out
    assert "duplicate groups under master/" in out
    assert "unreferenced modules" in out
    assert "removal gate" in out
