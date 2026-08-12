"""Greenhouse ATS adapter.

Handles:
  * React ``role=combobox`` inputs (not <select>)
  * Location typeahead (async city options)
  * Embedded iframes on company wrapper pages
  * Email verification codes via IMAP
  * Re-render drops file attachments (skip location widgets on retry)
  * "Attach" buttons open native OS file dialog — NEVER click them
"""
from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from ..base import ATSAdapter
from ..mixins.upload import UploadMixin
from ..mixins.captcha import CaptchaMixin
from ..mixins.questions import QuestionsMixin, _clean_question_text, _ats_ai_hint, _should_use_ai
from ..mixins.fields import FieldsMixin
from ..mixins.verification import VerificationMixin
from ..types import FillStats


class GreenhouseAdapter(
    UploadMixin,
    CaptchaMixin,
    QuestionsMixin,
    FieldsMixin,
    VerificationMixin,
    ATSAdapter,
):
    platform_name = "greenhouse"

    def __init__(self) -> None:
        self.page: Any = None
        self.profile: dict[str, Any] = {}
        self.job_title = ""
        self.job_company = ""
        self.job_context = ""
        self._skip_location_widgets = False
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
            if host in {"grnh.se", "gh.io"}:
                return True
            return bool(re.search(
                r"(?:^|\.)(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io|greenhouse\.io)(?:/|$)",
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

        # Navigate to the application form if needed
        self._open_application_form()
        self._dismiss_overlays()
        # Overnight shortlist: LawZero / Aspect / Eucalyptus landed on
        # job-boards.greenhouse.io/{slug}?error=true (closed board).
        url_low = (self.page.url or "").lower()
        if "error=true" in url_low:
            self._log(f"Greenhouse board error page: {self.page.url[:160]}")

    def authenticate(self) -> bool:
        """Greenhouse uses email verification codes — handled post-submit."""
        return True

    def upload_documents(self, **kwargs: Any) -> dict[str, bool]:
        resume_path = kwargs.get("resume_path") or (self.profile.get("resume_path") or "").strip()
        cover_path = kwargs.get("cover_letter_path") or (self.profile.get("cover_letter_path") or "").strip()
        return UploadMixin.upload_documents(
            self, resume_path=resume_path, cover_letter_path=cover_path
        )

    def fill_application(self) -> FillStats:
        return self._fill_known_fields()

    def answer_questions(self) -> int:
        """Answer custom questions via combobox/radio/checkbox handlers."""
        answered = 0
        # Combobox selects
        answered += self._fill_combobox_questions()
        # Radio groups
        answered += self._fill_radio_groups()
        # Checkbox groups (location, contact prefs)
        answered += self._fill_checkbox_groups()
        # Consent checkboxes
        answered += self._fill_consent_checkboxes()
        # Free-text custom questions
        answered += self._fill_free_text_questions()
        # Native selects
        answered += self.fill_native_selects(
            self.profile, job_context=self.job_context, portal="greenhouse"
        )
        # Salary / compensation required fields (AvePoint-style post-OTP forms).
        answered += self._fill_salary_expectations()
        return answered

    def repair_required_fields(self) -> int:
        """Re-fill salary + empty required free-text after OTP/partial submit."""
        n = 0
        n += self._fill_salary_expectations()
        try:
            n += self._fill_free_text_questions()
        except Exception:
            pass
        try:
            n += self._fill_consent_checkboxes()
        except Exception:
            pass
        return n

    def _fill_salary_expectations(self) -> int:
        """Fill salary/compensation expectation inputs from profile or posting band."""
        filled = 0
        desired = str(
            self.profile.get("desired_salary")
            or self.profile.get("salary")
            or self.profile.get("desired_pay")
            or "70000"
        ).strip()
        # Prefer midpoint of a displayed CAD/USD range on the page.
        try:
            page_text = self._page_text(8000)
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
                desired = str(int((low + high) / 2))
        except Exception:
            pass
        try:
            fields = self.page.query_selector_all("input, textarea") or []
        except Exception:
            return 0
        for el in fields:
            try:
                if not self._visible(el):
                    continue
                if self._is_combobox(el):
                    continue
                typ = (el.get_attribute("type") or "text").lower()
                if typ in {"hidden", "submit", "button", "file", "checkbox", "radio"}:
                    continue
                blob = (self._field_blob(el) + " " + (self._visible_question_text(el) or "")).lower()
                if not any(
                    k in blob
                    for k in (
                        "salary", "compensation", "pay expectation",
                        "desired pay", "expected pay", "annual pay",
                        "wage expectation", "salary expectation",
                    )
                ):
                    continue
                try:
                    existing = (el.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and existing.lower() not in {"", "n/a", "missed"}:
                    continue
                if self._fill_input(el, desired):
                    filled += 1
                    self._log(f"salary expectation → {desired!r}")
            except Exception:
                continue
        return filled

    def submit(self) -> bool:
        try:
            self.repair_required_fields()
        except Exception:
            pass
        return self._submit()

    def verify_submission(self) -> str | None:
        """Page-primary confirmation; email OTP is only a gate, not the win.

        Greenhouse confirmation often lands on a ``.../confirmation`` URL or
        thank-you card. IMAP application-receipt mail is secondary and is not
        required once the page shows success. OTP codes still block submit
        until entered — then we re-check the page.
        """
        return self._page_success()

    # ── form opening ──────────────────────────────────────────────────

    def _open_application_form(self) -> None:
        """Navigate to the actual GH application form if on a wrapper page."""
        url = (self.page.url or "").lower()
        if self.detect(self.page.url):
            return
        if "/apply" in url and "greenhouse" in url:
            return

        # Company wrapper pages usually embed GH iframe
        gh_iframe = self._detect_embedded_gh_iframe()
        if gh_iframe:
            self._log(f"Detected embedded GH iframe, navigating to: {gh_iframe[:160]}")
            self.page.goto(gh_iframe, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1.5)
            return

        # Fallback: click wrapper Apply button
        new_url = self._click_wrapper_apply_button()
        if new_url:
            self._log(f"Clicked wrapper apply button, now at: {new_url[:160]}")
            gh_iframe2 = self._detect_embedded_gh_iframe(wait_seconds=4.0)
            if gh_iframe2:
                self.page.goto(gh_iframe2, wait_until="domcontentloaded", timeout=30000)
                time.sleep(1.5)
            return

        if "/apply" in url:
            return
        # GH new boards often embed the form on the same page
        if self.page.query_selector(
            "#first_name, #application-form, form#application-form, "
            "form.postings-form, #resume-upload-input, input[name='resume']"
        ):
            return

        # Click Apply link
        for sel in (
            "a[href*='/apply']",
            "a.postings-btn",
            "a[data-qa='btn-apply']",
            "button[data-qa='btn-apply']",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply for this job')",
            "a:has-text('Apply now')",
            "button:has-text('Apply now')",
        ):
            try:
                el = self.page.query_selector(sel)
                if not self._visible(el):
                    continue
                href = (el.get_attribute("href") or "").strip()
                if href and href.startswith("http") and "/apply" in href:
                    self.page.goto(href, wait_until="domcontentloaded", timeout=20000)
                else:
                    self._safe_click(el, force=True)
                time.sleep(1.0)
                break
            except Exception:
                continue
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

    def _detect_embedded_gh_iframe(self, *, wait_seconds: float = 6.0) -> str | None:
        """Return URL of embedded GH application iframe."""
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            try:
                for fr in self.page.frames:
                    if fr == self.page.main_frame:
                        continue
                    url = (fr.url or "").lower()
                    if "/embed/job_app" in url or "job-boards.greenhouse.io" in url:
                        return fr.url
                for node in self.page.query_selector_all("iframe"):
                    src = (node.get_attribute("src") or "").lower()
                    if "/embed/job_app" in src or "job-boards.greenhouse.io" in src:
                        return node.get_attribute("src")
            except Exception:
                pass
            if time.time() + 0.5 < deadline:
                time.sleep(0.5)
        return None

    def _click_wrapper_apply_button(self) -> str | None:
        """Click wrapper Apply button and return resulting URL."""
        selectors = [
            "a:has-text('Apply now')", "a:has-text('Apply Now')",
            "a:has-text('Apply')", "button:has-text('Apply now')",
            "button:has-text('Apply Now')", "button:has-text('Apply')",
            "[data-testid*='apply']", "a[href*='greenhouse']",
            "a[href*='/jobs/apply']", "a[href*='/apply']",
        ]
        original = self.page.url
        for sel in selectors:
            try:
                el = self.page.query_selector(sel)
                if not el or not el.is_visible():
                    continue
                href = (el.get_attribute("href") or "").strip()
                if href and ("greenhouse" in href.lower() or "/apply" in href.lower()):
                    self.page.goto(href, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(1.0)
                    return self.page.url
                self._safe_click(el, force=True)
                time.sleep(1.5)
                if self.page.url != original:
                    return self.page.url
            except Exception:
                continue
        return None

    # ── field filling ─────────────────────────────────────────────────

    def _fill_known_fields(self) -> FillStats:
        """Fill standard fields using direct selectors + brain for comboboxes."""
        stats = self.fill_standard_fields(
            self.profile, skip_location_widgets=self._skip_location_widgets
        )

        # Location (City)* autocomplete — GH-specific
        if not self._skip_location_widgets:
            for sel in (
                "#candidate-location",
                "input[id='candidate-location']",
                "input[aria-label*='Location (City)' i]",
                "input[aria-label*='Location' i][role='combobox']",
            ):
                try:
                    el = self.page.query_selector(sel)
                    if not el:
                        continue
                    if not self._field_is_empty(el):
                        stats.skipped += 1
                        break
                    loc_prefs = [
                        self.profile.get("location", ""),
                        f"{self.profile.get('city', '')}, {self.profile.get('state', '')}, Canada",
                        f"{self.profile.get('city', '')}, BC, Canada",
                        "Surrey, BC, Canada",
                        "Vancouver, BC, Canada",
                        self.profile.get("city", "Surrey"),
                    ]
                    if self._select_location_autocomplete(el, [p for p in loc_prefs if p]):
                        stats.combobox += 1
                        stats.filled += 1
                        break
                except Exception:
                    continue

            # Country combobox
            for sel in ("#country", "input[id='country']"):
                try:
                    el = self.page.query_selector(sel)
                    if el and (self._is_combobox(el) or self._is_country_field(self._field_blob(el), el)):
                        if not self._field_is_empty(el):
                            stats.skipped += 1
                            break
                        if self._select_combobox(
                            el,
                            ["Canada", "CA", self.profile.get("country", "")],
                            required=True,
                        ):
                            stats.combobox += 1
                            stats.filled += 1
                            break
                except Exception:
                    continue

        # Scan remaining inputs / selects / comboboxes
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
            (("initial", "initials"),
             (self.profile.get("first_name", "L")[:1] + self.profile.get("last_name", "S")[:1]).upper()),
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
                    if not self._is_combobox(el):
                        stats.skipped += 1
                        continue
                blob = self._field_blob(el)
                question = self._visible_question_text(el) or _clean_question_text(blob) or blob

                if "linkedin" in blob and typ == "button":
                    continue

                # For React Select fields, resolve only after the dropdown is
                # opened and its live option labels are known.  Resolving
                # against no options can produce an unrelated profile value
                # (for example a location answer for an AI-tools question).
                prefs = [] if self._is_combobox(el) else self._resolve_for_field(
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

                if self._is_combobox(el):
                    try:
                        if not el.is_visible():
                            continue
                    except Exception:
                        continue
                    if any(k in blob for k in ("iti__", "search-input", "phone country")):
                        continue
                    if not self._field_is_empty(el):
                        stats.skipped += 1
                        continue
                    if self._select_combobox(el, prefs or []):
                        stats.combobox += 1
                        stats.filled += 1
                        try:
                            el.evaluate("e => e.setAttribute('data-ats-filled', '1')")
                        except Exception:
                            pass
                    continue

                value = prefs[0] if prefs else None
                if value is None:
                    for keys, val in mapping:
                        if any(k in blob for k in keys) or any(k in question.lower() for k in keys):
                            value = val
                            break
                # Free-text school entry
                if value and str(value).strip().lower() in {"other", "autre"} and tag in {"input", "textarea"}:
                    q_low = (question or "").lower()
                    if any(k in q_low for k in ("school", "university", "college", "institution", "école", "ecole")):
                        value = self.profile.get("school") or "Kwantlen Polytechnic University"
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

        # Mark location widgets as done for retry pass
        self._skip_location_widgets = True
        return stats

    # ── combobox ──────────────────────────────────────────────────────

    def _fill_combobox_questions(self) -> int:
        """Fill React Select combobox questions via typeahead + option click."""
        answered = 0
        try:
            fields = self.page.query_selector_all("input, textarea, select, [role='combobox']")
        except Exception:
            return 0

        for el in fields:
            if not self._is_combobox(el):
                continue
            try:
                if not el.is_visible():
                    continue
            except Exception:
                continue
            blob = self._field_blob(el)
            if any(k in blob for k in ("iti__", "search-input", "phone country")):
                continue
            if not self._field_is_empty(el):
                continue

            question = self._visible_question_text(el) or _clean_question_text(blob) or blob
            # _select_combobox opens the control first, collects its current
            # options, and then resolves against those exact labels.
            if self._select_combobox(el, [], required=True):
                answered += 1
        return answered

    def _select_combobox(self, el: Any, preferences: list[str],
                         *, required: bool = False) -> bool:
        """Fill Greenhouse/React Select comboboxes via typeahead + option click."""
        prefs = [p for p in preferences if p][:8]
        try:
            if not el.is_visible():
                return False
        except Exception:
            pass
        blob = self._field_blob(el)
        question = self._visible_question_text(el) or _clean_question_text(blob) or blob

        # Location autocomplete
        if any(k in blob for k in ("candidate-location", "location (city", "location(city")):
            return self._select_location_autocomplete(el, prefs + ["Surrey, BC, Canada", "Vancouver, BC"])

        keep_phone = self._is_country_field(blob, el)
        if keep_phone:
            prefs = ["Canada +1", "Canada", "CA"] + list(prefs or [])

        try:
            el.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        # Click parent select control to reliably open dropdown
        clicked = False
        try:
            for selector in (".select__control", ".react-select__control", "control"):
                parent = el.locator(f"xpath=ancestor::*[contains(@class, '{selector}')][1]")
                if parent.count() > 0:
                    parent.first.click(timeout=1500, force=True)
                    clicked = True
                    break
        except Exception:
            pass
        if not clicked:
            try:
                el.click(timeout=1500, force=True)
            except Exception:
                self._safe_click(el, force=True)
        time.sleep(0.4)

        raw_opts = self.page.evaluate(
            """() => Array.from(document.querySelectorAll(
              "[role='listbox'] [role='option'], .select__menu [role='option'], .select__option, [role='option']"
            )).map(o => (o.innerText || o.textContent || '').trim()).filter(Boolean)"""
        ) or []
        option_labels = self._filter_combobox_options(list(raw_opts), keep_phone_codes=keep_phone)

        chosen = None
        for pref in prefs:
            chosen = self._map_pref_to_option(pref, option_labels)
            if chosen:
                break
        if not chosen:
            brain_prefs = self._resolve_for_field(
                question,
                profile=self.profile,
                options=option_labels or None,
                job_context=self.job_context,
                hint=_ats_ai_hint(question, option_labels or None, section_text=blob[:200]),
                required=required,
            ) or []
            if brain_prefs:
                prefs = list(brain_prefs) + [p for p in prefs if p not in brain_prefs]
                for pref in prefs:
                    chosen = self._map_pref_to_option(pref, option_labels) if option_labels else None
                    if chosen:
                        break

        # This profile routinely uses AI tools for coding and documentation.
        # If the general resolver declines the question, choose the exact
        # live option instead of leaving an unrelated profile value in the
        # React Select control.
        if not chosen and option_labels and "ai tools" in question.lower():
            chosen = self._map_pref_to_option("I regularly use AI tools", option_labels)

        if not chosen:
            # Typeahead search to reveal options
            search_token = ""
            q_low = (blob + question).lower()
            if any(k in q_low for k in ("school", "university", "college", "institution")):
                school_token = str(self.profile.get("school_short") or "Kwantlen").strip()
                for pref in [school_token, "Kwantlen", "KPU"] + list(prefs) + ["Other", "Not listed"]:
                    pl = str(pref or "").strip().lower()
                    if pl and not re.fullmatch(r"(19|20)\d{2}", pl):
                        search_token = str(pref).strip().split()[0][:16]
                        break
                search_token = search_token or "Kwantlen"
            elif any(k in q_low for k in ("start date year", "end date year", "start year", "end year", "graduation year")):
                for pref in prefs:
                    m = re.search(r"(19|20)\d{2}", pref or "")
                    if m:
                        search_token = m.group(0)
                        break
            elif prefs:
                token = re.sub(r"[^\w\s\-$]", " ", str(prefs[0])).strip().split()
                if token:
                    search_token = token[0][:16]

            if search_token:
                try:
                    el.fill("")
                    el.type(search_token, delay=15)
                    time.sleep(0.5)
                except Exception:
                    pass
                searched = self.page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                      "[role='listbox'] [role='option'], .select__menu [role='option'], .select__option, [role='option']"
                    )).map(o => (o.innerText || o.textContent || '').trim()).filter(Boolean)"""
                ) or []
                searched_labels = self._filter_combobox_options(list(searched), keep_phone_codes=keep_phone)
                if searched_labels:
                    brain_prefs = self._resolve_for_field(
                        question,
                        profile=self.profile,
                        options=searched_labels,
                        job_context=self.job_context,
                        hint=_ats_ai_hint(question, searched_labels, section_text=blob[:200]),
                        required=required,
                    ) or list(prefs or [])
                    for pref in brain_prefs:
                        chosen = self._map_pref_to_option(pref, searched_labels)
                        if chosen:
                            break
                # Fallback: if we didn't find a match and it's a school dropdown, try searching for "Other" / "Not listed"
                if not chosen and any(k in q_low for k in ("school", "university", "college", "institution")):
                    for alt_token in ("Other", "Not listed"):
                        try:
                            el.fill("")
                            el.type(alt_token, delay=15)
                            time.sleep(0.5)
                            searched = self.page.evaluate(
                                """() => Array.from(document.querySelectorAll(
                                  "[role='listbox'] [role='option'], .select__menu [role='option'], .select__option, [role='option']"
                                )).map(o => (o.innerText || o.textContent || '').trim()).filter(Boolean)"""
                            ) or []
                            searched_labels = self._filter_combobox_options(list(searched), keep_phone_codes=keep_phone)
                            chosen = self._map_pref_to_option(alt_token, searched_labels)
                            if chosen:
                                break
                        except Exception:
                            pass
                if not chosen:
                    try:
                        self.page.keyboard.press("Escape")
                    except Exception:
                        pass

        if not chosen:
            try:
                el.fill("")
            except Exception:
                pass
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            self._log(f"combobox no match for q={question[:60]!r}; opts={option_labels[:8]} required={required}")
            return False

        # Click the chosen option
        return self._click_combobox_option(el, chosen, keep_phone, question)

    def _click_combobox_option(self, el: Any, chosen: str, keep_phone: bool, question: str) -> bool:
        """Click the chosen option in a React Select combobox."""
        clicked = False
        for opt_el in self.page.query_selector_all("[role='option'], .select__option"):
            try:
                txt = (opt_el.inner_text() or "").strip()
            except Exception:
                continue
            if not txt:
                continue
            ch = (chosen or "").strip().lower()
            tl = txt.strip().lower()
            if keep_phone:
                ok = txt == chosen or ch in tl or tl.startswith(ch.split("+")[0].strip())
            else:
                if re.search(r"\+\d", txt):
                    continue
                q_low = (question or "").lower()
                q_school = any(k in q_low for k in ("school", "university", "college", "institution"))
                q_gender = bool(re.search(r"\b(gender|sex|sexe)\b", q_low))
                if q_gender or ch in {"male", "female", "man", "woman", "homme", "femme"}:
                    if ch in {"male", "man", "m", "homme"}:
                        ok = tl in {"male", "man", "m", "homme"} or (
                            re.search(r"(?<![a-z])male(?![a-z])", tl)
                            and "female" not in tl and "trans" not in tl
                        )
                    elif ch in {"female", "woman", "f", "femme"}:
                        ok = tl in {"female", "woman", "f", "femme"} or "female" in tl
                    else:
                        ok = tl == ch
                elif q_school and ch in {"other", "autre", "n/a", "na", "none", "not listed"}:
                    ok = tl == ch or tl in {"other", "autre"} or ("not listed" in ch and "not listed" in tl)
                elif q_school:
                    ok = tl == ch or tl.startswith(ch)
                else:
                    ok = tl == ch or txt == chosen
                    if not ok and len(ch) >= 3:
                        ok = bool(re.search(r"(?<![a-z0-9])" + re.escape(ch) + r"(?![a-z0-9])", tl))
            if ok and self._visible(opt_el):
                self._safe_click(opt_el, force=True)
                clicked = True
                self._force_react_register(el)
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                time.sleep(0.12)
                break

        if not clicked:
            # Typeahead then click
            el.fill("")
            token = re.sub(r"[^\w\s\-$]", " ", chosen).strip().split()[0][:20]
            el.type(token or chosen[:16], delay=15)
            time.sleep(0.35)
            for opt_el in self.page.query_selector_all("[role='option'], .select__option"):
                try:
                    txt = (opt_el.inner_text() or "").strip()
                except Exception:
                    continue
                if not txt:
                    continue
                if not keep_phone and re.search(r"\+\d", txt):
                    continue
                ch = chosen.lower()
                tl = txt.lower()
                typeahead_ok = ch in tl or tl in ch
                if ch in {"male", "man"} and ("female" in tl or "woman" in tl):
                    typeahead_ok = tl in {"male", "man"}
                if typeahead_ok:
                    if self._visible(opt_el):
                        self._safe_click(opt_el, force=True)
                        clicked = True
                        self._force_react_register(el)
                        try:
                            self.page.keyboard.press("Escape")
                        except Exception:
                            pass
                        break
            if not clicked:
                self.page.keyboard.press("Enter")
                clicked = True
                time.sleep(0.15)

        time.sleep(0.25)
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        self._log(f"combobox chose {chosen!r} clicked={clicked}")
        time.sleep(0.3)
        return clicked

    def _force_react_register(self, el: Any) -> None:
        """Force React to register a combobox selection."""
        try:
            self.page.evaluate("""(input) => {
                if (!input) return;
                ['input', 'change', 'blur'].forEach(ev => {
                    input.dispatchEvent(new Event(ev, {bubbles: true}));
                });
                input.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                input.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                try { input.setAttribute('data-ats-filled', '1'); } catch (e) {}
            }""", el)
        except Exception:
            pass

    def _select_location_autocomplete(self, el: Any, preferences: list[str]) -> bool:
        """Greenhouse candidate-location typeahead (async city options)."""
        prefs = [p for p in preferences if p] or []
        prefs = [
            "Surrey, British Columbia, Canada",
            "Surrey, BC, Canada",
            "Vancouver, British Columbia, Canada",
            "Vancouver, BC, Canada",
            *prefs,
            "Burnaby, BC, Canada",
        ]
        seen, clean = set(), []
        for p in prefs:
            k = p.lower()
            if k in seen:
                continue
            seen.add(k)
            clean.append(p)
        prefs = clean[:5]
        try:
            if not el.is_visible():
                return False
        except Exception:
            pass
        try:
            el.scroll_into_view_if_needed(timeout=1000)
            el.click(timeout=1500, force=True)
        except Exception:
            self._safe_click(el, force=True)
        for pref in prefs:
            try:
                el.fill("")
                el.type(str(pref), delay=18)
                time.sleep(1.2)
                opts = self.page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                      "[role='listbox'] [role='option'], .select__menu [role='option'], .select__option, [role='option'], .pac-item"
                    )).map(o => (o.innerText || o.textContent || '').trim()).filter(Boolean)"""
                ) or []
                opts = [o for o in opts if o and o.lower() not in {"select...", "select"}]
                ranked = []
                for o in opts:
                    ol = o.lower()
                    score = 0
                    if "canada" in ol or "bc" in ol or "british columbia" in ol:
                        score += 10
                    if "surrey" in ol and ("bc" in ol or "british" in ol or "canada" in ol):
                        score += 20
                    if "vancouver" in ol and ("bc" in ol or "canada" in ol or "british" in ol):
                        score += 15
                    if any(x in ol for x in ("united kingdom", "uk", "england", "surrey heath")):
                        score -= 50
                    if "united states" in ol or ", us" in ol or " usa" in ol:
                        score -= 5
                    ranked.append((score, o))
                ranked.sort(key=lambda x: x[0], reverse=True)
                if not ranked or ranked[0][0] < 0:
                    self.page.keyboard.press("Escape")
                    time.sleep(0.1)
                    continue
                target = ranked[0][1]
                for opt_el in self.page.query_selector_all("[role='option'], .select__option, .pac-item"):
                    try:
                        txt = (opt_el.inner_text() or "").strip()
                    except Exception:
                        continue
                    if txt == target or target.lower() in txt.lower():
                        if self._visible(opt_el):
                            self._safe_click(opt_el, force=True)
                            time.sleep(0.25)
                            self._log(f"location autocomplete chose {txt!r}")
                            return True
                self.page.keyboard.press("ArrowDown")
                self.page.keyboard.press("Enter")
                time.sleep(0.25)
                try:
                    val = (el.input_value() or "").strip()
                except Exception:
                    val = ""
                if val and "united kingdom" not in val.lower() and "heath" not in val.lower() and "england" not in val.lower():
                    self._log(f"location autocomplete set → {val!r}")
                    return True
                # ArrowDown+Enter committed a wrong value (UK/England) — clear
                # the field and try the next preference.
                if val:
                    self._log(f"location autocomplete rejected bad value {val!r}, clearing")
                    try:
                        el.fill("")
                        self.page.keyboard.press("Escape")
                        time.sleep(0.2)
                    except Exception:
                        pass
            except Exception as exc:
                self._log(f"location autocomplete '{pref}' failed: {exc}")
                continue
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    # ── radio / checkbox ──────────────────────────────────────────────

    def _fill_radio_groups(self) -> int:
        """Fill radio button groups via shared brain."""
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
            first_radio = group_radios[0]
            val_options = [(r.get_attribute("value") or "").strip() for r in group_radios]
            context_label = self._question_text_from_group(first_radio, val_options)
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
                    self._log(f"radio chose '{prefs[0]}' for group {group_name[:25]}...")
                except Exception:
                    pass
        return answered

    def _fill_checkbox_groups(self) -> int:
        """Fill Greenhouse multi-checkbox groups (locations, contact prefs)."""
        filled = 0
        metro_yes = {
            "vancouver, bc", "vancouver", "surrey, bc",
            "tous les emplacements / all locations", "all locations", "tous les emplacements",
        }
        contact_yes = {"by email.", "by email", "by phone.", "by phone"}

        try:
            descriptors = self.page.evaluate("""() => {
                const out = [];
                for (const [index, cb] of Array.from(document.querySelectorAll('input[type=checkbox]')).entries()) {
                    if (cb.disabled) continue;
                    const labelEl = cb.closest('label')
                      || (cb.id && document.querySelector('label[for="' + CSS.escape(cb.id) + '"]'));
                    let label = (labelEl?.innerText || '').trim();
                    if (!label) {
                        const parentText = (cb.parentElement?.innerText || '').trim();
                        label = parentText.split('\\n').map(s => s.trim()).filter(Boolean)[0] || parentText;
                    }
                    label = label.replace(/\\s+/g, ' ').slice(0, 160);
                    const value = (cb.value || '').trim();
                    const container = cb.closest(
                      'fieldset, .field, .application-field, .form-group, [class*="field"], li, .question, div'
                    ) || cb.parentElement;
                    const sectionText = (container?.innerText || '').trim().slice(0, 320);
                    const blob = (sectionText + ' ' + label + ' ' + value).toLowerCase();
                    const isCityGroup = /ville|city|location|emplacement|vancouver|calgary|edmonton|toronto|montreal|halifax|ottawa|quebec/i.test(blob);
                    const isContactGroup = /by email|by phone|like to be contacted|further communications|receive further|contacted to receive/i.test(blob);
                    out.push({
                        index, value, label, checked: !!cb.checked,
                        isCityGroup, isContactGroup, sectionText: sectionText.slice(0, 200)
                    });
                }
                return out;
            }""") or []
            elements = self.page.query_selector_all("input[type=checkbox]")
        except Exception:
            descriptors, elements = [], []

        for desc in descriptors:
            if desc.get("checked"):
                continue
            label = (desc.get("label") or "").strip()
            value = (desc.get("value") or "").strip()
            question = _clean_question_text(label) or label or value
            if not question:
                continue
            if any(k in question.lower() for k in ("agree", "privacy policy", "terms of", "certify that")):
                continue
            q_low = question.lower().strip()
            is_city = bool(desc.get("isCityGroup")) or bool(
                re.match(r"^[a-z .'\-]+,?\s*[a-z]{2}$", q_low)
            ) or "emplacement" in q_low or "all location" in q_low
            is_contact = bool(desc.get("isContactGroup")) or q_low in contact_yes
            if not (is_city or is_contact):
                continue

            prefs = self._resolve_for_field(
                question,
                profile=self.profile,
                options=["Yes", "No"],
                job_context=self.job_context,
                hint=_ats_ai_hint(question, ["Yes", "No"], section_text=(desc.get("sectionText") or "")[:200]),
                required=True,
            ) or []
            want_yes = bool(prefs) and any(
                str(p).strip().lower() in {"yes", "true", "y", "1"}
                or str(p).strip().lower().startswith("yes")
                for p in prefs
            )
            if q_low in contact_yes or q_low in metro_yes:
                want_yes = True
            if not want_yes:
                continue

            idx = int(desc.get("index", -1))
            el = elements[idx] if 0 <= idx < len(elements) else None
            ok = False
            if el is not None:
                ok = self._check_input_via_js(el)
            if not ok:
                ok = self._click_checkbox_by_label_text(question)
            if not ok and value:
                ok = self._click_checkbox_by_label_text(value)
            if ok:
                filled += 1

        # Fallback for known required labels
        for known in ("Tous les emplacements / All locations", "Vancouver, BC", "by email.", "by phone."):
            try:
                already = self.page.evaluate(
                    """(needle) => {
                      const n = (needle || '').toLowerCase();
                      for (const cb of document.querySelectorAll('input[type=checkbox]')) {
                        if (!cb.checked) continue;
                        const t = ((cb.closest('label')?.innerText || cb.parentElement?.innerText || '') + ' ' + (cb.value||'')).toLowerCase();
                        if (t.includes(n.slice(0, 20))) return true;
                      }
                      return false;
                    }""",
                    known,
                )
                if already:
                    continue
            except Exception:
                pass
            if self._click_checkbox_by_label_text(known):
                filled += 1

        return filled

    def _fill_consent_checkboxes(self) -> int:
        """Fill consent/agreement checkboxes."""
        filled = 0
        try:
            boxes = self.page.query_selector_all("input[type='checkbox']")
        except Exception:
            return 0
        for box in boxes:
            try:
                if box.is_checked():
                    continue
                is_req = box.get_attribute("required") is not None or box.get_attribute("aria-required") == "true"
                blob = self._field_blob(box)
                if is_req or any(k in blob for k in ("agree", "consent", "privacy", "terms", "acknowledge", "confirm", "certify", "gdpr", "demographic")):
                    box.check(force=True)
                    filled += 1
            except Exception:
                continue
        return filled

    def _fill_free_text_questions(self) -> int:
        """Fill free-text custom questions via shared brain."""
        filled = 0
        try:
            for el in self.page.query_selector_all("input[type='text'], input:not([type]), textarea"):
                try:
                    elem_id = (el.get_attribute("id") or "").lower()
                    elem_name = (el.get_attribute("name") or "").lower()
                    if any(k in elem_id or k in elem_name for k in ("security-input", "verification", "otp_code", "one-time-code")):
                        continue
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
                if any(k in q.lower() for k in ("security code", "verification code", "security", "passcode", "otp")):
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
                    self._log(f"free-text via brain: {q[:60]!r}")
        except Exception:
            pass
        return filled

    # ── submission ────────────────────────────────────────────────────

    def _submit(self) -> bool:
        self._dismiss_overlays()
        try:
            self.page.evaluate("""() => {
                const checkedNames = new Set();
                document.querySelectorAll("input[type='checkbox']:checked").forEach(cb => {
                    if (cb.name) checkedNames.add(cb.name);
                });
                document.querySelectorAll("input[type='checkbox'][required], input[type='checkbox'][aria-required='true']").forEach(cb => {
                    if (cb.name && checkedNames.has(cb.name) && !cb.checked) {
                        cb.removeAttribute('required');
                        cb.removeAttribute('aria-required');
                    }
                });
            }""")
        except Exception:
            pass
        try:
            for cb in self.page.query_selector_all("input[type='checkbox'][required], input[type='checkbox'][aria-required='true']"):
                try:
                    if not cb.is_checked():
                        cb.check(force=True)
                except Exception:
                    pass
        except Exception:
            pass
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

    def _page_success(self) -> str | None:
        """Require on-page confirmation text/URL/banner — not form-gone alone."""
        from ..confirmation import classify_page_confirmation

        self.confirmation_evidence = ""
        url = self.page.url or ""
        text = self._page_text(24000) or ""
        status, evidence = classify_page_confirmation(
            url, text, page=self.page, platform="greenhouse",
        )
        if evidence:
            self.confirmation_evidence = evidence
        return status

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

    # ── helpers ───────────────────────────────────────────────────────

    def _is_country_field(self, blob: str, el: Any = None) -> bool:
        b = (blob or "").lower()
        eid = ""
        try:
            if el is not None:
                eid = (el.get_attribute("id") or "").lower()
        except Exception:
            eid = ""
        if eid in {"country", "country_id", "country-code"}:
            return True
        if re.search(r"\bcountry\*?\b", b) and len(b) < 80 and not any(
            k in b for k in ("eligible", "entitled", "authorized", "work in", "live in", "based")
        ):
            return True
        return False

    def _field_is_empty(self, el: Any) -> bool:
        """Check if a field is empty (handles React Select chips)."""
        try:
            if (el.get_attribute("data-ats-filled") or "").lower() in {"1", "true", "yes"}:
                return False
        except Exception:
            pass
        try:
            tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
        except Exception:
            tag = ""
        try:
            typ = (el.get_attribute("type") or "text").lower()
        except Exception:
            typ = "text"
        if typ == "file":
            return not self._file_input_has_files(el)
        if typ in {"checkbox", "radio"}:
            try:
                return not bool(el.is_checked())
            except Exception:
                return True
        if tag == "select":
            try:
                val = (el.input_value() or "").strip()
                text = el.evaluate(
                    "el => (el.options[el.selectedIndex] && (el.options[el.selectedIndex].text||'')) || ''"
                ) or ""
                t = (text or val or "").strip().lower()
                return t in {"", "select...", "select", "please select", "choose", "n/a"}
            except Exception:
                return True
        # Combobox (React Select)
        role = (el.get_attribute("role") or "").lower()
        cls = (el.get_attribute("class") or "").lower()
        if role == "combobox" or "select__input" in cls or "react-select" in cls:
            try:
                has_chip = el.evaluate("""(node) => {
                    const parent = node.closest('.select__control, .react-select__control, [class*="control"]');
                    if (!parent) return false;
                    const selectors = [
                        '.select__single-value', '.react-select__single-value',
                        '[class$="-single-value"]',
                        '.select__value-container [class*="single"]',
                        '.Select-value', '.select__multi-value__label', 'input + span',
                    ];
                    for (const sel of selectors) {
                        const el = parent.querySelector(sel);
                        if (el) {
                            const txt = (el.textContent || '').trim();
                            if (txt && txt.toLowerCase() !== 'select...' && txt !== '') return true;
                        }
                    }
                    if (node.getAttribute('aria-activedescendant') || node.getAttribute('data-selected')) return true;
                    const chips = parent.querySelectorAll('[class*="multi-value"], [class*="chip"], [class*="tag"]');
                    for (const chip of chips) {
                        const t = (chip.textContent || '').trim();
                        if (t && t.toLowerCase() !== 'select...') return true;
                    }
                    return false;
                }""")
                if has_chip:
                    return False
            except Exception:
                pass
        if "select2-hidden-accessible" in cls:
            try:
                has_value = el.evaluate("""(node) => {
                    const idx = node.selectedIndex;
                    if (idx >= 0 && node.options[idx]) {
                        const t = node.options[idx].text.trim().toLowerCase();
                        return t && !['select...', 'select', 'please select', ''].includes(t);
                    }
                    return false;
                }""")
                return not has_value
            except Exception:
                return True
        try:
            val = (el.input_value() or "").strip()
        except Exception:
            val = ""
        if val and val.lower() not in {"", "select...", "select"}:
            return False
        return True
