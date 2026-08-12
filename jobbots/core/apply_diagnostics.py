"""Capture-on-drop diagnostics (all portals).

When an application is about to be dropped because its questions could not be
handled, capture visual evidence **before** the drop:

* ``*_area.png`` — clipped to the unresolved question's element when known,
  else the question text location, else the visible form/dialog region;
* ``*_page.png`` — the current viewport (what an operator would see);
* ``*_full.png`` — full-page capture (best effort).

Canonical location: ``automation_monorepo/outputs/unhandled_questions/``
(override with ``JOBBOTS_UNHANDLED_Q_DIR``). One training event per capture.

Hard rules: never raises, never changes the drop decision, never touches the
Q&A chain. Job Bank has no browser form (email-based applications) — its
unresolved screening questions are already captured in the answer log, which
is why nothing browser-side is wired for it.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jobbots.paths import MONOREPO_ROOT

_log = logging.getLogger("apply_diagnostics")

_DIR_ENV = "JOBBOTS_UNHANDLED_Q_DIR"

#: Selectors tried (in order) when no element/question hint is available —
#: the visible form or dialog region is the "area" that matters.
_AREA_FALLBACK_SELECTORS = (
    "form",
    "[role='dialog']",
    "div[class*='modal']",
    "div[class*='form']",
    "main",
)


def unhandled_questions_dir() -> Path:
    override = (os.getenv(_DIR_ENV) or "").strip()
    base = Path(override) if override else MONOREPO_ROOT / "outputs" / "unhandled_questions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_")
    return slug[:limit]


def _clip_for(page: Any, *, question: str = "", element: Any = None) -> dict | None:
    """Best-effort bounding box for the question area. None when unresolvable."""
    target = element
    if target is None and question:
        try:
            loc = page.get_by_text(question[:80], exact=False)
            target = loc.first if hasattr(loc, "first") else loc
        except Exception:
            target = None
    if target is None:
        for sel in _AREA_FALLBACK_SELECTORS:
            try:
                target = page.query_selector(sel)
                if target:
                    break
            except Exception:
                continue
    if target is None:
        return None
    try:
        box = target.bounding_box()
    except Exception:
        return None
    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
        return None
    pad = 24
    try:
        vp = page.viewport_size or {}
    except Exception:
        vp = {}
    max_w = float(vp.get("width") or 1920)
    max_h = float(vp.get("height") or 4000)
    x = max(0.0, float(box["x"]) - pad)
    y = max(0.0, float(box["y"]) - pad)
    return {
        "x": x,
        "y": y,
        "width": min(float(box["width"]) + 2 * pad, max_w - x),
        "height": min(float(box["height"]) + 2 * pad, max_h - y),
    }


def capture_unhandled_question(
    page: Any,
    *,
    portal: str,
    job_id: str = "",
    question: str = "",
    reason: str = "",
    element: Any = None,
) -> dict[str, str]:
    """Capture the question area + page before a drop. Never raises.

    Returns a dict of the artifacts written (``area``/``page``/``full`` keys
    mapping to absolute paths); empty when capture was impossible.
    """
    artifacts: dict[str, str] = {}
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = unhandled_questions_dir() / (
            f"{_safe(portal, 24) or 'portal'}_{_safe(job_id, 40) or 'job'}_{stamp}"
        )
        clip = _clip_for(page, question=question, element=element)

        if clip:
            try:
                area = f"{base}_area.png"
                page.screenshot(path=area, clip=clip)
                artifacts["area"] = area
            except Exception as exc:
                _log.debug("area capture failed: %s", exc)
        try:
            viewport = f"{base}_page.png"
            page.screenshot(path=viewport)
            artifacts["page"] = viewport
        except Exception as exc:
            _log.debug("viewport capture failed: %s", exc)
        try:
            full = f"{base}_full.png"
            page.screenshot(path=full, full_page=True)
            artifacts["full"] = full
        except Exception as exc:
            _log.debug("full-page capture failed: %s", exc)

        _log.info(
            "unhandled-question capture [%s] job=%s reason=%s -> %s",
            portal, job_id or "?", reason or "?", sorted(artifacts),
        )
        try:
            from jobbots.core.training_capture import record_training_event

            record_training_event(
                "unhandled_question_capture",
                portal=portal,
                job_id=job_id,
                question=(question or "")[:420],
                reason=reason or "",
                artifacts=sorted(artifacts),
            )
        except Exception:
            pass
    except Exception as exc:  # diagnostics must never break the apply path
        _log.debug("capture_unhandled_question suppressed: %s", exc)
    return artifacts
