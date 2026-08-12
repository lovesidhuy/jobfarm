"""Lever ATS adapter.

Handles:
  * Single ``name`` field (full name, not first/last)
  * LinkedIn "Apply with LinkedIn" iframe overlay
  * ``/apply`` path suffix for application form
  * Card-style radio buttons (labels from sibling DOM)
  * Card text inputs nested in ``cards[...]`` wrappers
  * Native selects with Select2
  * Resume upload via ``input[name='resume']``
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

from ..base import ATSAdapter
from ..mixins.upload import UploadMixin
from ..mixins.captcha import CaptchaMixin
from ..mixins.questions import QuestionsMixin, _clean_question_text, _ats_ai_hint
from ..mixins.fields import FieldsMixin
from ..mixins.verification import VerificationMixin
from ..types import FillStats


class LeverAdapter(
    UploadMixin,
    CaptchaMixin,
    QuestionsMixin,
    FieldsMixin,
    VerificationMixin,
    ATSAdapter,
):
    platform_name = "lever"

    def __init__(self) -> None:
        self.page: Any = None
        self.profile: dict[str, Any] = {}
        self.job_title = ""
        self.job_company = ""
        self.job_context = ""
        # Human-readable proof from the confirmation page (page-primary policy).
        self.confirmation_evidence = ""

    # ── detection ─────────────────────────────────────────────────────

    @classmethod
    def detect(cls, url: str) -> bool:
        if not url:
            return False
        try:
            host = (urlparse(url).hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            return bool(re.search(
                r"(?:^|\.)(?:jobs\.lever\.co|lever\.co)(?:/|$)",
                host + "/", re.I,
            ))
        except Exception:
            return False

    # ── lifecycle ─────────────────────────────────────────────────────

    def initialize(self, page: Any, profile: dict[str, Any],
                   *, job_title: str = "", job_company: str = "",
                   job_context: str = "") -> None:
        self.page = page
        self.profile = profile
        self.job_title = job_title
        self.job_company = job_company
        self.job_context = job_context

        # Navigate to /apply if not already there
        url = (self.page.url or "").lower()
        if "jobs.lever.co" in url and not url.rstrip("/").endswith("/apply"):
            try:
                self.page.goto(
                    self.page.url.rstrip("/") + "/apply",
                    wait_until="domcontentloaded", timeout=20000,
                )
                time.sleep(0.8)
            except Exception:
                pass

        self._dismiss_overlays()
        # Surface closed postings early (overnight: Jobgether / Crypto 404).
        try:
            text = (self.page.inner_text("body") or "")[:4000]
        except Exception:
            text = ""
        if re.search(
            r"couldn.?t find anything here|404 error|job posting.*(?:closed|removed)|"
            r"no longer available|position has been filled",
            text,
            re.I,
        ):
            self._log("Lever posting appears closed/404 — form will not open")

    def authenticate(self) -> bool:
        return True

    def upload_documents(self, **kwargs: Any) -> dict[str, bool]:
        resume_path = kwargs.get("resume_path") or (self.profile.get("resume_path") or "").strip()
        cover_path = kwargs.get("cover_letter_path") or (self.profile.get("cover_letter_path") or "").strip()
        return UploadMixin.upload_documents(
            self, resume_path=resume_path, cover_letter_path=cover_path
        )

    def fill_application(self) -> FillStats:
        # Lever's ``Current location`` field is not a normal text input.  Its
        # blur handler clears both the visible value and ``selectedLocation``
        # unless a .dropdown-location result has been selected.  Keep it out
        # of the shared direct-field pass, then commit a typeahead result.
        stats = self.fill_standard_fields(self.profile, skip_location_widgets=True)
        if self._fill_current_location():
            stats.filled += 1

        # Scan remaining fields
        try:
            fields = self.page.query_selector_all("input, textarea, select")
        except Exception:
            fields = []

        mapping = [
            (("first name", "firstname", "given-name", "fname"), self.profile.get("first_name", "")),
            (("last name", "lastname", "family-name", "lname", "surname"), self.profile.get("last_name", "")),
            (("email", "e-mail"), self.profile.get("email", "")),
            (("phone", "mobile", "tel"), self.profile.get("phone", "")),
            (("linkedin",), self.profile.get("linkedin", "")),
            (("website", "portfolio", "github"), self.profile.get("website", "")),
        ]

        for el in fields:
            try:
                tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
                typ = (el.get_attribute("type") or "text").lower()
                if typ in {"hidden", "submit", "button", "file", "image"}:
                    continue
                if typ in {"checkbox", "radio"}:
                    continue
                if not self._visible(el) and not self._is_combobox(el):
                    continue
                blob = self._field_blob(el)
                question = self._visible_question_text(el) or _clean_question_text(blob) or blob

                # Do not fall back to raw text for Lever's structured location
                # widget.  A raw fill looks correct briefly, but Lever clears
                # it on blur because selectedLocation was never populated.
                if self._is_lever_location_input(el, question):
                    continue

                prefs = self._resolve_for_field(
                    question,
                    profile=self.profile,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(question, None, section_text=blob[:200]),
                )
                if tag == "select":
                    try:
                        opts = el.evaluate(
                            "el => Array.from(el.options).map(o => (o.text||'').trim()).filter(Boolean)"
                        ) or []
                    except Exception:
                        opts = []
                    if opts:
                        prefs = self._resolve_for_field(
                            question,
                            profile=self.profile,
                            options=list(opts),
                            job_context=self.job_context,
                            hint=_ats_ai_hint(question, list(opts), section_text=blob[:200]),
                        ) or prefs
                    if prefs and self._select_native(el, prefs):
                        stats.filled += 1
                    continue

                value = prefs[0] if prefs else None
                if value is None:
                    for keys, val in mapping:
                        if any(k in blob for k in keys) or any(k in question.lower() for k in keys):
                            value = val
                            break
                if not value:
                    continue
                try:
                    existing = (el.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and existing.lower() not in {"", "n/a", "select..."}:
                    stats.skipped += 1
                    continue
                if self._fill_input(el, str(value)):
                    stats.filled += 1
            except Exception:
                continue

        return stats

    def _is_lever_location_input(self, el: Any, question: str = "") -> bool:
        """Return whether *el* is Lever's selectedLocation-backed widget."""
        try:
            name = (el.get_attribute("name") or "").lower()
            ident = (el.get_attribute("id") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            qa = (el.get_attribute("data-qa") or "").lower()
        except Exception:
            return False
        return (
            name == "location"
            or ident == "location-input"
            or "location-input" in cls
            or qa == "location-input"
            or "current location" in question.lower()
        )

    def _fill_current_location(self) -> bool:
        """Select a Lever location suggestion so its hidden value is bound.

        The public Lever form uses ``.dropdown-location`` entries and records
        the selected result in ``#selected-location``.  Typing a fully
        formatted profile value is not sufficient (and can yield no results),
        so search only for the city and click the Canadian suggestion.
        """
        try:
            field = self.page.query_selector(
                "input.location-input, input[data-qa='location-input'], "
                "#location-input, input[name='location']"
            )
        except Exception:
            field = None
        if not field or not self._visible(field):
            return False

        location = str(
            self.profile.get("location") or self.profile.get("city") or "Surrey"
        ).strip()
        city = location.split(",", 1)[0].strip() or "Surrey"
        try:
            field.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        if not self._safe_click(field, force=True):
            return False
        try:
            field.fill("")
            field.type(city, delay=50)
        except Exception as exc:
            self._log(f"Lever location search could not type {city!r}: {exc}")
            return False

        # retrieveLocations.js debounces its API request by 500 ms.  Prefer
        # the concrete option selector over broad text matching: the latter
        # can click the dropdown container instead of its option.
        try:
            self.page.wait_for_selector(".dropdown-location", state="visible", timeout=6000)
        except Exception:
            time.sleep(0.8)
        try:
            options = self.page.query_selector_all(".dropdown-location")
        except Exception:
            options = []
        ranked: list[tuple[int, Any, str]] = []
        for option in options:
            try:
                if not self._visible(option):
                    continue
                text = (option.inner_text() or "").strip()
            except Exception:
                continue
            normalized = text.lower()
            score = 0
            if city.lower() in normalized:
                score += 20
            if "can" in normalized or "canada" in normalized:
                score += 20
            if "bc" in normalized or "british columbia" in normalized:
                score += 15
            if city.lower() == "surrey" and "surrey, bc, can" in normalized:
                score += 100
            if any(token in normalized for token in ("united kingdom", ", uk", ", us", "usa")):
                score -= 100
            ranked.append((score, option, text))
        # Never silently select Surrey, UK (or another non-Canadian city)
        # merely because it is the only autocomplete result.  An uncommitted
        # location can be repaired or surfaced; a wrong country is worse.
        ranked = [item for item in ranked if item[0] >= 20]
        if not ranked:
            self._log(f"Lever location search found no Canadian match for {city!r}")
            return False
        _, option, text = max(ranked, key=lambda item: item[0])
        if not self._safe_click(option, force=True):
            return False
        time.sleep(0.25)
        bound = self._selected_location_value()
        if not bound:
            self._log(f"Lever location option {text!r} was clicked but did not bind")
            return False
        self._log(f"Lever location selected {text!r}")
        return True

    def _selected_location_value(self) -> str:
        """Read Lever's hidden location binding across form variants."""
        # Lever's hosted forms use both kebab-case and camelCase names. The
        # visible text input can retain typed text without a selectedLocation
        # value, so only a populated hidden/bound input counts as success.
        selectors = (
            "#selected-location",
            "#selectedLocation",
            "input[name='selectedLocation']",
            "input[name='selected_location']",
            "input[name='locationId']",
            "input[data-qa='selected-location']",
            "input[data-qa='selectedLocation']",
        )
        for selector in selectors:
            try:
                selected = self.page.query_selector(selector)
                bound = (selected.input_value() or "").strip() if selected else ""
                if bound:
                    return bound
            except Exception:
                continue
        return ""

    def answer_questions(self) -> int:
        answered = 0
        # Lever card-style radios
        answered += self._fill_lever_card_radios()
        # Lever card-style checkboxes
        answered += self._fill_lever_card_checkboxes()
        # Lever card text inputs
        answered += self._fill_lever_card_text_inputs()
        # Lever native selects (Select2)
        answered += self.fill_native_selects(
            self.profile, job_context=self.job_context, portal="lever"
        )
        # Radio groups (generic)
        answered += self._fill_radio_groups()
        # Consent checkboxes
        answered += self._fill_consent_checkboxes()
        # Free-text questions
        answered += self._fill_free_text_questions()
        return answered

    def submit(self) -> bool:
        self._dismiss_overlays()
        selectors = [
            "button#submit_app",
            "input#submit_app",
            "#btn-submit",
            "button#btn-submit",
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button:has-text('SUBMIT APPLICATION')",
            "button:has-text('Submit')",
            "input[value*='Submit' i]",
        ]
        for sel in selectors:
            try:
                el = self.page.query_selector(sel)
                if not el:
                    continue
                if self._safe_click(el, force=True):
                    time.sleep(0.8)
                    return True
            except Exception:
                continue
        return False

    def verify_submission(self) -> str | None:
        """Page-primary confirmation (email is secondary / not required).

        Lever typically redirects to ``/thanks`` or shows an application
        confirmation card. Do not treat form-gone alone as success.
        """
        from ..confirmation import classify_page_confirmation

        self.confirmation_evidence = ""
        url = self.page.url or ""
        text = self._page_text(24000) or ""
        if re.search(
            r"couldn.?t find anything here|404 error|job posting.*(?:closed|removed)|"
            r"no longer available",
            text,
            re.I,
        ):
            return None
        status, evidence = classify_page_confirmation(
            url, text, page=self.page, platform="lever",
        )
        if evidence:
            self.confirmation_evidence = evidence
        return status

    # ── Lever-specific handlers ───────────────────────────────────────

    def _fill_lever_card_radios(self) -> int:
        """Handle Lever card-style radio buttons that have no HTML labels."""
        filled = 0
        try:
            raw = self.page.evaluate('''() => {
                const getLabelText = (r) => {
                    let labelText = '';
                    try {
                        const labelEl = r.closest('label') || r.parentElement;
                        if (labelEl) {
                            labelText = (labelEl.innerText || labelEl.textContent || '').trim();
                        }
                    } catch (e) {}
                    return labelText;
                };

                const getQuestionText = (r) => {
                    const appField = r.closest('.application-field, .form-group');
                    const prev = appField ? appField.previousElementSibling : null;
                    if (prev) {
                        const t = (prev.innerText || prev.textContent || '').trim();
                        if (t && t.length > 2) return t;
                    }
                    const card = r.closest('.application-question, .custom-question, fieldset');
                    const titleEl = card ? card.querySelector('.application-label, legend, h4, h5') : null;
                    if (titleEl) {
                        const t = (titleEl.innerText || titleEl.textContent || '').trim();
                        if (t && t.length > 2) return t;
                    }
                    return '';
                };

                const sections = new Map();
                for (const r of document.querySelectorAll('input[type=radio]')) {
                    const name = r.name || '';
                    if (!name) continue;
                    if (!sections.has(name)) sections.set(name, []);
                    const existing = sections.get(name);
                    const val = (r.value||'').trim();
                    if (!existing.some(e => e.value === val)) {
                        existing.push({
                            name,
                            value: val,
                            checked: r.checked,
                            sectionTitle: getQuestionText(r),
                            label: getLabelText(r)
                        });
                    }
                }
                return [...sections.entries()].map(([k,v]) => ({name:k, options:v}));
            }''') or []

            for group in raw:
                group_name = group.get('name', '')
                options = group.get('options', [])
                if any(o.get('checked') for o in options):
                    continue
                
                # Try to use labels for matching, falling back to option values
                labels_map = {}
                for o in options:
                    val = o.get('value')
                    lbl = o.get('label') or val
                    if val and lbl:
                        labels_map[lbl.lower().strip()] = val
                        labels_map[val.lower().strip()] = val

                values = [o.get('label') or o.get('value') for o in options if o.get('value')]
                values = [v for v in values if v]

                # Employment type preference
                if any("permanent full-time" in v.lower() for v in values) and any(
                    "incorporated contractor" in v.lower() for v in values
                ):
                    target_lbl = next(v for v in values if "permanent full-time" in v.lower())
                    target_val = labels_map.get(target_lbl.lower().strip())
                    self.page.evaluate('''({name, value}) => {
                        for (const r of document.querySelectorAll('input[type="radio"]')) {
                            if (r.name === name && r.value === value) {
                                r.click(); r.checked = true;
                                r.dispatchEvent(new Event('input', {bubbles: true}));
                                r.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                        }
                    }''', {"name": group_name, "value": target_val})
                    filled += 1
                    self._log(f"lever-radio chose '{target_lbl}' for employment type")
                    continue

                section_title = ''
                for o in options:
                    if o.get('sectionTitle'):
                        section_title = o['sectionTitle']
                        break
                if not values:
                    continue
                # Clean section title
                clean_section = section_title
                if clean_section and "\n" in clean_section:
                    lines = clean_section.split("\n")
                    cleaned_lines = []
                    for line in lines:
                        t = line.strip()
                        if t in ("Yes", "No", "I am not a veteran", "Decline to self-identify"):
                            continue
                        cleaned_lines.append(t)
                    clean_section = cleaned_lines[0].strip() if cleaned_lines else clean_section
                question = _clean_question_text(clean_section) or clean_section or group_name

                if not self._radio_options_match_question(question, values):
                    continue
                prefs = self._resolve_for_field(
                    question,
                    profile=self.profile,
                    options=values,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(question, values, section_text=section_title),
                ) or []
                if not prefs:
                    continue
                # Find matching radio and click
                for pref in prefs:
                    pl = pref.lower().strip()
                    target_val = labels_map.get(pl)
                    if not target_val:
                        # Fuzzy match
                        for o in options:
                            lbl = (o.get('label') or o.get('value') or '').lower().strip()
                            if pl == lbl or (len(pl) >= 3 and pl in lbl):
                                target_val = o.get('value')
                                break
                    if target_val:
                        found = self.page.evaluate('''({name, value}) => {
                            for (const r of document.querySelectorAll('input[type=radio]')) {
                                if (r.name === name && r.value === value) return true;
                            }
                            return false;
                        }''', {"name": group_name, "value": target_val})
                        if found:
                            self.page.evaluate('''({name, value}) => {
                                for (const r of document.querySelectorAll('input[type=radio]')) {
                                    if (r.name === name && r.value === value) {
                                        const target = r.closest('label') || r.parentElement || r;
                                        target.scrollIntoView({block: 'center'});
                                        target.click();
                                        r.checked = true;
                                        r.dispatchEvent(new Event('change', {bubbles: true}));
                                        r.dispatchEvent(new Event('input', {bubbles: true}));
                                    }
                                }
                            }''', {"name": group_name, "value": target_val})
                            filled += 1
                            self._log(f"lever-radio chose '{pref}' (value='{target_val}') in group {group_name[:30]}...")
                            break
        except Exception as exc:
            self._log(f"lever-radio fill failed: {exc}")
        return filled

    def _fill_lever_card_checkboxes(self) -> int:
        """Handle Lever card-style checkbox groups."""
        filled = 0
        try:
            raw = self.page.evaluate('''() => {
                const getLabelText = (cb) => {
                    let labelText = '';
                    try {
                        const labelEl = cb.closest('label') || cb.parentElement;
                        if (labelEl) {
                            labelText = (labelEl.innerText || labelEl.textContent || '').trim();
                        }
                    } catch (e) {}
                    return labelText;
                };

                const getQuestionText = (cb) => {
                    const appField = cb.closest('.application-field, .form-group');
                    const prev = appField ? appField.previousElementSibling : null;
                    if (prev) {
                        const t = (prev.innerText || prev.textContent || '').trim();
                        if (t && t.length > 2) return t;
                    }
                    const card = cb.closest('.application-question, .custom-question, fieldset');
                    const titleEl = card ? card.querySelector('.application-label, legend, h4, h5') : null;
                    if (titleEl) {
                        const t = (titleEl.innerText || titleEl.textContent || '').trim();
                        if (t && t.length > 2) return t;
                    }
                    return '';
                };

                const sections = new Map();
                for (const cb of document.querySelectorAll('input[type=checkbox]')) {
                    const name = cb.name || '';
                    if (!name) continue;
                    if (!sections.has(name)) sections.set(name, []);
                    const existing = sections.get(name);
                    const val = (cb.value||'').trim();
                    if (!existing.some(e => e.value === val)) {
                        existing.push({
                            name,
                            value: val,
                            checked: cb.checked,
                            sectionTitle: getQuestionText(cb),
                            label: getLabelText(cb)
                        });
                    }
                }
                return [...sections.entries()].map(([k,v]) => ({name:k, options:v}));
            }''') or []

            for group in raw:
                group_name = group.get('name', '')
                options = group.get('options', [])
                if any(o.get('checked') for o in options):
                    continue
                
                # Map labels/values to checkbox options
                labels_map = {}
                for o in options:
                    val = o.get('value')
                    lbl = o.get('label') or val
                    if val and lbl:
                        labels_map[lbl.lower().strip()] = val
                        labels_map[val.lower().strip()] = val

                values = [o.get('label') or o.get('value') for o in options if o.get('value')]
                values = [v for v in values if v]

                section_title = ''
                for o in options:
                    if o.get('sectionTitle'):
                        section_title = o['sectionTitle']
                        break
                if not values:
                    continue
                
                # Clean section title to get the question
                clean_section = section_title
                if clean_section and "\n" in clean_section:
                    lines = clean_section.split("\n")
                    cleaned_lines = []
                    for line in lines:
                        t = line.strip()
                        if any(t.lower() == v.lower() for v in values):
                            continue
                        cleaned_lines.append(t)
                    clean_section = cleaned_lines[0].strip() if cleaned_lines else clean_section
                
                question = _clean_question_text(clean_section) or clean_section or group_name

                # Skip consent checkboxes
                if any(k in question.lower() for k in ("agree", "privacy", "terms", "consent", "certify")):
                    continue

                prefs = self._resolve_for_field(
                    question,
                    profile=self.profile,
                    options=values,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(question, values, section_text=section_title),
                ) or []
                if not prefs:
                    continue

                # Find matching checkbox and click
                for pref in prefs:
                    pl = pref.lower().strip()
                    target_val = labels_map.get(pl)
                    if not target_val:
                        # Fuzzy match
                        for o in options:
                            lbl = (o.get('label') or o.get('value') or '').lower().strip()
                            if pl == lbl or (len(pl) >= 3 and pl in lbl):
                                target_val = o.get('value')
                                break
                    if target_val:
                        found = self.page.evaluate('''({name, value}) => {
                            for (const cb of document.querySelectorAll('input[type=checkbox]')) {
                                if (cb.name === name && cb.value === value) return true;
                            }
                            return false;
                        }''', {"name": group_name, "value": target_val})
                        if found:
                            self.page.evaluate('''({name, value}) => {
                                for (const cb of document.querySelectorAll('input[type=checkbox]')) {
                                    if (cb.name === name && cb.value === value) {
                                        const target = cb.closest('label') || cb.parentElement || cb;
                                        target.scrollIntoView({block: 'center'});
                                        target.click();
                                        cb.checked = true;
                                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                                        cb.dispatchEvent(new Event('input', {bubbles: true}));
                                    }
                                }
                            }''', {"name": group_name, "value": target_val})
                            filled += 1
                            self._log(f"lever-checkbox chose '{pref}' (value='{target_val}') in group {group_name[:30]}...")
        except Exception as exc:
            self._log(f"lever-checkbox fill failed: {exc}")
        return filled

    def _fill_lever_card_text_inputs(self) -> int:
        """Fill Lever questionnaire text inputs nested inside cards[...] wrappers."""
        filled = 0
        try:
            descriptors = self.page.evaluate("""() => {
              const all = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), textarea'));
              return all.map((input, index) => {
                if (input.disabled || input.readOnly) return null;
                const wrap = input.closest('[class*="field" i], .application-field, .form-group, li, [class*="card" i]') || input.parentElement;
                const text = (wrap?.innerText || wrap?.textContent || '').trim();
                const name = input.name || '';
                if (!text && !name) return null;
                if (!name.startsWith('cards[') && !/[?*]/.test(text) && text.length < 24) return null;
                return {index, text: text.slice(0, 500), name};
              }).filter(Boolean);
            }""") or []
            elements = self.page.query_selector_all("input[type='text'], input:not([type]), textarea")
        except Exception:
            return 0
        for desc in descriptors:
            idx = int(desc.get("index", -1))
            if idx < 0 or idx >= len(elements):
                continue
            el = elements[idx]
            if not self._visible(el) or self._is_combobox(el):
                continue
            try:
                if (el.input_value() or "").strip():
                    continue
            except Exception:
                continue
            name_lower = (desc.get("name") or "").lower()
            if name_lower in ("location", "name", "email", "phone", "org", "urls[linkedin]", "urls[twitter]", "urls[github]", "urls[portfolio]", "urls[other]"):
                continue
            question = _clean_question_text(desc.get("text", "")) or desc.get("name", "")
            if not question:
                continue
            if any(k in question.lower() for k in ("location", "current location", "city", "address", "full name", "email", "phone", "resume")):
                continue
            # Skip bare "Other:" conditional follow-ups
            if question.strip().lower().rstrip(":*") == "other":
                continue
            try:
                prefs = self._resolve_for_field(
                    question, profile=self.profile, job_context=self.job_context,
                    hint=_ats_ai_hint(question, None, section_text=desc.get("text", "")[:240]),
                    required=True,
                ) or []
                if prefs and self._fill_input(el, str(prefs[0])[:2000]):
                    filled += 1
                    self._log(f"lever-card text filled: {question[:70]!r}")
            except Exception as exc:
                self._log(f"lever-card text failed: {exc}")
        return filled

    def _fill_radio_groups(self) -> int:
        """Fill generic radio button groups."""
        answered = 0
        try:
            radios = self.page.query_selector_all("input[type='radio']")
        except Exception:
            return 0
        groups: dict[str, list] = {}
        for r in radios:
            n = r.get_attribute("name") or ""
            if n:
                groups.setdefault(n, []).append(r)

        for group_name, group_radios in groups.items():
            if any(x.is_checked() for x in group_radios):
                continue
            val_options = [(r.get_attribute("value") or "").strip() for r in group_radios]
            context_label = self._question_text_from_group(group_radios[0], val_options)
            for r in group_radios:
                if context_label:
                    break
                lb = self._label_text_for(r)
                if lb:
                    context_label = lb
                    break
            if not val_options:
                continue
            prefs = self._resolve_for_field(
                context_label or group_name,
                profile=self.profile,
                options=val_options,
                job_context=self.job_context,
                hint=_ats_ai_hint(context_label or group_name, val_options),
            ) or []
            chosen = None
            for pref in prefs:
                pl = pref.lower().strip()
                for r in group_radios:
                    rv = (r.get_attribute("value") or "").lower()
                    if pl == rv or pl in rv or rv in pl:
                        chosen = r
                        break
                if chosen:
                    break
            if chosen:
                try:
                    chosen.check(force=True)
                    answered += 1
                except Exception:
                    pass
        return answered

    def _fill_consent_checkboxes(self) -> int:
        filled = 0
        try:
            boxes = self.page.query_selector_all("input[type='checkbox']")
        except Exception:
            return 0
        for box in boxes:
            try:
                if box.is_checked():
                    continue
                blob = self._field_blob(box)
                if any(k in blob for k in ("agree", "consent", "privacy", "terms", "acknowledge", "confirm", "certify")):
                    box.check(force=True)
                    filled += 1
                # Pronouns checkboxes
                elif box.get_attribute("name") == "pronouns" and not box.hasAttribute("type"):
                    box.check(force=True)
                    filled += 1
            except Exception:
                continue
        return filled

    def _fill_free_text_questions(self) -> int:
        filled = 0
        try:
            for el in self.page.query_selector_all("input[type='text'], input:not([type]), textarea"):
                try:
                    if not self._visible(el) or self._is_combobox(el):
                        continue
                except Exception:
                    continue
                try:
                    if (el.input_value() or "").strip():
                        continue
                except Exception:
                    continue
                blob = self._field_blob(el)
                q = self._visible_question_text(el) or _clean_question_text(blob) or blob
                if not q:
                    continue
                if any(k in q.lower() for k in ("first name", "last name", "email", "phone")) and len(q) < 24:
                    continue
                interesting = (
                    "?" in blob
                    or len(_clean_question_text(blob) or "") >= 18
                    or any(k in blob for k in (
                        "notice", "preferred name", "why ", "interests you",
                        "tell us about", "motivation", "cover letter", "additional",
                    ))
                )
                if not interesting:
                    continue
                prefs = self._resolve_for_field(
                    q,
                    profile=self.profile,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(q, None, section_text=blob[:200]),
                )
                if prefs and self._fill_input(el, str(prefs[0])[:2000]):
                    filled += 1
        except Exception:
            pass
        return filled

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _radio_options_match_question(question: str, options: list[str]) -> bool:
        """Sanity check that radio options belong to the question."""
        if not question or not options:
            return False
        q = question.lower()
        opts_l = " ".join(o.lower() for o in options)
        # Reject mismatched contexts (e.g. veteran question with location options)
        if "veteran" in q and "vancouver" in opts_l:
            return False
        if "location" in q and "veteran" in opts_l:
            return False
        return True

    def _page_text(self, limit: int = 20000) -> str:
        try:
            text = self.page.inner_text("body") or ""
        except Exception:
            try:
                text = self.page.content() or ""
            except Exception:
                text = ""
        if len(text) <= limit:
            return text
        head = limit // 2
        tail = limit - head
        return text[:head] + "\n" + text[-tail:]

    def _field_is_empty(self, el: Any) -> bool:
        try:
            val = (el.input_value() or "").strip()
            return not val or val.lower() in {"", "n/a", "select..."}
        except Exception:
            return True
