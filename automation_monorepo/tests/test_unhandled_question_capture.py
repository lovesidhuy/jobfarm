"""Capture-on-drop diagnostics: question-area screenshots before a drop.

Covers the canonical helper (fake Playwright page — no browser) and proves
every portal's drop point is wired: SmartApply (Indeed/Workopolis/Glassdoor
forms), the ATS engine (greenhouse/ashby/lever/bamboohr), both Glassdoor
loops, and the LinkedIn JS runner. Offline, no network, no AI.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MONOREPO = _REPO / "automation_monorepo"
for _p in (str(_REPO), str(_MONOREPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from jobbots.core.apply_diagnostics import (  # noqa: E402
    _safe,
    capture_unhandled_question,
    unhandled_questions_dir,
)


class _FakeElement:
    def __init__(self, box=None):
        self._box = box or {"x": 100.0, "y": 200.0, "width": 600.0, "height": 300.0}

    def bounding_box(self):
        return self._box


class _FakePage:
    """Records screenshot calls and writes real files (tiny PNG header)."""

    def __init__(self, with_form=True, fail=False):
        self.calls: list[dict] = []
        self.viewport_size = {"width": 1280, "height": 800}
        self._form = _FakeElement() if with_form else None
        self._fail = fail

    def query_selector(self, sel):
        if sel == "form":
            return self._form
        return None

    def get_by_text(self, text, exact=False):
        return _FakeElement()

    def screenshot(self, path, clip=None, full_page=False):
        if self._fail:
            raise RuntimeError("browser gone")
        self.calls.append({"path": str(path), "clip": clip, "full_page": full_page})
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")


def test_capture_writes_area_page_and_full(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBBOTS_UNHANDLED_Q_DIR", str(tmp_path))
    page = _FakePage()
    artifacts = capture_unhandled_question(
        page, portal="indeed_it", job_id="jk123", reason="failed_at_questions"
    )
    assert set(artifacts) == {"area", "page", "full"}
    for path in artifacts.values():
        assert Path(path).is_file()
        assert f"{tmp_path}" in path
    assert "indeed_it_jk123" in Path(artifacts["area"]).name
    # the area call was clipped to the form's bounding box (padded)
    area_call = next(c for c in page.calls if c["clip"] is not None)
    assert area_call["clip"]["x"] == 76.0  # 100 - 24 padding
    assert area_call["clip"]["y"] == 176.0


def test_capture_without_form_falls_back_to_viewport_and_full(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBBOTS_UNHANDLED_Q_DIR", str(tmp_path))
    page = _FakePage(with_form=False)
    page.get_by_text = None  # no text lookup either
    artifacts = capture_unhandled_question(page, portal="glassdoor_it", job_id="gd9", reason="x")
    assert "area" not in artifacts
    assert set(artifacts) == {"page", "full"}


def test_capture_never_raises_and_returns_empty_on_total_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBBOTS_UNHANDLED_Q_DIR", str(tmp_path))
    page = _FakePage(fail=True)
    artifacts = capture_unhandled_question(page, portal="greenhouse", job_id="1", reason="x")
    assert artifacts == {}


def test_output_dir_defaults_under_monorepo_outputs(monkeypatch):
    monkeypatch.delenv("JOBBOTS_UNHANDLED_Q_DIR", raising=False)
    base = unhandled_questions_dir()
    assert base.name == "unhandled_questions"
    assert base.parent.name == "outputs"


def test_safe_slug():
    assert _safe("indeed_it") == "indeed_it"
    assert _safe("a b/c:d") == "a_b_c_d"
    assert _safe("") == ""


def test_all_portal_drop_points_are_wired():
    """Source guards: every portal's question-failure drop captures first."""
    smartapply = (_REPO / "jobbots/core/shared_modules/indeed/smartapply.py").read_text()
    assert "capture_unhandled_question(" in smartapply
    assert 'reason="failed_at_questions"' in smartapply

    engine = (_REPO / "jobbots/core/ats/engine.py").read_text()
    assert engine.count("capture_unhandled_question(") >= 2  # both terminal drops

    for tree in ("gen_indeed", "it_indeed cwgeopy"):
        p = _REPO / f"master/{tree}/Auto_indeed/modules/glassdoor/loop.py"
        if p.is_file():
            loop = p.read_text()
            assert "capture_unhandled_question(" in loop, tree

    runner_p = _REPO / "legacy/linkedin-ai-auto-apply-source/hybrid_runner.js"
    if runner_p.is_file():
        runner = runner_p.read_text()
        assert "async function captureUnresolvedQuestionScreenshot" in runner
        assert runner.count("captureUnresolvedQuestionScreenshot({") >= 2  # both unresolved sites
        assert "unhandled_questions" in runner


def test_capture_signature_matches_all_call_sites():
    """Every call site passes only kwargs the helper accepts."""
    import inspect
    import re

    sig = inspect.signature(capture_unhandled_question)
    accepted = {"portal", "job_id", "question", "reason", "element"}
    assert set(sig.parameters) == {"page", *accepted}

    for rel in (
        "jobbots/core/shared_modules/indeed/smartapply.py",
        "jobbots/core/ats/engine.py",
        "master/gen_indeed/Auto_indeed/modules/glassdoor/loop.py",
        "master/it_indeed cwgeopy/Auto_indeed/modules/glassdoor/loop.py",
    ):
        p = _REPO / rel
        if not p.is_file():
            continue
        text = p.read_text()
        for call in re.findall(r"capture_unhandled_question\(([^)]*)\)", text, re.S):
            for kw in re.findall(r"(\w+)\s*=", call):
                assert kw in accepted, f"{rel}: unexpected kwarg {kw}"
