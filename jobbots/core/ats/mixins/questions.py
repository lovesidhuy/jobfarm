"""Question answering mixin — wraps the shared form_answers brain.

Resolution order:
  1. Hard policy (identity / eligibility locks — never AI)
  2. Curated QA bank
  3. Deterministic profile/safe rules
  4. DeepSeek AI (fallback only, within budget)

Also handles DOM-aware helpers: combobox typeahead, option mapping,
checkbox groups, radio groups, location autocomplete.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any


# ── module-level state ────────────────────────────────────────────────

_AI_CALLS_USED = 0
_ANSWER_LOG_PATH: Path | None = None


def _reset_ai_budget() -> None:
    global _AI_CALLS_USED
    _AI_CALLS_USED = 0


def _ai_calls_max() -> int:
    try:
        return max(0, int(os.getenv("ATS_AI_MAX_CALLS", "12") or "12"))
    except Exception:
        return 12


def _get_ai_calls_used() -> int:
    return _AI_CALLS_USED


def _answer_log_path() -> Path:
    raw = (os.getenv("ATS_ANSWER_LOG") or "").strip()
    if raw:
        return Path(raw)
    root = _MONOREPO_ROOT
    for candidate in (
        root / "logs" / "ats_it" / "answers.jsonl",
        Path.cwd() / "logs" / "ats_it" / "answers.jsonl",
    ):
        return candidate
    return Path("logs/ats_it/answers.jsonl")


def _log_answer_event(
    *,
    question: str,
    options: list[str] | None,
    value: str | None,
    source: str,
    required: bool = False,
    filled: bool = False,
    portal: str = "",
    url: str = "",
    score: float | None = None,
) -> None:
    """One JSONL line per field for bank growth / training harvest."""
    global _ANSWER_LOG_PATH
    try:
        if _ANSWER_LOG_PATH is None:
            _ANSWER_LOG_PATH = _answer_log_path()
        path = _ANSWER_LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "portal": portal or "",
            "url": (url or "")[:300],
            "question": (question or "")[:420],
            "options": [str(o)[:120] for o in (options or [])[:40]],
            "value": (value or "")[:500] if value is not None else "",
            "source": source or "missed",
            "required": bool(required),
            "filled": bool(filled),
        }
        if score is not None:
            payload["score"] = score
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        try:
            from jobbots.core.training_capture import record_training_event
            record_training_event(
                "question_answered" if filled else "question_unresolved",
                portal=portal, job_url=url, question=question, options=options or [],
                answer=value or "", answer_source=source, required=required,
                filled=filled, score=score,
            )
        except Exception:
            pass
    except Exception:
        pass


def _clean_question_text(text: str) -> str:
    """Strip CSS noise and option labels from question text."""
    if not text:
        return ""
    t = text.strip()
    # Remove CSS class names that leak into extracted text
    if re.fullmatch(r"[a-z_-]+(\s+[a-z_-]+)*", t, re.I) and len(t) < 60:
        # Likely a class name
        if any(k in t.lower() for k in ("input", "select", "remix", "css", "card", "field", "required")):
            return ""
    # Remove trailing option labels (Yes/No etc.)
    lines = t.split("\n")
    cleaned = []
    for line in lines:
        l = line.strip()
        if l.lower() in {"yes", "no", "select...", "select", "please select",
                          "i am not a veteran", "decline to self-identify",
                          "true", "false"}:
            continue
        cleaned.append(l)
    result = " ".join(cleaned).strip()
    # Remove trailing * (required marker)
    result = re.sub(r"\s*\*\s*$", "", result)
    # Remove "Required" suffix
    result = re.sub(r"\s+Required\s*$", "", result, flags=re.I)
    return result.strip()


def _should_use_ai(question: str, options: list[str] | None, required: bool = False) -> bool:
    """Decide whether AI fallback is appropriate for this question."""
    global _AI_CALLS_USED
    if _AI_CALLS_USED >= _ai_calls_max():
        return False
    q = _clean_question_text(question) or (question or "").strip()
    if not q:
        return False
    low = q.lower()
    # Identity/contact fields are policy/profile — no AI.
    if any(k in low for k in (
        "first name", "last name", "full name", "email", "phone",
        "province", "state", "postal", "zip", "linkedin", "resume", "attach",
        "iti__", "search-input", "remix-css", "requiredinput", "card-field-input",
        "0 results available", "use up and down",
        "gender", "sex", "sexe",
    )):
        return False
    if low in {"country", "country code", "phone country"}:
        return False
    if low in {"city", "location"} and not options:
        return False
    if required:
        return True
    if options and len(options) >= 2:
        return True
    if len(q) >= 8 or "?" in q:
        return True
    return False


def _ats_ai_hint(question: str, options: list[str] | None,
                 *, section_text: str = "") -> str:
    """Build a structured hint for the AI resolver."""
    parts = []
    if question:
        parts.append(f"Question: {question}")
    if options:
        parts.append(
            "Choose exactly one of these DOM option labels: "
            + ", ".join(f"'{o}'" for o in options[:20])
            + ". Return only the option label, nothing else."
        )
    if section_text:
        parts.append(f"DOM context: {section_text[:520]}")
    if not parts:
        parts.append("Answer only the value that should be entered into this one field.")
    return "\n".join(parts)


# ── question classification helpers ───────────────────────────────────

_US_WORK_AUTH_RE = re.compile(
    r"(?:legally|lawfully|authorized|eligible|entitled|right|permission|allowed)"
    r".{0,90}(?:united\s+states|u\.s\.?|usa|us)"
    r"|(?:united\s+states|u\.s\.?|usa|us)"
    r".{0,90}(?:legally|lawfully|authorized|eligible|entitled|right|permission|allowed|work)",
    re.IGNORECASE | re.DOTALL,
)

_COOP_ELIGIBILITY_RE = re.compile(
    r"(?:currently\s+enrolled|eligible\s+to\s+complete|approved\s+co-?op\s+work\s+term|"
    r"full\s+8\s*-?\s*month\s+(?:co-?op\s+)?term|aug(?:ust)?\s+2026.{0,30}apr(?:il)?\s+2027)",
    re.IGNORECASE | re.DOTALL,
)


def _contains_us_work_auth_question(text: str) -> bool:
    """Return true for US-specific work authorization prompts."""
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        return False
    return bool(_US_WORK_AUTH_RE.search(value))


def _contains_unverified_coop_requirement(text: str) -> bool:
    """The current profile does not verify an eligible co-op work term."""
    value = re.sub(r"\s+", " ", text or "").strip()
    return bool(_COOP_ELIGIBILITY_RE.search(value))


def _format_required_fail_reason(missing: list[str]) -> str:
    """Format a human-readable reason for unresolved required fields."""
    labels: list[str] = []
    seen: set[str] = set()
    for m in missing:
        lab = _clean_question_text(m) or re.sub(r"\s+", " ", (m or "").strip())
        lab = lab[:80] if lab else "unknown_field"
        key = lab.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(lab)
    joined = ", ".join(labels) if labels else "unknown"
    return f"required_fields_unanswered: {joined}"


# ── module-level brain resolution (shared by mixin + facade) ─────────

def _resolve_for_field(
    question: str,
    *,
    profile: dict[str, Any],
    options: list[str] | None = None,
    job_context: str = "",
    hint: str = "",
    required: bool = False,
    portal: str = "",
    url: str = "",
) -> list[str] | None:
    """Use shared Indeed IT brain (policy + bank + DeepSeek) for this field.

    Returns ordered preference list, or None if no answer.
    """
    global _AI_CALLS_USED
    try:
        from jobbots.core.shared_modules.form_answers import resolve_answer
    except Exception:
        return None

    clean_q = _clean_question_text(question) or (question or "").strip()
    opt_list = [str(o).strip() for o in (options or []) if str(o).strip()]
    use_ai = _should_use_ai(clean_q, opt_list or None, required=required)
    resolved_hint = hint or _ats_ai_hint(clean_q, opt_list or None)

    ans = resolve_answer(
        clean_q,
        hint=resolved_hint,
        profile=profile,
        options=opt_list or None,
        job_context=job_context,
        allow_ai=use_ai,
    )
    source = getattr(ans, "source", "") if ans else "missed"
    score = float(getattr(ans, "score", 0.0) or 0.0) if ans else 0.0
    value = (ans.value if ans and ans.value else "") or ""

    if use_ai and ans and source.startswith("deepseek"):
        _AI_CALLS_USED += 1

    _log_answer_event(
        question=clean_q,
        options=opt_list or None,
        value=value or None,
        source=source or "missed",
        required=required,
        filled=bool(value),
        portal=portal,
        url=url,
        score=score if value else None,
    )
    if not ans or not ans.value:
        return None

    # Brain value first.  Only add DOM aliases (same semantic value, different
    # casing / phone-code form) — never prepend alternate answer choices.
    prefs = [ans.value]
    v = ans.value.strip()
    if v.lower() in {"yes", "no"}:
        prefs.extend([v.capitalize(), v.upper(), v.lower()])
    if v.lower() == "canada":
        prefs.extend(["Canada +1", "Canada+1", "CA", "CAN"])
    if v.lower() in {"bc", "british columbia"}:
        prefs.extend(["British Columbia", "BC"])
    digits = re.sub(r"[^\d]", "", v)
    if digits and any(k in clean_q.lower() for k in ("salary", "compensation", "pay expectation", "base salary")):
        prefs.extend([f"${digits}", digits, f"{int(digits):,}" if digits.isdigit() else digits])
    # de-dupe
    out, seen = [], set()
    for p in prefs:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _map_pref_to_option(pref: str, options: list[str]) -> str | None:
    """Map a brain answer onto a real visible option label."""
    if not pref or not options:
        return None
    try:
        from jobbots.core.shared_modules.form_answers import _map_to_options
        mapped = _map_to_options(pref, options)
        if mapped:
            return mapped
    except Exception:
        pass
    pl = pref.lower().strip()
    # Word-boundary match to avoid 'male' matching 'female'.
    for opt in options:
        ol = opt.lower()
        if pl in {"male", "man", "m"} and ("female" in ol or "woman" in ol or "femme" in ol):
            continue
        if pl in {"male", "female"} and "transgender" in ol:
            continue
        if pl == ol:
            return opt
        if len(pl) >= 3:
            pat = r'(?<![a-zA-Z])' + re.escape(pl) + r'(?![a-zA-Z])'
            if re.search(pat, ol):
                if pl == "male" and ("female" in ol or "woman" in ol):
                    continue
                return opt
            if 3 <= len(pl) <= 5 and pl not in {"male", "man", "men"}:
                if re.match(r'(?<![a-zA-Z])' + re.escape(pl), ol):
                    return opt
        if ' ' in pl:
            tokens = [t for t in pl.split() if len(t) >= 2]
            if tokens and all(re.search(r'(?<![a-zA-Z])' + re.escape(t) + r'(?![a-zA-Z])', ol) for t in tokens):
                return opt
    if "canada" in pl:
        for opt in options:
            if opt.lower().startswith("canada"):
                return opt
    if any(k in pl for k in ("job board", "indeed", "glassdoor", "job search")):
        for opt in options:
            if any(k in opt.lower() for k in ("job search", "indeed", "glassdoor", "job board")):
                return opt
    if "linkedin" in pl or "social" in pl:
        for opt in options:
            if "social" in opt.lower() or "linkedin" in opt.lower():
                return opt
    if "other" in pl:
        for opt in options:
            if opt.lower() == "other":
                return opt
    digits = re.sub(r"[^\d]", "", pref)
    if digits:
        try:
            n = int(digits)
            for opt in options:
                m = re.findall(r"\$?([\d,]+)", opt)
                if not m:
                    continue
                nums = [int(x.replace(",", "")) for x in m]
                if len(nums) >= 2 and nums[0] <= n <= nums[1]:
                    return opt
                if len(nums) == 1 and "+" in opt and n >= nums[0]:
                    return opt
            best, best_dist = None, 10**12
            for opt in options:
                m = re.findall(r"\$?([\d,]+)", opt)
                if len(m) >= 2:
                    lo = int(m[0].replace(",", ""))
                    hi = int(m[1].replace(",", ""))
                    mid = (lo + hi) // 2
                    dist = abs(mid - n)
                    if dist < best_dist:
                        best, best_dist = opt, dist
            if best:
                return best
        except Exception:
            pass
    if pl in {"yes", "no"}:
        for opt in options:
            if opt.lower() == pl:
                return opt
    opt_set = {o.lower() for o in options}
    if opt_set <= {"yes", "no"} or opt_set == {"yes", "no"}:
        if any(k in pl for k in ("yes", "true", "y", "i do", "i am", "have")) and "no" not in pl.split():
            for opt in options:
                if opt.lower() == "yes":
                    return opt
        if any(k in pl for k in ("no", "false", "n", "do not", "don't")):
            for opt in options:
                if opt.lower() == "no":
                    return opt
    return None


class QuestionsMixin:
    """Mixin providing question-answering via the shared form_answers brain."""

    page: Any  # Playwright Page — set by adapter
    platform_name: str = ""

    def _log(self, msg: str) -> None:
        try:
            from jobbots.core.utils import print_lg  # type: ignore
            print_lg(msg)
        except Exception:
            print(msg)

    # ── answer resolution ─────────────────────────────────────────────

    def _resolve_for_field(
        self,
        question: str,
        *,
        profile: dict[str, Any],
        options: list[str] | None = None,
        job_context: str = "",
        hint: str = "",
        required: bool = False,
    ) -> list[str] | None:
        """Use shared Indeed IT brain (policy + bank + DeepSeek) for this field.

        Returns ordered preference list, or None if no answer.
        """
        url = ""
        try:
            url = getattr(self.page, "url", "") or ""
        except Exception:
            pass
        clean_q = _clean_question_text(question) or (question or "").strip()
        opt_list = [str(o).strip() for o in (options or []) if str(o).strip()]
        prefs = _resolve_for_field(
            question,
            profile=profile,
            options=options,
            job_context=job_context,
            hint=hint,
            required=required,
            portal=self.platform_name,
            url=url,
        )
        self._log(
            f"resolve q={clean_q[:80]!r} opts={len(opt_list)} "
            f"→ {(prefs[0][:80] if prefs else 'missed')!r}"
        )
        return prefs

    # ── option mapping ────────────────────────────────────────────────

    @staticmethod
    def _map_pref_to_option(pref: str, options: list[str]) -> str | None:
        """Map a brain answer onto a real visible option label."""
        return _map_pref_to_option(pref, options)

    @staticmethod
    def _filter_combobox_options(labels: list[str], *, keep_phone_codes: bool = False) -> list[str]:
        """Drop phone-country-code noise unless this field is a country/phone selector."""
        out = []
        for o in labels or []:
            t = (o or "").strip()
            if not t:
                continue
            low = t.lower()
            if low in {"select...", "select", "please select"}:
                continue
            is_phone_code = bool(re.search(r"\+\d{1,4}\s*$", t) or re.search(r"\+\d{1,4}$", t.replace(" ", "")))
            if is_phone_code and not keep_phone_codes:
                continue
            out.append(t)
        seen, clean = set(), []
        for o in out:
            k = o.lower()
            if k in seen:
                continue
            seen.add(k)
            clean.append(o)
        return clean

    # ── DOM helpers ───────────────────────────────────────────────────

    @staticmethod
    def _safe_click(el: Any, *, force: bool = True) -> bool:
        if not el:
            return False
        try:
            el.click(force=force, timeout=4000)
            return True
        except Exception:
            try:
                el.evaluate("n => n.click()")
                return True
            except Exception:
                return False

    @staticmethod
    def _fill_input(el: Any, value: str) -> bool:
        if value is None or value == "":
            return False
        try:
            typ = (el.get_attribute("type") or "").lower()
            name = (el.get_attribute("name") or "").lower()
            id_val = (el.get_attribute("id") or "").lower()
            placeholder = (el.get_attribute("placeholder") or "").lower()
            val_str = str(value).strip().lower()
            is_url_field = typ == "url" or any(k in name or k in id_val for k in ("url", "website", "portfolio", "github", "linkedin", "twitter"))
            if is_url_field and val_str in ("no", "n/a", "none", "not applicable", "no link", "n.a.", "missed"):
                return False

            el_text = ""
            try:
                el_text = (el.evaluate("""(node) => {
                    const label = node.id ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`) : null;
                    if (label) return label.innerText;
                    const card = node.closest('.application-field, .form-group, li, [class*="card" i], div');
                    return card ? card.innerText : '';
                }""") or "").lower()
            except Exception:
                pass

            is_location = any(k in name or k in id_val or k in placeholder or k in el_text for k in ("location", "city", "address"))
            if is_location and not is_url_field:
                val_str = str(value).strip()
                city = val_str.split(",")[0].strip() if "," in val_str else val_str
                if not city or len(city) < 2:
                    city = "Surrey"
                try:
                    el.click(force=True)
                except Exception:
                    pass
                el.fill("")
                el.type(city, delay=50)
                time.sleep(1.2)
                try:
                    clicked = el.evaluate("""(node, query) => {
                        const items = Array.from(document.querySelectorAll('li, div, a, span, p'));
                        for (const item of items) {
                            const txt = (item.innerText || item.textContent || '').trim();
                            if (txt.includes('Surrey, BC, CAN') || (txt.includes('Surrey') && txt.includes('CAN'))) {
                                item.scrollIntoView?.();
                                item.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                                item.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                                item.click();
                                return true;
                            }
                        }
                        return false;
                    }""", city)
                    if not clicked:
                        el.press("ArrowDown")
                        time.sleep(0.2)
                        el.press("Enter")
                    time.sleep(0.4)
                except Exception:
                    pass
                return True
        except Exception:
            pass
        try:
            el.fill("")
            el.fill(str(value))
            return True
        except Exception:
            try:
                el.evaluate(
                    """(node, val) => {
                        node.focus();
                        node.value = val;
                        node.dispatchEvent(new Event('input', {bubbles:true}));
                        node.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    str(value),
                )
                return True
            except Exception:
                return False

    @staticmethod
    def _visible(el: Any) -> bool:
        try:
            return bool(el and el.is_visible())
        except Exception:
            return False

    @staticmethod
    def _is_combobox(el: Any) -> bool:
        try:
            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            return role == "combobox" or "select__input" in cls or "react-select" in cls
        except Exception:
            return False

    def _field_blob(self, el: Any) -> str:
        """Attribute soup for routing."""
        parts = [
            el.get_attribute("name") or "",
            el.get_attribute("id") or "",
            el.get_attribute("placeholder") or "",
            el.get_attribute("aria-label") or "",
            el.get_attribute("autocomplete") or "",
            el.get_attribute("role") or "",
            el.get_attribute("class") or "",
        ]
        return " ".join(p.strip() for p in parts if p.strip()).lower()

    def _label_text_for(self, el: Any) -> str:
        """Extract label text for a form element."""
        try:
            return (el.evaluate(
                r"""(node) => {
                    const id = node.id;
                    if (id) {
                      const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                      if (lab) return (lab.innerText || lab.textContent || '').trim();
                    }
                    const aria = node.getAttribute('aria-label');
                    if (aria) return aria.trim();
                    const wrap = node.closest(
                      'label, .field, .application-field, .form-group, .select, li'
                    );
                    if (wrap) {
                      const txt = (wrap.innerText || wrap.textContent || '').trim().slice(0, 300);
                      if (txt && txt.split(/\s+/).length > 1) return txt;
                    }
                    let parent = node.parentElement;
                    let depth = 0;
                    while (parent && depth < 5) {
                      const sib = parent.previousElementSibling;
                      if (sib) {
                        const t = (sib.innerText || sib.textContent || '').trim();
                        if (t.length > 10 && t.length < 400) return t.slice(0, 300);
                      }
                      parent = parent.parentElement;
                      depth++;
                    }
                    return '';
                }"""
            ) or "").strip()
        except Exception:
            return ""

    def _question_text_from_group(self, el: Any, option_labels: list[str] | None = None) -> str:
        """Extract a radio/checkbox group's question from its DOM container.

        A label associated with an individual radio normally identifies only
        the option ("Yes", "2027", "Bachelor's").  ATS forms commonly keep
        the actual question on a surrounding fieldset/card.  Return that
        heading when it is meaningfully different from the supplied options;
        callers can safely fall back to their existing label/name behavior.
        """
        try:
            text = el.evaluate(r"""node => {
                const root = node.closest(
                    '[data-field-entry-id], fieldset, [role="radiogroup"], [role="group"], '
                    + '.application-field, .form-group, .field, .question, li'
                );
                if (!root) return '';
                const labelledBy = root.getAttribute('aria-labelledby');
                const labelled = labelledBy
                    ? document.getElementById(labelledBy.split(/\s+/)[0])
                    : null;
                const heading = labelled || root.querySelector(
                    'legend, [class*="question" i], [class*="heading" i], '
                    + '[class*="title" i], label:not([for])'
                );
                return (heading?.innerText || heading?.textContent || '').trim().slice(0, 1800);
            }""") or ""
            result = str(text).strip()
            normalized_options = {str(x).strip().lower() for x in (option_labels or []) if str(x).strip()}
            if result and result.lower() not in normalized_options:
                return result
        except Exception:
            pass
        return ""

    def _visible_question_text(self, el: Any) -> str:
        """Get question text from the element's label / wrapper."""
        blob = self._field_blob(el)
        label = self._label_text_for(el)
        return label or blob

    def _format_required_fail_reason(self, missing: list[str]) -> str:
        """Format a human-readable reason for unresolved required fields."""
        return _format_required_fail_reason(missing)
