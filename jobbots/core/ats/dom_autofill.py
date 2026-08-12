"""Native In-DOM Form Autofill Engine for ATS Application Pages.

Extracts in-DOM interactive fields, resolves answers dynamically via candidate
profile, QA answer bank, and LLM gateway, and dispatches native/synthetic React & Vue events.

Zero external server dependency, zero extension requirement, 100% headless/VM compatible.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_log = logging.getLogger("jobbots.core.ats.dom_autofill")

# ── In-DOM Field Scanner JavaScript ──────────────────────────────────────────
# Extracts all visible interactive fields with accurate label text, options, and types,
# tagging each element with a unique `data-ats-field-id` for reliable subsequent injection.

SCAN_FIELDS_JS = r"""
(() => {
    let fieldCounter = 0;
    const elements = [];

    function cleanText(txt) {
        return (txt || '').replace(/\s+/g, ' ').replace(/[✱*:]+$/, '').trim();
    }

    function isVisible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const isFormField = ['input', 'select', 'textarea'].includes(el.tagName.toLowerCase());
        if (!isFormField && style.opacity === '0') return false;
        const rect = el.getBoundingClientRect();
        if (isFormField) return true;
        return rect.width > 0 && rect.height > 0;
    }

    function findLabelForElement(el) {
        const root = el.getRootNode ? el.getRootNode() : document;
        // 1. Direct label[for="id"]
        const id = el.getAttribute('id');
        if (id) {
            try {
                const l = root.querySelector(`label[for="${CSS.escape(id)}"]`) || document.querySelector(`label[for="${CSS.escape(id)}"]`);
                if (l && isVisible(l)) return cleanText(l.innerText || l.textContent);
            } catch (e) {}
        }

        // 2. Enclosing <label>
        const parentLabel = el.closest('label');
        if (parentLabel) {
            // Clone and remove inputs/selects to avoid grabbing child control text
            const clone = parentLabel.cloneNode(true);
            clone.querySelectorAll('input, select, textarea, button').forEach(n => n.remove());
            const txt = cleanText(clone.innerText || clone.textContent);
            if (txt) return txt;
        }

        // 3. aria-label / aria-labelledby
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel) return cleanText(ariaLabel);
        const ariaLabelledby = el.getAttribute('aria-labelledby');
        if (ariaLabelledby) {
            const targets = ariaLabelledby.split(/\s+/).map(i => {
                try { return root.querySelector(`#${CSS.escape(i)}`) || document.getElementById(i); } catch (e) { return null; }
            }).filter(Boolean);
            const txt = targets.map(t => cleanText(t.innerText || t.textContent)).join(' ');
            if (txt) return txt;
        }

        // `labels` covers implicit and explicit native label relationships.
        try {
            if (el.labels && el.labels.length) {
                const txt = Array.from(el.labels).map(l => cleanText(l.innerText || l.textContent)).filter(Boolean).join(' ');
                if (txt) return txt;
            }
        } catch (e) {}

        // 4. Closest preceding question container / heading / legend / card-field
        const container = el.closest('.application-question, .form-group, .question, .card, [data-qa], .input-container, .field, fieldset, li, tr, div');
        if (container) {
            const legend = container.querySelector('.application-label, .text, legend, .legend, .label, .field-label, .question-label, .card-field-label, h3, h4, h5, strong, b');
            if (legend && isVisible(legend)) {
                const txt = cleanText(legend.innerText || legend.textContent);
                if (txt && !txt.toLowerCase().includes('type your response') && !txt.toLowerCase().startsWith('cards[')) return txt;
            }
        }

        // 5. Check previous sibling or parent element text
        let prev = el.previousElementSibling;
        while (prev) {
            if (isVisible(prev)) {
                const txt = cleanText(prev.innerText || prev.textContent);
                if (txt && txt.length < 250) return txt;
            }
            prev = prev.previousElementSibling;
        }

        // 6. Placeholder, name, or id fallback
        const ph = cleanText(el.getAttribute('placeholder') || '');
        if (ph && !ph.toLowerCase().includes('type here') && !ph.toLowerCase().includes('type your response')) return ph;
        return cleanText(el.getAttribute('name') || el.getAttribute('id') || '');
    }

    function queryAllPiercing(selector) {
        const result = [];
        function traverse(root) {
            if (!root) return;
            try {
                const matches = root.querySelectorAll(selector);
                for (const el of matches) {
                    if (!result.includes(el)) {
                        result.push(el);
                    }
                }
            } catch (e) {}
            const children = root.querySelectorAll('*');
            for (const el of children) {
                if (el.shadowRoot) {
                    traverse(el.shadowRoot);
                }
            }
        }
        traverse(document);
        return result;
    }

    // ── 1. Text, Email, Tel, Number Inputs & Textareas ──
    const textInputs = Array.from(queryAllPiercing('input, textarea')).filter(el => {
        const type = (el.getAttribute('type') || 'text').toLowerCase();
        if (['hidden', 'file', 'submit', 'button', 'reset', 'image', 'radio', 'checkbox'].includes(type)) return false;
        return isVisible(el);
    });

    for (const el of textInputs) {
        const label = findLabelForElement(el);
        if (!label) continue;
        const fieldId = `ats_field_${++fieldCounter}`;
        el.setAttribute('data-ats-field-id', fieldId);
        elements.push({
            id: fieldId,
            tag: el.tagName.toLowerCase(),
            type: (el.getAttribute('type') || 'text').toLowerCase(),
            label: label,
            name: el.getAttribute('name') || '',
            current_value: el.value || '',
            required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true'
        });
    }

    // ── 2. Native Select Menus ──
    const selectElements = Array.from(queryAllPiercing('select')).filter(isVisible);
    for (const el of selectElements) {
        const label = findLabelForElement(el);
        if (!label) continue;
        const fieldId = `ats_field_${++fieldCounter}`;
        el.setAttribute('data-ats-field-id', fieldId);
        const options = Array.from(el.options).map(o => cleanText(o.text || o.value)).filter(Boolean);
        elements.push({
            id: fieldId,
            tag: 'select',
            type: 'select',
            label: label,
            name: el.getAttribute('name') || '',
            options: options,
            current_value: el.value || '',
            required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true'
        });
    }

    // ── 3. Radio Button Groups ──
    const radioInputs = Array.from(queryAllPiercing('input[type="radio"]')).filter(isVisible);
    const radioGroups = {};
    for (const r of radioInputs) {
        const groupKey = r.getAttribute('name') || (r.closest('fieldset, .form-group, .question, .card') ? findLabelForElement(r) : 'unknown_radio_group');
        if (!radioGroups[groupKey]) radioGroups[groupKey] = [];
        radioGroups[groupKey].push(r);
    }

    for (const [groupKey, radios] of Object.entries(radioGroups)) {
        if (!radios.length) continue;
        const groupLabel = findLabelForElement(radios[0]) || groupKey;
        const fieldId = `ats_field_${++fieldCounter}`;
        const options = [];
        radios.forEach((r, idx) => {
            const optId = `${fieldId}_opt_${idx}`;
            r.setAttribute('data-ats-field-id', optId);
            const optLabel = cleanText(findLabelForElement(r) || r.value || '');
            options.push({ id: optId, label: optLabel, value: r.value || optLabel, checked: r.checked });
        });
        elements.push({
            id: fieldId,
            tag: 'radiogroup',
            type: 'radio',
            label: groupLabel,
            name: groupKey,
            options: options.map(o => o.label),
            radio_options: options,
            required: radios.some(r => r.hasAttribute('required'))
        });
    }

    // ── 4. Checkboxes (Consent / Single / Multi) ──
    const checkboxes = Array.from(queryAllPiercing('input[type="checkbox"]')).filter(isVisible);
    for (const chk of checkboxes) {
        const label = findLabelForElement(chk);
        if (!label) continue;
        const fieldId = `ats_field_${++fieldCounter}`;
        chk.setAttribute('data-ats-field-id', fieldId);
        elements.push({
            id: fieldId,
            tag: 'checkbox',
            type: 'checkbox',
            label: label,
            name: chk.getAttribute('name') || '',
            checked: chk.checked,
            required: chk.hasAttribute('required')
        });
    }

    // ── 5. ARIA Comboboxes & Custom Dropdowns (Ashby / Greenhouse / Lever) ──
    const comboboxes = Array.from(queryAllPiercing('[role="combobox"], [aria-autocomplete="list"], .select__input, .dropdown-input')).filter(isVisible);
    for (const cb of comboboxes) {
        if (cb.hasAttribute('data-ats-field-id')) continue;
        const label = findLabelForElement(cb);
        if (!label) continue;
        const fieldId = `ats_field_${++fieldCounter}`;
        cb.setAttribute('data-ats-field-id', fieldId);
        elements.push({
            id: fieldId,
            tag: cb.tagName.toLowerCase(),
            type: 'combobox',
            label: label,
            current_value: cb.value || cb.innerText || '',
            required: cb.getAttribute('aria-required') === 'true'
        });
    }

    // Rich-text answers on some custom questions are contenteditable rather than textarea.
    const editableControls = Array.from(queryAllPiercing('[contenteditable="true"][role="textbox"], [contenteditable="true"][aria-multiline="true"]')).filter(isVisible);
    for (const el of editableControls) {
        if (el.hasAttribute('data-ats-field-id')) continue;
        const label = findLabelForElement(el);
        if (!label) continue;
        const fieldId = `ats_field_${++fieldCounter}`;
        el.setAttribute('data-ats-field-id', fieldId);
        elements.push({ id: fieldId, tag: el.tagName.toLowerCase(), type: 'contenteditable', label: label,
            current_value: el.innerText || el.textContent || '', required: el.getAttribute('aria-required') === 'true' });
    }

    return elements;
})()
"""

# ── In-DOM Value Injector JavaScript ─────────────────────────────────────────
# Injects resolved values with React prototype descriptors and dispatches synthetic events.

INJECT_VALUES_JS = """
((injectionPayload) => {
    let filledCount = 0;

    function dispatchSyntheticEvents(el, val) {
        if (!el) return;
        el.focus();
        // React 16/17/18/19 Value Tracker Hook
        const tracker = el._valueTracker;
        if (tracker) {
            tracker.setValue('');
        }
        const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
        if (descriptor && descriptor.set) {
            descriptor.set.call(el, val);
        } else {
            el.value = val;
        }

        try {
            el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: String(val) }));
        } catch (e) {}
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
    }

    // document.querySelector cannot enter an open shadow root. Scan and inject
    // with the same traversal so discovered controls remain addressable.
    function findByAtsId(id) {
        function find(root) {
            try {
                const direct = root.querySelector(`[data-ats-field-id="${CSS.escape(id)}"]`);
                if (direct) return direct;
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot) {
                        const nested = find(child.shadowRoot);
                        if (nested) return nested;
                    }
                }
            } catch (e) {}
            return null;
        }
        return find(document);
    }

    for (const item of injectionPayload) {
        const { id, type, value, radio_options } = item;
        if (!value && value !== false) continue;

        if (type === 'radio' && radio_options && radio_options.length) {
            const targetVal = String(value).toLowerCase().trim();
            const matched = radio_options.find(o => o.label.toLowerCase() === targetVal || o.value.toLowerCase() === targetVal) 
                         || radio_options.find(o => o.label.toLowerCase().includes(targetVal) || targetVal.includes(o.label.toLowerCase()));
            if (matched) {
                const radioEl = findByAtsId(matched.id);
                if (radioEl) {
                    radioEl.click();
                    radioEl.dispatchEvent(new Event('change', { bubbles: true }));
                    filledCount++;
                }
            }
        } else if (type === 'checkbox') {
            const chk = findByAtsId(id);
            if (chk) {
                const shouldCheck = typeof value === 'boolean' ? value : ['yes', 'true', '1', 'agree', 'accept'].includes(String(value).toLowerCase().trim());
                if (shouldCheck && !chk.checked) {
                    chk.click();
                    chk.dispatchEvent(new Event('change', { bubbles: true }));
                    filledCount++;
                }
            }
        } else if (type === 'select') {
            const sel = findByAtsId(id);
            if (sel && sel.tagName.toLowerCase() === 'select') {
                const targetVal = String(value).toLowerCase().trim();
                let foundOpt = false;
                for (let i = 0; i < sel.options.length; i++) {
                    const opt = sel.options[i];
                    const optText = (opt.text || '').toLowerCase().trim();
                    const optVal = (opt.value || '').toLowerCase().trim();
                    if (optText === targetVal || optVal === targetVal || optText.includes(targetVal) || targetVal.includes(optText)) {
                        sel.selectedIndex = i;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                        filledCount++;
                        foundOpt = true;
                        break;
                    }
                }
                if (!foundOpt && sel.options.length > 1 && !sel.value) {
                    // Fallback to first non-empty option
                    sel.selectedIndex = 1;
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    filledCount++;
                }
            }
        } else if (type === 'combobox') {
            const cb = findByAtsId(id);
            if (cb) {
                dispatchSyntheticEvents(cb, String(value));
                filledCount++;
            }
        } else {
            // Text, textarea, email, tel, number
            const el = findByAtsId(id);
            if (el) {
                if (type === 'contenteditable') {
                    el.focus();
                    el.textContent = String(value);
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(value) }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    dispatchSyntheticEvents(el, String(value));
                }
                filledCount++;
            }
        }
    }

    return filledCount;
})
"""


@dataclass
class FillStats:
    total: int = 0
    filled: int = 0
    skipped: int = 0


class DOMAutofillEngine:
    """High-performance, in-DOM form autofiller for ATS application portals."""

    @classmethod
    def scan_page_fields(cls, page: Any) -> List[Dict[str, Any]]:
        """Extract fields from the document and accessible child frames.

        ATS identity/document-upload widgets occasionally render questions inside a
        same-origin iframe. A frame is a separate DOM, so each field carries its
        frame index and is injected back into that exact browsing context.
        """
        try:
            frames = list(getattr(page, "frames", []) or [])
            if not frames:
                frames = [page]
            elements: List[Dict[str, Any]] = []
            for frame_index, frame in enumerate(frames):
                try:
                    frame_fields = frame.evaluate(SCAN_FIELDS_JS) or []
                    for field in frame_fields:
                        field["_frame_index"] = frame_index
                    elements.extend(frame_fields)
                except Exception as exc:
                    # Cross-origin or detached frames must not prevent filling the
                    # primary application form.
                    _log.debug("Skipping inaccessible form frame %s: %s", frame_index, exc)
            return elements
        except Exception as exc:
            _log.warning(f"Error scanning DOM fields: {exc}")
            return []

    @classmethod
    def resolve_field_answers(
        cls,
        fields: List[Dict[str, Any]],
        profile: Dict[str, Any],
        job_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Resolve values for scanned fields via candidate profile, QA bank, and LLM gateway."""
        from jobbots.core.shared_modules.form_answers import resolve_answer

        # Build known profile key lookup
        first = profile.get("first_name", "")
        last = profile.get("last_name", "")
        full = profile.get("full_name") or f"{first} {last}".strip()

        known_map = {
            "first name": first,
            "last name": last,
            "full name": full,
            "name": full,
            "email": profile.get("email", ""),
            "email address": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "phone number": profile.get("phone", ""),
            "mobile phone": profile.get("phone", ""),
            "linkedin": profile.get("linkedin", ""),
            "linkedin url": profile.get("linkedin", ""),
            "linkedin profile": profile.get("linkedin", ""),
            "github": profile.get("github", ""),
            "github url": profile.get("github", ""),
            "github profile": profile.get("github", ""),
            "portfolio": profile.get("website", ""),
            "portfolio url": profile.get("website", ""),
            "website": profile.get("website", ""),
            "personal website": profile.get("website", ""),
            "city": profile.get("city", ""),
            "state": profile.get("state", ""),
            "province": profile.get("state", ""),
            "country": profile.get("country", ""),
            "postal code": profile.get("zipcode", "") or profile.get("postal_code", ""),
            "zip code": profile.get("zipcode", "") or profile.get("postal_code", ""),
            "zip": profile.get("zipcode", "") or profile.get("postal_code", ""),
            "address": profile.get("address", ""),
            "street address": profile.get("address", ""),
            "location": profile.get("location", ""),
            "current company": profile.get("current_company", ""),
            "current title": profile.get("current_title", ""),
            "recent job title": profile.get("current_title", ""),
            "recent employer": profile.get("current_company", ""),
        }

        injection_payload = []
        for f in fields:
            label = f.get("label", "").strip()
            if not label:
                continue

            # Skip if already filled and valid
            curr = f.get("current_value", "").strip()
            if curr and curr.lower() not in {"select...", "choose...", "select an option"}:
                continue

            clean_lbl = label.lower().replace("*", "").replace(":", "").strip()
            options = f.get("options")

            val = known_map.get(clean_lbl)
            if not val:
                # Query QA bank / LLM Gateway
                ans = resolve_answer(
                    question=label,
                    options=options,
                    job_context=job_context,
                )
                val = ans.value if ans else ""

            if val:
                injection_payload.append({
                    "id": f["id"],
                    "type": f.get("type", "text"),
                    "value": val,
                    "radio_options": f.get("radio_options", []),
                    "_frame_index": f.get("_frame_index", 0),
                })

        return injection_payload

    @classmethod
    def autofill(
        cls,
        page: Any,
        profile: Dict[str, Any],
        job_context: str = "",
    ) -> FillStats:
        """Scan, resolve, and inject all form field values directly in the page DOM.

        Returns FillStats(total, filled, skipped).
        """
        t0 = time.time()
        try:
            fields = cls.scan_page_fields(page)
            if not fields:
                _log.info("DOMAutofillEngine: No visible interactive fields found on page.")
                return FillStats(total=0, filled=0, skipped=0)

            _log.info(f"DOMAutofillEngine: Found {len(fields)} interactive fields on page.")
            payload = cls.resolve_field_answers(fields, profile, job_context=job_context)
            if not payload:
                _log.info("DOMAutofillEngine: No answers to inject.")
                return FillStats(total=len(fields), filled=0, skipped=len(fields))

            frames = list(getattr(page, "frames", []) or [])
            if not frames:
                frames = [page]
            filled_count = 0
            payload_by_frame: Dict[int, List[Dict[str, Any]]] = {}
            for item in payload:
                payload_by_frame.setdefault(int(item.get("_frame_index", 0)), []).append(item)
            for frame_index, frame_payload in payload_by_frame.items():
                if frame_index >= len(frames):
                    continue
                try:
                    filled_count += frames[frame_index].evaluate(INJECT_VALUES_JS, frame_payload) or 0
                except Exception as exc:
                    _log.debug("Could not inject into form frame %s: %s", frame_index, exc)

            # Fire native Playwright CDP fill on text/textarea fields to guarantee React synthetic hooks sync
            for item in payload:
                f_type = item.get("type", "text")
                f_id = item.get("id")
                f_val = item.get("value")
                frame_index = int(item.get("_frame_index", 0))
                if f_type in ("text", "textarea", "email", "tel", "number") and f_id and f_val and frame_index < len(frames):
                    try:
                        loc = frames[frame_index].locator(f"[data-ats-field-id='{f_id}']")
                        if loc.count() and loc.is_visible():
                            loc.fill(str(f_val))
                    except Exception:
                        pass

            elapsed = round(time.time() - t0, 2)
            _log.info(f"DOMAutofillEngine: Injected {filled_count}/{len(fields)} fields in {elapsed}s.")
            return FillStats(total=len(fields), filled=filled_count, skipped=len(fields) - filled_count)
        except Exception as exc:
            _log.warning(f"DOMAutofillEngine autofill error: {exc}")
            return FillStats(total=0, filled=0, skipped=0)
