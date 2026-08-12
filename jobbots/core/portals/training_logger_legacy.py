from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from config.settings import logs_folder_path
except ImportError:
    logs_folder_path = "logs"

try:
    from config.settings import indeed_training_logging  # shared flag; IT bot uses same setting
except ImportError:
    indeed_training_logging = True

_LOCK = threading.Lock()
_BOT_NAME = (os.environ.get("BOT_NAME") or "unknown_bot").strip()
_TRAINING_LOG_PATH = Path(logs_folder_path) / f"{_BOT_NAME}_training_log.jsonl"
_MAX_STRING = 1200
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
)
_QUESTION_EVENT_TYPES = {
    "question_detected",
    "question_answered",
    "question_skipped",
    "question_answer_failed",
    "question_unresolved",
    "ai_answer",
    "ai_answer_error",
    "ai_answer_skipped",
}
_QUESTION_ONLY_LOGGING = (
    os.environ.get("TRAINING_QUESTION_ONLY", "").strip().lower()
    in {"1", "true", "yes", "on"}
)


def _enabled() -> bool:
    return str(indeed_training_logging).strip().lower() not in {"0", "false", "no", "off"}


def _should_log_event(event_type: str) -> bool:
    if not _enabled():
        return False
    if _QUESTION_ONLY_LOGGING:
        return event_type in _QUESTION_EVENT_TYPES
    return True


def _redact(value: str) -> str:
    text = value
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = _redact(value.replace("\x00", ""))
        if len(value) > _MAX_STRING:
            return value[:_MAX_STRING] + "...[truncated]"
        return value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in list(value)[:80]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(str(value))


def log_training_event(event_type: str, **payload: Any) -> None:
    """Append one structured training event as JSONL."""
    if not _should_log_event(event_type):
        return
    try:
        _TRAINING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event_type": event_type,
            **payload,
        }
        line = json.dumps(_clean(event), ensure_ascii=False, sort_keys=True)
        with _LOCK:
            with _TRAINING_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        try:
            from jobbots.core.training_capture import record_training_event
            job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
            extra = dict(payload)
            for key in ("portal", "profile", "job_id", "source_job_id", "job_url", "result_url"):
                extra.pop(key, None)
            record_training_event(
                event_type, portal=job.get("portal") or _BOT_NAME.split("_", 1)[0],
                profile=job.get("profile") or (_BOT_NAME.split("_", 1)[1] if "_" in _BOT_NAME else ""),
                job_id=job.get("job_id") or job.get("id") or "", job_url=job.get("url") or "",
                **extra,
            )
        except Exception:
            pass
    except Exception:
        pass


def element_dom_snapshot(page, element, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Capture compact DOM facts for a form element without dumping full HTML."""
    snapshot: dict[str, Any] = {}
    try:
        snapshot = element.evaluate(
            """
            el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const attrs = {};
                for (const attr of Array.from(el.attributes || [])) {
                    if ([
                        "id", "name", "type", "role", "aria-label",
                        "aria-labelledby", "aria-describedby", "placeholder",
                        "autocomplete", "data-testid", "value"
                    ].includes(attr.name)) {
                        attrs[attr.name] = attr.value;
                    }
                }
                const labels = [];
                if (el.id) {
                    for (const label of document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`)) {
                        labels.push((label.innerText || label.textContent || "").trim());
                    }
                }
                let ancestorText = "";
                let ancestorTag = "";
                let ancestorTestId = "";
                let node = el;
                for (let i = 0; i < 6 && node; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const txt = (node.innerText || node.textContent || "").replace(/\\s+/g, " ").trim();
                    if (txt && txt.length > ancestorText.length) {
                        ancestorText = txt.slice(0, 320);
                        ancestorTag = node.tagName.toLowerCase();
                        ancestorTestId = node.getAttribute("data-testid") || "";
                    }
                    if (txt && txt.length > 40) break;
                }
                return {
                    tag: el.tagName.toLowerCase(),
                    input_type: el.getAttribute("type") || "",
                    visible: Boolean(rect.width && rect.height && style.visibility !== "hidden" && style.display !== "none"),
                    disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
                    checked: Boolean(el.checked),
                    value_len: String(el.value || "").length,
                    text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 220),
                    attrs,
                    labels,
                    ancestor: {tag: ancestorTag, data_testid: ancestorTestId, text: ancestorText},
                };
            }
            """
        ) or {}
    except Exception as e:
        snapshot = {"snapshot_error": type(e).__name__}
    if extra:
        snapshot.update(extra)
    return snapshot


def page_dom_snapshot(page, limit: int = 20) -> dict[str, Any]:
    """Capture compact page-level DOM inventory useful for failed steps."""
    if _QUESTION_ONLY_LOGGING:
        return {}
    try:
        return page.evaluate(
            """
            limit => {
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return Boolean(rect.width && rect.height && style.display !== "none" && style.visibility !== "hidden");
                };
                const simple = el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute("type") || "",
                    role: el.getAttribute("role") || "",
                    name: el.getAttribute("name") || "",
                    id: el.id || "",
                    testid: el.getAttribute("data-testid") || "",
                    aria: el.getAttribute("aria-label") || "",
                    text: (el.innerText || el.textContent || el.getAttribute("placeholder") || "").replace(/\\s+/g, " ").trim().slice(0, 220),
                    disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
                });
                const controls = Array.from(document.querySelectorAll("input, textarea, select, button, [role='button'], [role='combobox'], [role='radio'], [role='checkbox']"))
                    .filter(visible)
                    .slice(0, limit)
                    .map(simple);
                const alerts = Array.from(document.querySelectorAll("[role='alert'], [aria-live='assertive'], .ia-FormErrorText, .icl-FormField-errorText"))
                    .map(el => (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim())
                    .filter(Boolean)
                    .slice(0, 10);
                return {
                    url: location.href,
                    title: document.title,
                    counts: {
                        inputs: document.querySelectorAll("input").length,
                        textareas: document.querySelectorAll("textarea").length,
                        selects: document.querySelectorAll("select").length,
                        buttons: document.querySelectorAll("button").length,
                        iframes: document.querySelectorAll("iframe").length,
                    },
                    visible_controls: controls,
                    alerts,
                    body_text_sample: (document.body?.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 900),
                };
            }
            """,
            limit,
        ) or {}
    except Exception as e:
        return {"snapshot_error": type(e).__name__}


def training_log_path() -> str:
    return os.fspath(_TRAINING_LOG_PATH)
