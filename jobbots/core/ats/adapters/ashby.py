"""Ashby ATS adapter.

Handles:
  * React SPA at ``jobs.ashbyhq.com``
  * Dynamic form fields rendered via React
  * Conditional questions that appear/disappear
  * File uploads via hidden inputs
  * Standard fields use ``name`` attributes
  * Ashby form structure with field wrappers
"""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any
from urllib.parse import urlparse

from ..base import ATSAdapter
from ..mixins.upload import UploadMixin
from ..mixins.captcha import CaptchaMixin
from ..mixins.questions import QuestionsMixin, _clean_question_text, _ats_ai_hint
from ..mixins.fields import FieldsMixin
from ..mixins.verification import VerificationMixin
from ..types import FillStats


class AshbyAdapter(
    UploadMixin,
    CaptchaMixin,
    QuestionsMixin,
    FieldsMixin,
    VerificationMixin,
    ATSAdapter,
):
    platform_name = "ashby"

    def __init__(self) -> None:
        self.page: Any = None
        self.profile: dict[str, Any] = {}
        self.job_title = ""
        self.job_company = ""
        self.job_context = ""
        # Human-readable proof for queue / training (set only on real success).
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
                r"(?:^|\.)(?:jobs\.ashbyhq\.com|ashbyhq\.com)(?:/|$)",
                host + "/", re.I,
            ))
        except Exception:
            return False

    @classmethod
    def detect_from_page(cls, page: Any) -> bool:
        if cls.detect(getattr(page, "url", "") or ""):
            return True
        try:
            html = (page.content() or "")[:12000].lower()
            return any(m in html for m in (
                "ashbyhq.com", "ashby-embedded", "ashby-application",
                "ashby_application", "ashby-form",
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

        # Ashby job pages render the form behind an "Application" tab.  The
        # form is not present in the DOM until that tab is activated.
        self._open_application_tab()

        # Wait for Ashby React SPA to render
        self._wait_for_form()
        self._dismiss_overlays()

    def authenticate(self) -> bool:
        return True

    def upload_documents(self, **kwargs: Any) -> dict[str, bool]:
        resume_path = kwargs.get("resume_path") or (self.profile.get("resume_path") or "").strip()
        cover_path = kwargs.get("cover_letter_path") or (self.profile.get("cover_letter_path") or "").strip()
        return UploadMixin.upload_documents(
            self, resume_path=resume_path, cover_letter_path=cover_path
        )

    def fill_application(self) -> FillStats:
        stats = self.fill_standard_fields(self.profile)
        stats.merge(self._fill_ashby_fields())
        # Ashby often uses a single "Name" field; brain can return "missed"
        # when the field is already half-filled by resume autofill.
        stats.filled += self._ensure_identity_fields()
        # Keep Ashby's async location choice as the final write in this phase;
        # other React field updates can otherwise discard an uncommitted query.
        stats.filled += self._commit_ashby_location()
        return stats

    def _ensure_identity_fields(self) -> int:
        """Force-fill Name/Email when empty — required on every Ashby form."""
        filled = 0
        full = (
            self.profile.get("full_name")
            or f"{self.profile.get('first_name', '')} {self.profile.get('last_name', '')}".strip()
        )
        email = self.profile.get("email") or ""
        mapping = (
            (("name", "full name", "full_name", "applicant name"), full),
            (("email", "e-mail"), email),
            (("phone", "mobile", "tel"), self.profile.get("phone") or ""),
            (("linkedin",), self.profile.get("linkedin") or ""),
            (("pronoun",), self.profile.get("pronouns") or "He/Him"),
        )
        try:
            fields = self.page.query_selector_all("input, textarea") or []
        except Exception:
            return 0
        for el in fields:
            try:
                typ = (el.get_attribute("type") or "text").lower()
                if typ in {"hidden", "submit", "button", "file", "checkbox", "radio"}:
                    continue
                if not self._visible(el):
                    continue
                try:
                    existing = (el.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and existing.lower() not in {"", "n/a", "missed", "select..."}:
                    continue
                blob = (self._field_blob(el) + " " + (self._visible_question_text(el) or "")).lower()
                for keys, val in mapping:
                    if not val:
                        continue
                    if any(k in blob for k in keys):
                        if self._fill_input(el, str(val)):
                            filled += 1
                        break
            except Exception:
                continue
        return filled

    def repair_required_fields(self) -> int:
        """Re-commit identity, location, Yes/No, and empty required text fields."""
        n = 0
        n += self._ensure_identity_fields()
        n += self._commit_ashby_location()
        try:
            n += self._fill_button_choice_groups()
        except Exception:
            pass
        try:
            n += self._fill_consent_checkboxes()
        except Exception:
            pass
        try:
            n += self._fill_checkbox_groups()
        except Exception:
            pass
        # Fill any still-empty visible required inputs.
        try:
            for el in self.page.query_selector_all(
                "input[required], textarea[required], input[aria-required='true'], "
                "textarea[aria-required='true']"
            ) or []:
                if not self._visible(el):
                    continue
                typ = (el.get_attribute("type") or "text").lower()
                if typ in {"hidden", "submit", "button", "file", "checkbox", "radio"}:
                    continue
                try:
                    if (el.input_value() or "").strip():
                        continue
                except Exception:
                    continue
                blob = self._field_blob(el)
                q = self._visible_question_text(el) or _clean_question_text(blob) or blob
                prefs = self._resolve_for_field(
                    q,
                    profile=self.profile,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(q, None, section_text=blob[:200]),
                    required=True,
                )
                if prefs and self._fill_input(el, str(prefs[0])[:2000]):
                    n += 1
        except Exception:
            pass
        return n

    def _commit_ashby_location(self) -> int:
        """Commit the profile location to Ashby's async autocomplete."""
        target_text = (
            self.profile.get("location")
            or f"{self.profile.get('city', 'Vancouver')}, {self.profile.get('state', 'British Columbia')}, {self.profile.get('country', 'Canada')}"
        )
        city_keyword = (self.profile.get("city") or "Vancouver").strip()
        try:
            field = self.page.query_selector("input[placeholder='Start typing...'], input[name*='location'], input[placeholder*='location' i], input[name='f850d369-7f4a-4e8a-999f-1658d27ccc0f']")
            if not field or not self._visible(field):
                return 0
            if not self._fill_input(field, target_text):
                return 0
            time.sleep(0.45)
            choice = self.page.get_by_text(target_text, exact=True).last
            if choice.count() and self._visible(choice) and self._safe_click(choice, force=True):
                return 1
            if city_keyword:
                choice_partial = self.page.get_by_text(city_keyword, exact=False).first
                if choice_partial.count() and self._visible(choice_partial) and self._safe_click(choice_partial, force=True):
                    return 1
        except Exception:
            pass
        return 0

    def answer_questions(self) -> int:
        answered = 0
        # Conditional / custom questions
        answered += self._fill_custom_questions()
        # Radio groups
        answered += self._fill_radio_groups()
        # Ashby Yes/No controls are buttons backed by hidden checkboxes, not
        # native radios.  They need their own question-aware handler.
        answered += self._fill_button_choice_groups()
        # Checkboxes
        answered += self._fill_consent_checkboxes()
        # Required multi-select questions (for example, team preferences).
        answered += self._fill_checkbox_groups()
        # Dropdowns (Ashby uses both native and custom)
        answered += self._fill_dropdowns()
        # Free-text questions
        answered += self._fill_free_text_questions()
        return answered

    def submit(self) -> bool:
        """Ashby is a multi-step React SPA — advance Next, then Submit."""
        self._dismiss_overlays()
        try:
            self.repair_required_fields()
        except Exception:
            pass
        # Multi-step: click Next a few times if present before Submit.
        for _ in range(4):
            if self._click_first_visible(
                (
                    "button:has-text('Submit application')",
                    "button:has-text('Submit Application')",
                    "button:has-text('Submit')",
                    "button[type='submit']",
                    "input[type='submit']",
                    "[data-testid='submit-button']",
                    "[data-testid='apply-button']",
                    "button.ashby-application-form-submit-button",
                )
            ):
                time.sleep(1.5)
                return True
            advanced = self._click_first_visible(
                (
                    "button:has-text('Next')",
                    "button:has-text('Continue')",
                    "button:has-text('Review')",
                    "button:has-text('Save and continue')",
                )
            )
            if not advanced:
                break
            time.sleep(0.7)
            self._dismiss_overlays()
            # Re-answer any newly revealed fields on next step.
            try:
                self.answer_questions()
                self.repair_required_fields()
            except Exception:
                pass

        # Last resort: any primary-looking button in the form footer.
        if self._click_first_visible(
            (
                "button:has-text('Apply')",
                "form button[type='submit']",
                "form button:last-of-type",
                "[class*='ashby'] button[type='submit']",
                "button.ashby-application-form-submit-button",
            )
        ):
            time.sleep(1.5)
            return True
        # JS requestSubmit for stubborn React handlers.
        try:
            ok = self.page.evaluate(
                """() => {
                    const btn = document.querySelector(
                      "button.ashby-application-form-submit-button, " +
                      "button[type='submit'], form button"
                    );
                    if (btn) { btn.click(); return true; }
                    const form = document.querySelector('form');
                    if (form && typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                        return true;
                    }
                    return false;
                }"""
            )
            if ok:
                time.sleep(1.5)
                return True
        except Exception:
            pass
        return False

    def _click_first_visible(self, selectors: tuple[str, ...]) -> bool:
        for sel in selectors:
            try:
                els = self.page.query_selector_all(sel) or []
            except Exception:
                els = []
            if not els:
                try:
                    el = self.page.query_selector(sel)
                    els = [el] if el else []
                except Exception:
                    els = []
            for el in els:
                try:
                    if not el or not self._visible(el):
                        continue
                    label = (
                        (el.inner_text() or "")
                        + " "
                        + (el.get_attribute("aria-label") or "")
                    ).lower()
                    # Skip non-submit chrome
                    if any(
                        bad in label
                        for bad in (
                            "cancel",
                            "back",
                            "previous",
                            "upload",
                            "add file",
                            "choose file",
                            "sign in",
                            "log in",
                        )
                    ):
                        continue
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    if self._safe_click(el, force=True):
                        return True
                    # Playwright force path sometimes needed on React portals
                    try:
                        el.click(force=True, timeout=3000)
                        return True
                    except Exception:
                        continue
                except Exception:
                    continue
        return False

    def verify_submission(self) -> str | None:
        """Page-primary confirmation (email is secondary / not required).

        Ashby often stays on ``.../application`` and shows a green banner, e.g.
        ``Success / Your application was successfully submitted!``.

        Never treat "submit button gone" alone as success — SPA multi-step,
        anti-spam interstitials, and soft re-renders drop Submit without a win.
        """
        from ..confirmation import classify_page_confirmation

        self.confirmation_evidence = ""
        url = self.page.url or ""
        text = self._page_text(24000) or ""
        status, evidence = classify_page_confirmation(
            url, text, page=self.page, platform="ashby",
        )
        if evidence:
            self.confirmation_evidence = evidence
        return status

    # ── Ashby-specific handlers ───────────────────────────────────────

    def _open_application_tab(self) -> None:
        """Activate Ashby's application tab when starting on the job page."""
        try:
            self.page.wait_for_selector("[role='tab'], button, a", timeout=8000)
        except Exception:
            pass

        # Try clicking the big blue "Apply for this Job" link or button first
        try:
            apply_btns = self.page.query_selector_all("a, button")
            for btn in apply_btns:
                text = (btn.inner_text() or "").strip().lower()
                if text == "apply for this job":
                    self._safe_click(btn, force=True)
                    time.sleep(1.0)
                    return
        except Exception:
            pass

        # Fallback to finding and clicking the "Application" tab
        try:
            tabs = self.page.query_selector_all("[role='tab'], button, a")
        except Exception:
            return
        for tab in tabs:
            try:
                label = (tab.inner_text() or tab.get_attribute("aria-label") or "").strip().lower()
                if label != "application":
                    continue
                self._safe_click(tab, force=True)
                time.sleep(1.0)
                return
            except Exception:
                continue

    def _wait_for_form(self, timeout_s: float = 15.0) -> None:
        """Wait for Ashby React SPA to render the application form."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                # Check for form elements
                if self.page.query_selector(
                    "form, input[name], textarea, select, [role='form'], "
                    "[class*='application'], [class*='apply'], "
                    "input[type='text'], input[type='email']"
                ):
                    # Extra wait for React hydration
                    time.sleep(1.0)
                    return
            except Exception:
                pass
            time.sleep(0.5)
        # Even if no form detected, proceed — the page may use non-standard markup

    def _fill_ashby_fields(self) -> FillStats:
        """Fill Ashby-specific form fields."""
        stats = FillStats()

        location_value = ", ".join(
            x for x in (
                self.profile.get("city") or self.profile.get("location") or "",
                "British Columbia" if str(self.profile.get("state") or "").upper() in {"BC", "B.C.", "BRITISH COLUMBIA"}
                else self.profile.get("state") or "",
                self.profile.get("country") or "Canada",
            ) if str(x).strip()
        )

        try:
            fields = self.page.query_selector_all("input, textarea, select")
        except Exception:
            return stats

        mapping = [
            (("first name", "firstname", "given-name", "fname", "first_name"), self.profile.get("first_name", "")),
            (("last name", "lastname", "family-name", "lname", "surname", "last_name"), self.profile.get("last_name", "")),
            (("email", "e-mail"), self.profile.get("email", "")),
            (("phone", "mobile", "tel", "phone_number"), self.profile.get("phone", "")),
            (("linkedin",), self.profile.get("linkedin", "")),
            (("website", "portfolio", "github"), self.profile.get("website", "")),
            (("location", "located", "city", "address"), location_value),
            (("name", "full name", "full_name"), self.profile.get("full_name", "")),
            (("pronoun",), self.profile.get("pronouns") or "He/Him"),
        ]

        for el in fields:
            try:
                tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
                typ = (el.get_attribute("type") or "text").lower()
                if typ in {"hidden", "submit", "button", "file", "image"}:
                    continue
                if typ in {"checkbox", "radio"}:
                    continue
                if not self._visible(el):
                    continue

                blob = self._field_blob(el)
                question = self._visible_question_text(el) or _clean_question_text(blob) or blob

                # Ashby's date picker rejects the brain's natural-language
                # "Immediate" answer.  Use the candidate's configured
                # immediate-start policy as today's date in the control's
                # locale-friendly format.
                if (el.get_attribute("placeholder") or "").strip().lower() == "pick date...":
                    if self._fill_input(el, date.today().strftime("%m/%d/%Y")):
                        try:
                            el.press("Tab")
                        except Exception:
                            pass
                        stats.filled += 1
                    continue

                # Skip already-filled
                try:
                    existing = (el.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and existing.lower() not in {"", "n/a", "select..."}:
                    stats.skipped += 1
                    continue

                # Location is a live autocomplete with a known profile value;
                # never let a free-form model answer replace it.
                if any(k in question.lower() for k in ("location", "located")):
                    prefs = [location_value]
                else:
                    prefs = self._resolve_for_field(
                        question,
                        profile=self.profile,
                        job_context=self.job_context,
                        hint=_ats_ai_hint(question, None, section_text=blob[:200]),
                    )

                # A generic desired-pay profile value must not undercut a
                # compensation band published on the live Ashby posting.
                # Use the midpoint of a displayed CAD/USD range when this is
                # an annual salary/compensation field.
                q_low = question.lower()
                if any(k in q_low for k in ("salary", "compensation", "annual pay", "annual salary")):
                    try:
                        page_text = self._page_text(12000)
                    except Exception:
                        page_text = ""
                    band = re.search(
                        r"(?:CA\$|US\$|\$)\s*([\d,.]+)\s*K?\s*(?:–|-|to)\s*"
                        r"(?:CA\$|US\$|\$)?\s*([\d,.]+)\s*K?",
                        page_text,
                        re.I,
                    )
                    if band:
                        low = float(band.group(1).replace(",", ""))
                        high = float(band.group(2).replace(",", ""))
                        if max(low, high) < 1000:
                            low *= 1000
                            high *= 1000
                        prefs = [str(int((low + high) / 2))]

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
                # Ashby combobox fields (location, school, etc.) are custom
                # React typeaheads.  A plain fill only changes the text; the
                # form remains invalid until an actual listbox option is
                # committed by clicking a dropdown result.
                if self._is_combobox(el):
                    if self._fill_input(el, str(value)):
                        # Ashby's suggestions render asynchronously.
                        time.sleep(0.6)
                        committed = False
                        try:
                            options = self.page.locator("[role='option']")
                            opt_count = options.count()
                            if opt_count > 0:
                                # Build list of visible option texts
                                opt_texts = []
                                for i in range(min(opt_count, 15)):
                                    try:
                                        txt = (options.nth(i).inner_text() or "").strip()
                                        if txt:
                                            opt_texts.append((i, txt))
                                    except Exception:
                                        pass

                                val_lower = str(value).lower().strip()
                                best_idx = None

                                # 1) Exact match
                                for idx, txt in opt_texts:
                                    if txt.lower() == val_lower:
                                        best_idx = idx
                                        break

                                # 2) Option starts with or contains the typed value
                                if best_idx is None:
                                    for idx, txt in opt_texts:
                                        if val_lower in txt.lower():
                                            best_idx = idx
                                            break

                                # 3) Typed value contains the option text
                                if best_idx is None:
                                    for idx, txt in opt_texts:
                                        if txt.lower() in val_lower and len(txt) > 3:
                                            best_idx = idx
                                            break

                                # 4) Fall back to first option
                                if best_idx is None and opt_texts:
                                    best_idx = opt_texts[0][0]

                                if best_idx is not None:
                                    try:
                                        chosen = options.nth(best_idx)
                                        if chosen.is_visible():
                                            chosen.click(force=True)
                                            committed = True
                                            matched_text = opt_texts[best_idx][1] if best_idx < len(opt_texts) else "?"
                                            self._log(f"ashby-combobox selected '{matched_text}' for q='{question[:40]}'")
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        if not committed:
                            try:
                                el.press("ArrowDown")
                                el.press("Enter")
                            except Exception:
                                pass
                        stats.filled += 1
                    continue
                if self._fill_input(el, str(value)):
                    stats.filled += 1
            except Exception:
                continue

        return stats

    def _fill_custom_questions(self) -> int:
        """Fill Ashby custom/conditional questions."""
        filled = 0
        try:
            # Ashby renders custom questions in various containers
            for el in self.page.query_selector_all(
                "input[type='text'], input:not([type]), textarea, "
                "input[type='number'], input[type='url']"
            ):
                try:
                    if not self._visible(el):
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
                # Skip standard identity fields already handled
                if any(k in q.lower() for k in ("first name", "last name", "email", "phone")) and len(q) < 24:
                    continue
                # Skip file inputs
                typ = (el.get_attribute("type") or "").lower()
                if typ == "file":
                    continue
                if self._is_combobox(el):
                    continue

                interesting = (
                    "?" in blob or "?" in q
                    or len(_clean_question_text(blob) or "") >= 15
                    or any(k in blob for k in (
                        "why", "tell us", "describe", "experience",
                        "motivation", "interest", "additional",
                        "salary", "compensation", "notice", "start date",
                        "authorized", "eligible", "sponsorship", "visa",
                        "how did you hear", "referral", "source",
                    ))
                )
                if not interesting:
                    continue

                prefs = self._resolve_for_field(
                    q,
                    profile=self.profile,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(q, None, section_text=blob[:200]),
                    required=True,
                )
                if prefs and self._fill_input(el, str(prefs[0])[:2000]):
                    filled += 1
                    self._log(f"custom question: {q[:60]!r} → {prefs[0][:40]!r}")
        except Exception:
            pass
        return filled

    def _fill_radio_groups(self) -> int:
        """Fill Ashby radio button groups."""
        answered = 0
        try:
            radios = self.page.query_selector_all("input[type='radio']")
        except Exception:
            return 0
        groups: dict[str, list] = {}
        for r in radios:
            n = r.get_attribute("name") or ""
            if not n:
                try:
                    n = "__ashby_group__" + re.sub(
                        r"\s+", " ",
                        (r.evaluate(
                            "e => (e.closest('[role=\\\"radiogroup\\\"]')?.innerText || e.parentElement?.parentElement?.innerText || '')"
                        ) or "").strip(),
                    )[:160]
                except Exception:
                    n = "__ashby_radio__"
            groups.setdefault(n, []).append(r)

        for group_name, group_radios in groups.items():
            if any(x.is_checked() for x in group_radios):
                continue
            # Ashby commonly uses value="on" for every radio.  Use the
            # associated visible label in that case so the answering brain
            # can distinguish options such as Male/Female.
            val_options = []
            for r in group_radios:
                raw_value = (r.get_attribute("value") or "").strip()
                label = (self._label_text_for(r) or "").strip()
                if raw_value.lower() in {"", "on"}:
                    try:
                        local_label = (r.evaluate(
                            "e => (e.parentElement?.parentElement?.innerText || '').trim()"
                        ) or "").splitlines()[0].strip()
                        if local_label:
                            label = local_label
                    except Exception:
                        pass
                val_options.append(label if raw_value.lower() in {"", "on"} and label else raw_value)
            # The label attached to a radio is normally just its option
            # (e.g. "2027"), not the question.  Resolve against the Ashby
            # field-entry heading so the answer policy receives the real
            # context (e.g. "Expected Graduation Year").
            context_label = self._ashby_question_text(group_radios[0])
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
            # For explicit binary gender options, the profile policy is
            # authoritative even if the brain returns no answer or a generic
            # HTML value such as "on".
            profile_gender = str(self.profile.get("gender") or "").strip()
            if profile_gender and any(
                profile_gender.lower() == option.lower() for option in val_options
            ) and any(option.lower() in {"male", "female"} for option in val_options):
                prefs = [profile_gender]
            # EEO-style fields are often optional, so the policy resolver may
            # intentionally return no answer.  If the configured profile has
            # an exact option match, use it for the generic Ashby radio group.
            if not prefs:
                if profile_gender and any(
                    profile_gender.lower() == option.lower() for option in val_options
                ):
                    prefs = [profile_gender]
            chosen = None
            for pref in prefs:
                pl = pref.lower().strip()
                for r, option_label in zip(group_radios, val_options):
                    rv = (option_label or r.get_attribute("value") or "").lower()
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

    def _fill_button_choice_groups(self) -> int:
        """Answer Ashby's button-style question groups (Yes/No and multi-option).

        Ashby renders choice questions as a container with [data-field-entry-id]
        containing multiple <button> elements.  Clicks are sent via JS
        dispatchEvent to guarantee React's synthetic event system fires.
        """
        answered = 0
        try:
            containers = self.page.query_selector_all("[data-field-entry-id]") or []
        except Exception:
            return 0
        for container in containers:
            try:
                if not self._visible(container):
                    continue
                buttons = container.query_selector_all("button") or []
                # Collect all visible button labels (not just Yes/No)
                options: dict[str, Any] = {}
                for button in buttons:
                    label = (button.inner_text() or "").strip()
                    if label and self._visible(button):
                        options[label.lower()] = button
                if len(options) < 2:
                    continue
                # Do not disturb a choice already committed by the applicant.
                boxes = container.query_selector_all(
                    "input[type='checkbox'], input[type='radio']"
                ) or []
                if any(box.is_checked() for box in boxes):
                    continue
                question = ""
                try:
                    lbl = container.query_selector("label, [class*='label'], p, span")
                    question = (lbl or container).inner_text() or ""
                except Exception:
                    try:
                        question = container.inner_text() or ""
                    except Exception:
                        pass
                option_labels = list(options.keys())  # lowercase keys
                display_labels = list(options.keys())  # same for display
                # Prefer display-cased from the raw buttons
                try:
                    display_labels = [
                        (b.inner_text() or k).strip()
                        for k, b in options.items()
                    ]
                except Exception:
                    pass
                prefs = self._resolve_for_field(
                    question,
                    profile=self.profile,
                    options=display_labels,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(question, display_labels),
                    required=True,
                ) or []
                choice = next(
                    (str(pref).strip().lower() for pref in prefs
                     if str(pref).strip().lower() in options),
                    "",
                )
                # Fallback: match pref substring against option labels
                if not choice and prefs:
                    pref_l = str(prefs[0]).strip().lower()
                    for opt_l in option_labels:
                        if pref_l in opt_l or opt_l in pref_l:
                            choice = opt_l
                            break
                if not choice:
                    continue

                btn_el = options[choice]
                # Select exactly once.  Ashby's Yes/No control stores its
                # state behind a hidden checkbox: a second click deselects
                # the answer.  In particular, never follow a successful
                # Playwright click with a synthetic click.
                clicked = self._react_click(btn_el)
                if clicked:
                    self._log(f"button-choice: {question[:50]!r} → {choice!r}")
                    answered += 1
            except Exception:
                continue
        return answered

    def _react_click(self, el: Any) -> bool:
        """Click a React-controlled button reliably.

        Ashby's Yes/No buttons are React-managed.  Prefer the browser's
        normal click and verify it selected *this* button.  Only use the
        synthetic event sequence as a single fallback when the normal click
        did not commit; dispatching it after a normal click toggles Ashby's
        choice back off.
        """
        _JS_REACT_CLICK = """
        (el) => {
            el.scrollIntoView({block: 'center'});
            ['mousedown', 'mouseup', 'click'].forEach(type => {
                el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true, cancelable: true, view: window
                }));
            });
        }
        """
        # Try Playwright native click first.
        try:
            el.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        try:
            el.click(force=True, timeout=3000)
            time.sleep(0.3)
            if self._button_choice_is_selected(el):
                return True
        except Exception:
            pass

        # Native click did not commit.  Try one React-compatible fallback.
        try:
            el.evaluate(_JS_REACT_CLICK)
            time.sleep(0.3)
            return self._button_choice_is_selected(el)
        except Exception:
            try:
                self.page.evaluate(_JS_REACT_CLICK, el)
                time.sleep(0.3)
                return self._button_choice_is_selected(el)
            except Exception:
                return False

    @staticmethod
    def _button_choice_is_selected(el: Any) -> bool:
        """Return whether an Ashby option button, rather than its group, won."""
        try:
            return bool(el.evaluate("""button => {
                const cls = String(button.className || '');
                if (/(^|\\s)(?:active|selected|checked)(?:\\s|$)|_(?:active|selected|checked)_/i.test(cls)) {
                    return true;
                }
                return button.getAttribute('aria-pressed') === 'true'
                    || button.getAttribute('aria-checked') === 'true'
                    || button.dataset.selected === 'true';
            }"""))
        except Exception:
            return False


    def _fill_consent_checkboxes(self) -> int:
        """Check consent checkboxes AND consent radio buttons.

        Ashby uses both input[type='checkbox'] and input[type='radio'] for
        consent acknowledgements (e.g. 'I have read and acknowledge...').
        Both are handled here.
        """
        _CONSENT_KEYWORDS = (
            "agree", "consent", "privacy", "terms", "acknowledge",
            "confirm", "certify", "have read", "i accept",
        )
        filled = 0
        # Checkboxes
        try:
            boxes = self.page.query_selector_all("input[type='checkbox']")
        except Exception:
            boxes = []
        for box in boxes:
            try:
                if box.is_checked():
                    continue
                blob = self._field_blob(box)
                if any(k in blob for k in _CONSENT_KEYWORDS):
                    box.check(force=True)
                    filled += 1
            except Exception:
                continue

        # Radio buttons used as consent toggles (Sentry-style "I have read and
        # acknowledge..." pattern).
        try:
            radios = self.page.query_selector_all("input[type='radio']")
        except Exception:
            radios = []
        for radio in radios:
            try:
                if radio.is_checked():
                    continue
                blob = self._field_blob(radio)
                if any(k in blob for k in _CONSENT_KEYWORDS):
                    radio.check(force=True)
                    filled += 1
            except Exception:
                continue

        if not filled:
            # Some Ashby forms render the acknowledgement as a custom control
            # whose input has no useful name or label association.
            for pattern in (
                r"I confirm I have read the above",
                r"I have read and acknowledge",
                r"I have read.*privacy",
                r"I agree.*terms",
            ):
                try:
                    label = self.page.get_by_text(
                        re.compile(pattern, re.I)
                    ).last
                    if label.count() and self._visible(label):
                        self._safe_click(label, force=True)
                        filled += 1
                        break
                except Exception:
                    pass
        return filled

    def _fill_checkbox_groups(self) -> int:
        """Answer required, non-consent Ashby multi-select checkbox questions.

        Ashby represents multi-select custom questions as a field-entry
        container of labelled checkboxes.  They are distinct from the
        voluntary EEO survey and consent acknowledgements, neither of which
        should be inferred by this generic handler.
        """
        filled = 0
        consent_words = (
            "agree", "consent", "privacy", "terms", "acknowledge",
            "confirm", "certify", "have read", "i accept",
        )
        try:
            containers = self.page.query_selector_all("[data-field-entry-id]") or []
        except Exception:
            return 0
        for container in containers:
            try:
                if not self._visible(container):
                    continue
                boxes = container.query_selector_all("input[type='checkbox']") or []
                if len(boxes) < 2 or any(box.is_checked() for box in boxes):
                    continue
                question = self._ashby_question_text(boxes[0])
                if not question:
                    title = container.query_selector("label[class*='required'], label, legend")
                    question = (title or container).inner_text() or ""
                q_lower = question.lower()
                # Only act on explicitly required custom questions.  This
                # avoids voluntary demographic/self-identification fields.
                required = bool(container.query_selector(
                    "label[class*='required'], [aria-required='true'], input[required]"
                ))
                if not required or any(word in q_lower for word in consent_words):
                    continue
                option_labels = [
                    (self._label_text_for(box) or box.get_attribute("name") or "").strip()
                    for box in boxes
                ]
                if not all(option_labels):
                    continue
                prefs = self._resolve_for_field(
                    question,
                    profile=self.profile,
                    options=option_labels,
                    job_context=self.job_context,
                    hint=_ats_ai_hint(question, option_labels),
                    required=True,
                ) or []
                selected = 0
                for pref in prefs:
                    pref_lower = str(pref).strip().lower()
                    for box, label in zip(boxes, option_labels):
                        label_lower = label.lower()
                        if pref_lower == label_lower or pref_lower in label_lower or label_lower in pref_lower:
                            if not box.is_checked():
                                box.check(force=True)
                                selected += 1
                            break
                if selected:
                    self._log(f"checkbox-group: {question[:50]!r} → {selected} selection(s)")
                    filled += selected
            except Exception:
                continue
        return filled

    def _fill_dropdowns(self) -> int:
        """Fill Ashby dropdown/select elements."""
        filled = 0
        try:
            selects = self.page.query_selector_all("select")
        except Exception:
            return 0
        for sel in selects:
            blob = self._field_blob(sel)
            try:
                opts = sel.evaluate(
                    "el => Array.from(el.options).map(o => (o.text||'').trim()).filter(Boolean)"
                ) or []
            except Exception:
                opts = []
            if not opts:
                continue
            # Check if already has value
            try:
                idx = sel.evaluate("el => el.selectedIndex")
                if idx > 0:
                    text = sel.evaluate("el => el.options[el.selectedIndex]?.text || ''")
                    if text and text.strip().lower() not in {"select...", "select", "please select", ""}:
                        continue
            except Exception:
                pass
            prefs = self._resolve_for_field(
                self._visible_question_text(sel) or blob,
                profile=self.profile,
                options=list(opts),
                job_context=self.job_context,
                hint=_ats_ai_hint(self._visible_question_text(sel) or blob, list(opts)),
            )
            if prefs and self._select_native(sel, prefs):
                filled += 1
        return filled

    def _fill_free_text_questions(self) -> int:
        """Fill free-text questions via shared brain."""
        filled = 0
        try:
            for el in self.page.query_selector_all("textarea"):
                try:
                    if not self._visible(el):
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
                interesting = (
                    "?" in blob or "?" in q
                    or len(q) >= 15
                    or any(k in blob for k in (
                        "why", "tell us", "describe", "cover",
                        "motivation", "additional", "comment",
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
    def _ashby_question_text(el: Any) -> str:
        """Read the question heading from an Ashby field-entry container.

        Native labels on radio and checkbox inputs identify options, while
        Ashby keeps the actual question on the surrounding
        ``data-field-entry-id`` container.  Reading that live DOM structure
        gives the answer resolver the context it needs without relying on
        fragile generated class names.
        """
        try:
            text = el.evaluate(r"""node => {
                const field = node.closest('[data-field-entry-id]');
                if (!field) return '';
                const heading = field.querySelector(
                    'label.ashby-application-form-question-title, legend, label[class*="required"], label'
                );
                const description = field.querySelector(
                    '.ashby-application-form-question-description, [class*="description"]'
                );
                return [heading, description]
                    .filter(Boolean)
                    .map(item => (item.innerText || item.textContent || '').trim())
                    .filter(Boolean)
                    .join(' ')
                    .slice(0, 1800);
            }""") or ""
            return str(text).strip()
        except Exception:
            return ""

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
