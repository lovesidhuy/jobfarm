"""Standard field filling mixin — name, email, phone, location, LinkedIn, etc.

Provides generic field scanning and filling that works across ATS platforms.
Adapters override ``_field_selectors`` to customize per-platform.
"""
from __future__ import annotations

import re
import time
from typing import Any

from ..types import FillStats


class FieldsMixin:
    """Mixin providing standard field filling for ATS adapters."""

    page: Any  # Playwright Page — set by adapter

    def _log(self, msg: str) -> None:
        try:
            from jobbots.core.utils import print_lg  # type: ignore
            print_lg(msg)
        except Exception:
            print(msg)

    @staticmethod
    def _visible(el: Any) -> bool:
        try:
            return bool(el and el.is_visible())
        except Exception:
            return False

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
    def _is_combobox(el: Any) -> bool:
        try:
            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            return role == "combobox" or "select__input" in cls or "react-select" in cls
        except Exception:
            return False

    @staticmethod
    def _field_is_empty(el: Any) -> bool:
        try:
            val = (el.input_value() or "").strip()
            return not val or val.lower() in {"", "n/a", "select..."}
        except Exception:
            return True

    def _dismiss_overlays(self) -> None:
        """Best-effort: close cookie banners / skip widgets that block clicks."""
        for text in ("Accept", "Accept all", "I agree", "Got it", "Close", "Skip", "No thanks"):
            try:
                btn = self.page.query_selector(f"button:has-text('{text}')") or self.page.query_selector(
                    f"a:has-text('{text}')"
                )
                if self._visible(btn):
                    self._safe_click(btn)
                    time.sleep(0.2)
            except Exception:
                continue
        # Hide LinkedIn Apply-with-LinkedIn widgets
        try:
            self.page.evaluate(
                """() => {
                  for (const el of document.querySelectorAll(
                    '.IN-widget, iframe[title*="LinkedIn"], .awli-button, [class*="awli"]'
                  )) {
                    try { el.style.pointerEvents = 'none'; el.style.opacity = '0.2'; } catch (e) {}
                  }
                }"""
            )
        except Exception:
            pass

    def fill_standard_fields(self, profile: dict[str, Any],
                             *, skip_location_widgets: bool = False) -> FillStats:
        """Fill direct-mapped fields (name, email, phone, LinkedIn, etc.).

        Returns FillStats with counts of filled/skipped.
        """
        stats = FillStats()

        # Direct ID / name hits — works for most platforms
        direct = [
            ("#first_name, input[name='first_name'], input[autocomplete='given-name']", profile.get("first_name", "")),
            ("#last_name, input[name='last_name'], input[autocomplete='family-name']", profile.get("last_name", "")),
            ("#preferred_name", profile.get("first_name", "")),
            ("#email, input[name='email'], input[type='email'], input[autocomplete='email']", profile.get("email", "")),
            ("#phone, input[name='phone'], input[type='tel'], input[autocomplete='tel']", profile.get("phone", "")),
            ("input[name='name']", profile.get("full_name", "")),  # Lever full name
            ("#location-input, input[name='location'], input[autocomplete='address-level2']", profile.get("location", "")),
            ("input[name='urls[LinkedIn]'], input[name='linkedin'], input[id*='linkedin' i]", profile.get("linkedin", "")),
            ("textarea[name='comments'], textarea[name='additionalInformation'], textarea[id*='cover']", profile.get("cover_letter", "")),
            ("textarea[name='cover_letter'], textarea[name='coverLetter']", profile.get("cover_letter", "")),
            ("input[name='website'], input[name='urls[Portfolio]'], input[id*='website' i]", profile.get("website", "")),
            ("input[name='city'], input[autocomplete='address-level2']", profile.get("city", "")),
            ("input[name='state'], input[autocomplete='address-level1']", profile.get("state", "")),
            ("input[name='zip'], input[name='zipcode'], input[autocomplete='postal-code']", profile.get("zipcode", "")),
            ("input[name='address'], input[name='street'], input[autocomplete='street-address']", profile.get("street", "")),
            ("input[name='country'], input[autocomplete='country-name']", profile.get("country", "")),
        ]
        for sel, val in direct:
            if not val:
                continue
            if skip_location_widgets and (
                "location" in sel or "address-level2" in sel
            ):
                continue
            try:
                el = self.page.query_selector(sel)
                if not el:
                    continue
                if self._is_combobox(el):
                    continue
                try:
                    existing = (el.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and existing.lower() not in {"", "n/a", "select..."}:
                    stats.skipped += 1
                    continue
                if self._fill_input(el, str(val)):
                    stats.filled += 1
            except Exception:
                continue

        return stats

    def fill_native_selects(self, profile: dict[str, Any],
                            *, job_context: str = "",
                            portal: str = "") -> int:
        """Fill native <select> elements that combobox/radio handlers miss."""
        filled = 0
        try:
            pairs = self.page.evaluate("""() => {
                const results = [];
                for (const [index, sel] of Array.from(document.querySelectorAll('select')).entries()) {
                    const card = sel.closest('.application-field, [class*="Card"], li, .field');
                    const container = card || sel.parentElement;
                    const label = (container.innerText || '').trim().slice(0, 250);
                    const options = Array.from(sel.options).map(o => ({value: o.value || '', text: o.text.trim()})).filter(o => o.text);
                    const placeholderIdx = Array.from(sel.options).findIndex(o => {
                        const t = (o.text || '').trim().toLowerCase();
                        return ['select...', 'select', 'please select', ''].includes(t);
                    });
                    const selIdx = sel.selectedIndex;
                    const hasValue = selIdx > 0 && selIdx < sel.options.length
                        && (sel.options[selIdx].text || '').trim().toLowerCase().indexOf('select...') < 0;
                    results.push({index, label, options, placeholderIdx, hasValue, name: sel.name || ''});
                }
                return results;
            }""") or []
        except Exception:
            pairs = []

        for pair in pairs:
            if pair.get("hasValue"):
                continue
            label = pair.get("label", "")
            option_objects = pair.get("options", [])
            options = [o.get("text", "") for o in option_objects if isinstance(o, dict)]
            if not options:
                continue
            try:
                q = label
                # Clean cards[...] prefix
                q = re.sub(r'cards\[[^\]]+\]\[field\d+\]\s*', '', q).strip()
                if not q:
                    q = label

                # Import here to avoid circular
                from ..mixins.questions import _clean_question_text
                q = _clean_question_text(q) or q

                # Resolve via brain
                from ..mixins.questions import QuestionsMixin
                qm = QuestionsMixin()
                qm.page = self.page
                qm.platform_name = portal
                prefs = qm._resolve_for_field(
                    q,
                    profile=profile,
                    options=options,
                    job_context=job_context,
                    required=True,
                ) or []

                if prefs:
                    mapped = qm._map_pref_to_option(str(prefs[0]), options)
                    if mapped:
                        # Try Select2 API or native JS
                        ok = self.page.evaluate("""({nameHint, value}) => {
                            if (typeof $ !== 'undefined') {
                                const sel = $('select').filter(function() {
                                    const c = $(this).closest('.application-field, [class*="Card"], li, .field')[0] || this.parentElement;
                                    const txt = (c.innerText || '').trim();
                                    return txt.indexOf(nameHint) >= 0;
                                });
                                if (sel.length) {
                                    const node = sel[0];
                                    const match = Array.from(node.options).find(o => (o.text || '').trim() === value || o.value === value);
                                    if (!match) return false;
                                    sel.val(match.value).trigger('change');
                                    if (sel.hasClass('select2-hidden-accessible')) {
                                        sel.attr('data-ats-filled', '1');
                                        const visible = sel.next('.select2-container').find('.select2-selection__rendered');
                                        if (visible.length) visible.text(match.text);
                                    }
                                    return true;
                                }
                            }
                            for (const s of document.querySelectorAll('select')) {
                                const c = s.closest('.application-field, [class*="Card"], li, .field') || s.parentElement;
                                const txt = (c.innerText || '').trim().slice(0, 200);
                                if (txt.indexOf(nameHint) >= 0) {
                                    let set = false;
                                    for (let i = 0; i < s.options.length; i++) {
                                        if (s.options[i].text.trim() === value ||
                                            s.options[i].text.trim().toLowerCase() === value.toLowerCase()) {
                                            s.selectedIndex = i;
                                            ['change', 'input', 'select2:select'].forEach(ev => {
                                                s.dispatchEvent(new Event(ev, {bubbles: true}));
                                            });
                                            try { $(s).trigger('change'); } catch(e) {}
                                            set = true;
                                            break;
                                        }
                                    }
                                    s.setAttribute('data-ats-filled', '1');
                                    const s2rendered = s.nextElementSibling?.querySelector('.select2-selection__rendered');
                                    if (s2rendered) s2rendered.textContent = value;
                                    return set;
                                }
                            }
                            return false;
                        }""", {
                            "nameHint": label[:50] if len(label) > 50 else label,
                            "value": mapped,
                        })
                        if ok:
                            filled += 1
                            self._log(f"native select: {q[:50]!r} → {mapped[:40]!r}")
                            continue
            except Exception as exc:
                self._log(f"native select error: {exc}")
        return filled

    def _select_native(self, el: Any, preferences: list[str]) -> bool:
        """Select an option in a native <select> element."""
        try:
            opts = el.evaluate(
                "el => Array.from(el.options).map(o => ({v:o.value, t:(o.text||'').trim()}))"
            ) or []
        except Exception:
            return False
        prefs = [p.lower() for p in preferences if p]
        for pref in prefs:
            for opt in opts:
                text = (opt.get("t") or "").lower()
                val = (opt.get("v") or "").lower()
                if pref == text or pref == val or pref in text or text in pref:
                    try:
                        el.evaluate(
                            "(sel, val) => { sel.value = val; sel.dispatchEvent(new Event('change', { bubbles: true })); }",
                            opt.get("v")
                        )
                        return True
                    except Exception:
                        continue
        # Prefer decline options for EEO
        for opt in opts:
            text = (opt.get("t") or "").lower()
            if "decline" in text:
                try:
                    el.evaluate(
                        "(sel, val) => { sel.value = val; sel.dispatchEvent(new Event('change', { bubbles: true })); }",
                        opt.get("v")
                    )
                    return True
                except Exception:
                    continue
        return False

    def _check_input_via_js(self, el: Any) -> bool:
        """Force-check a checkbox/radio (label-styled or hidden input)."""
        try:
            if el.is_checked():
                return True
        except Exception:
            pass
        try:
            ok = self.page.evaluate(
                """(node) => {
                  if (!node) return false;
                  const label = node.closest('label')
                    || (node.id && document.querySelector('label[for="' + CSS.escape(node.id) + '"]'))
                    || node.parentElement;
                  try { node.scrollIntoView({block: 'center'}); } catch (e) {}
                  if (label && typeof label.click === 'function') {
                    label.click();
                  } else {
                    node.click();
                  }
                  if (!node.checked) {
                    node.checked = true;
                    ['input', 'change', 'click'].forEach(t => {
                      node.dispatchEvent(new Event(t, {bubbles: true}));
                    });
                    node.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                  }
                  return !!node.checked;
                }""",
                el,
            )
            if ok:
                return True
        except Exception:
            pass
        try:
            self._safe_click(el, force=True)
            time.sleep(0.08)
            return bool(el.is_checked())
        except Exception:
            return False

    def _click_checkbox_by_label_text(self, label_text: str) -> bool:
        """Find and check a multi-select option by its visible label text."""
        needle = (label_text or "").strip()
        if not needle:
            return False
        try:
            ok = self.page.evaluate(
                """(needle) => {
                  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                  const want = norm(needle);
                  if (!want) return false;

                  const tryCheck = (cb) => {
                    if (!cb || cb.disabled) return false;
                    if (cb.checked) return true;
                    const lab = cb.closest('label')
                      || (cb.id && document.querySelector('label[for="' + CSS.escape(cb.id) + '"]'))
                      || cb.parentElement;
                    try { (lab || cb).scrollIntoView({block: 'center'}); } catch (e) {}
                    try { (lab || cb).click(); } catch (e) { try { cb.click(); } catch (e2) {} }
                    if (!cb.checked) {
                      cb.checked = true;
                      ['input', 'change', 'click'].forEach(t =>
                        cb.dispatchEvent(new Event(t, {bubbles: true}))
                      );
                    }
                    return !!cb.checked;
                  };

                  for (const lab of document.querySelectorAll('label')) {
                    const t = norm(lab.innerText || lab.textContent || '');
                    if (!t) continue;
                    if (t.length > 180) continue;
                    if (t === want || t.startsWith(want) || want.startsWith(t) || t.includes(want)) {
                      const cb = lab.querySelector('input[type=checkbox]')
                        || (lab.htmlFor && document.getElementById(lab.htmlFor));
                      if (cb && cb.type === 'checkbox' && tryCheck(cb)) return true;
                    }
                  }

                  for (const cb of document.querySelectorAll('input[type=checkbox]')) {
                    const wrap = cb.closest('label, li, .checkbox, [class*="checkbox"], div') || cb.parentElement;
                    const t = norm(wrap?.innerText || wrap?.textContent || cb.value || '');
                    if (!t || t.length > 180) continue;
                    if (t === want || t.includes(want) || want.includes(t.split('\\n')[0])) {
                      if (tryCheck(cb)) return true;
                    }
                  }

                  for (const el of document.querySelectorAll('span, div, p, li, label')) {
                    const raw = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (norm(raw) !== want) continue;
                    if (raw.length > 120) continue;
                    const cb = el.querySelector('input[type=checkbox]')
                      || el.closest('label')?.querySelector('input[type=checkbox]')
                      || el.parentElement?.querySelector('input[type=checkbox]');
                    if (cb && tryCheck(cb)) return true;
                    try { el.click(); } catch (e) {}
                  }
                  return false;
                }""",
                needle,
            )
            return bool(ok)
        except Exception:
            return False

    def _validation_errors(self) -> list[str]:
        """Extract visible validation error messages from the page."""
        errors = []
        try:
            raw = self.page.evaluate("""() => {
                const out = [];
                for (const sel of ['.error-message', '.field-error-message', '.error-text',
                                   '.validation-message', '.form-error-message', '[role="alert"]',
                                   'span.error', 'p.error', 'div.error-text', 'span[id*="error"]']) {
                    for (const el of document.querySelectorAll(sel)) {
                        const style = window.getComputedStyle(el);
                        if (style && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
                            const t = (el.innerText || el.textContent || '').trim();
                            if (t && t.length > 3 && t.length < 200 && el.offsetParent !== null) {
                                const low = t.toLowerCase();
                                if (low.includes('required') || low.includes('invalid') || low.includes('please') || low.includes('select')) {
                                    out.push(t);
                                }
                            }
                        }
                    }
                }
                return out;
            }""") or []
            errors = [str(e).strip() for e in raw if str(e).strip()]
        except Exception:
            pass
        # Also check for aria-invalid fields
        try:
            invalid = self.page.query_selector_all("[aria-invalid='true']")
            for el in invalid:
                try:
                    if el.is_visible():
                        label = self._label_text_for(el) if hasattr(self, '_label_text_for') else ""
                        if label and label not in errors:
                            errors.append(label)
                except Exception:
                    pass
        except Exception:
            pass
        return errors
