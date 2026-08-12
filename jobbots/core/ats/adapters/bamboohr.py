"""BambooHR ATS adapter.

Handles:
  * Form at ``{company}.bamboohr.com/careers/{id}``
  * Standard HTML form fields (simpler than Greenhouse/Ashby)
  * Resume/cover letter uploads
  * EEO (Equal Employment Opportunity) section
  * Questionnaires
  * CAPTCHA support
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


class BambooHRAdapter(
    UploadMixin,
    CaptchaMixin,
    QuestionsMixin,
    FieldsMixin,
    VerificationMixin,
    ATSAdapter,
):
    platform_name = "bamboohr"

    def __init__(self) -> None:
        self.page: Any = None
        self.profile: dict[str, Any] = {}
        self.job_title = ""
        self.job_company = ""
        self.job_context = ""
        # Human-readable proof captured from the rendered confirmation page.
        # A successful click alone is never proof of submission.
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
            return bool(re.search(r"(?:^|\.)bamboohr\.com(?:/|$)", host + "/", re.I))
        except Exception:
            return False

    @classmethod
    def detect_from_page(cls, page: Any) -> bool:
        if cls.detect(getattr(page, "url", "") or ""):
            return True
        try:
            html = (page.content() or "")[:12000].lower()
            return any(m in html for m in (
                "bamboohr.com", "bamboohr-embedded", "bamboohr_application",
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

        # Wait for BambooHR React SPA to render (avoiding empty-render early exits)
        try:
            self.page.wait_for_selector(
                "button:has-text('Apply'), a:has-text('Apply'), form, input[type='email']",
                timeout=12000
            )
        except Exception:
            pass

        # BambooHR may need to click "Apply" on job detail page
        self._open_application_form()
        self._dismiss_overlays()

    def authenticate(self) -> bool:
        return True

    def upload_documents(self, **kwargs: Any) -> dict[str, bool]:
        resume_path = kwargs.get("resume_path") or (self.profile.get("resume_path") or "").strip()
        cover_path = kwargs.get("cover_letter_path") or (self.profile.get("cover_letter_path") or "").strip()
        return UploadMixin.upload_documents(
            self, resume_path=resume_path, cover_letter_path=cover_path
        )

    def _file_input_kind(self, el: Any) -> str:
        """Classify BambooHR upload slots without leaking text from sibling slots."""
        # Prefer the standard id/name classifier whenever Bamboo provides one.
        # The fallback below is for current BambooHR React inputs, which often
        # have neither id nor name.
        kind = super()._file_input_kind(el)
        if kind != "other":
            return kind
        try:
            slot_text = el.evaluate(
                """node => {
                    let p = node.parentElement;
                    for (let i = 0; p && i < 7; i++, p = p.parentElement) {
                      // Stop at the first local upload wrapper. Walking to
                      // the form can include both "Cover Letter" and
                      // "Resume", which routes the resume to the wrong slot.
                      if (p.querySelectorAll('input[type="file"]').length !== 1) continue;
                      const t = (p.innerText || p.textContent || '').trim();
                      if (t && t.length < 300) return t;
                    }
                    return '';
                }"""
            ) or ""
        except Exception:
            slot_text = ""
        low = (self._label_text_for(el) + " " + slot_text).lower()
        if "cover" in low and "resume" not in low and "cv" not in low:
            return "cover"
        if "resume" in low or "cv" in low:
            return "resume"
        return "other"

    def reupload_resume_if_needed(self) -> bool:
        """Restore a dropped required resume before BambooHR retry submits."""
        if self._page_has_resume_file():
            return False
        return self.force_reupload_resume(self.profile)

    def fill_application(self) -> FillStats:
        stats = self.fill_standard_fields(self.profile)
        stats.merge(self._fill_bamboohr_fields())
        return stats

    def answer_questions(self) -> int:
        answered = 0
        # Custom questions
        self._log("  [BAMBOOHR] Starting _fill_custom_questions()")
        answered += self._fill_custom_questions()
        # Radio groups
        self._log("  [BAMBOOHR] Starting _fill_radio_groups()")
        answered += self._fill_radio_groups()
        # BambooHR's current React form renders Province as a button-backed
        # menu, not a native <select>.  Commit the live option before submit.
        self._log("  [BAMBOOHR] Starting _fill_custom_dropdowns()")
        answered += self._fill_custom_dropdowns()
        # Checkboxes
        self._log("  [BAMBOOHR] Starting _fill_consent_checkboxes()")
        answered += self._fill_consent_checkboxes()
        # EEO section
        self._log("  [BAMBOOHR] Starting _fill_eeo_section()")
        answered += self._fill_eeo_section()
        # Dropdowns
        self._log("  [BAMBOOHR] Starting _fill_dropdowns()")
        answered += self._fill_dropdowns()
        # Free-text
        self._log("  [BAMBOOHR] Starting _fill_free_text_questions()")
        answered += self._fill_free_text_questions()
        # Final hard pass — Country/Province are the #1 Bamboo fail mode.
        self._log("  [BAMBOOHR] Starting repair_required_fields()")
        answered += self.repair_required_fields()
        self._log("  [BAMBOOHR] Finished answer_questions()")
        return answered

    def repair_required_fields(self) -> int:
        """Ensure Country + Province (and other required selects) are committed.

        Overnight shortlist: Creator Bamboo failed with visible
        "Please fill in this field / Please make a selection" on Province
        and Country.  Called before each submit attempt.
        """
        filled = 0
        self._log("  [BAMBOOHR] Starting _fill_country_province() in repair_required_fields()")
        filled += self._fill_country_province()
        self._log("  [BAMBOOHR] Starting _fill_custom_dropdowns() in repair_required_fields()")
        filled += self._fill_custom_dropdowns()
        self._log("  [BAMBOOHR] Starting _fill_native_country_province_selects() in repair_required_fields()")
        filled += self._fill_native_country_province_selects()
        self._log("  [BAMBOOHR] Finished repair_required_fields()")
        return filled

    def _profile_country(self) -> str:
        return str(self.profile.get("country") or "Canada").strip() or "Canada"

    def _profile_province(self) -> str:
        state = str(self.profile.get("state") or "").strip()
        up = state.upper().replace(".", "")
        if up in {"BC", "B C", "BRITISH COLUMBIA"}:
            return "British Columbia"
        if not state:
            return "British Columbia"
        # Expand common CA abbreviations when the menu uses full names.
        _CA = {
            "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
            "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
            "NS": "Nova Scotia", "NT": "Northwest Territories",
            "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island",
            "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
        }
        return _CA.get(up, state)

    def _fill_native_country_province_selects(self) -> int:
        """Fill native <select> Country/Province controls when present."""
        filled = 0
        try:
            selects = self.page.query_selector_all("select") or []
        except Exception:
            return 0
        for sel in selects:
            try:
                if not self._visible(sel) and not self._is_combobox(sel):
                    # Bamboo sometimes keeps the real select off-screen but still required.
                    pass
                blob = (self._field_blob(sel) + " " + (self._visible_question_text(sel) or "")).lower()
                label = (self._label_text_for(sel) or "").lower()
                target = blob + " " + label
                if "country" in target:
                    prefs = [self._profile_country(), "Canada", "CA"]
                elif "province" in target or "state" in target or "region" in target:
                    prefs = [self._profile_province(), "British Columbia", "BC", "B.C."]
                else:
                    continue
                if self._select_native(sel, prefs):
                    filled += 1
                    self._log(f"native select committed: {target[:40]!r} → {prefs[0]!r}")
            except Exception:
                continue
        return filled

    def _fill_country_province(self) -> int:
        """Locate Country/Province by label text and commit profile values.

        Bamboo React menus do not always expose aria-label='Select Country'.
        Matching the visible field label is more reliable than button text alone.
        """
        filled = 0
        targets = (
            ("country", [self._profile_country(), "Canada", "CA"]),
            ("province", [self._profile_province(), "British Columbia", "BC"]),
            ("state", [self._profile_province(), "British Columbia", "BC"]),
        )
        for kind, prefs in targets:
            try:
                self._log(f"  [BAMBOOHR] Calling _commit_labeled_menu({kind!r})")
                committed = self._commit_labeled_menu(kind, prefs)
                self._log(f"  [BAMBOOHR] _commit_labeled_menu({kind!r}) returned {committed}")
                if committed:
                    filled += 1
            except Exception as exc:
                self._log(f"country/province ({kind}) error: {exc}")
        return filled

    def _commit_labeled_menu(self, kind: str, prefs: list[str]) -> bool:
        """Open the Country/Province control next to its label and pick a value."""
        kind_low = kind.lower()
        self._log(f"  [BAMBOOHR] [_commit_labeled_menu] kind={kind_low}")
        # 1) Native selects by name/id/label.
        try:
            for sel in self.page.query_selector_all("select") or []:
                blob = " ".join(
                    str(sel.get_attribute(a) or "")
                    for a in ("name", "id", "aria-label", "data-field")
                ).lower()
                label = (self._label_text_for(sel) or "").lower()
                if kind_low not in blob and kind_low not in label:
                    continue
                self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Found matching native select: {blob[:40]}")
                if self._select_native(sel, prefs):
                    self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Selected native option successfully")
                    return True
        except Exception as e:
            self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Native select error: {e}")
            pass

        # 2) Button / listbox controls near a matching label.
        self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Calling _find_menu_control({kind_low!r})")
        control = self._find_menu_control(kind_low)
        if not control:
            self._log(f"  [BAMBOOHR] [_commit_labeled_menu] No control found for {kind_low}")
            return False
        current = (
            (control.get_attribute("aria-label") or "")
            + " "
            + (control.inner_text() or "")
        ).strip()
        self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Control current value: {current!r}")
        for pref in prefs:
            if pref and pref.lower() in current.lower() and "select" not in current.lower():
                self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Option {pref!r} already set")
                return True  # already set

        self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Clicking control: {current[:30]!r}")
        if not self._safe_click(control, force=True):
            try:
                self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Fallback click control")
                control.click(force=True, timeout=3000)
            except Exception as e:
                self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Click control failed: {e}")
                return False
        self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Control clicked, sleeping 0.35s")
        time.sleep(0.35)

        for pref in prefs:
            if not pref:
                continue
            self._log(f"  [BAMBOOHR] [_commit_labeled_menu] Calling _pick_open_menu_option({pref!r})")
            if self._pick_open_menu_option(pref):
                time.sleep(0.25)
                verify = (
                    (control.get_attribute("aria-label") or "")
                    + " "
                    + (control.inner_text() or "")
                ).lower()
                if pref.lower() in verify or (
                    "select" not in verify and len(verify.strip()) > 2
                ):
                    self._log(f"menu {kind_low} → {pref!r}")
                    return True
            # Keyboard fallback: type the option and Enter.
            try:
                self.page.keyboard.type(pref, delay=20)
                time.sleep(0.15)
                self.page.keyboard.press("Enter")
                time.sleep(0.25)
                verify = (
                    (control.get_attribute("aria-label") or "")
                    + " "
                    + (control.inner_text() or "")
                ).lower()
                if pref.lower() in verify:
                    self._log(f"menu {kind_low} (keyboard) → {pref!r}")
                    return True
            except Exception:
                pass
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    def _find_menu_control(self, kind: str):
        """Find the clickable Country/Province control via several strategies."""
        # Explicit Bamboo field markers
        for sel in (
            f"button[aria-label*='{kind}' i]",
            f"[role='combobox'][aria-label*='{kind}' i]",
            f"select[name*='{kind}' i]",
            f"select[id*='{kind}' i]",
            f"[data-field*='{kind}' i] button",
            f"[data-field*='{kind}' i] [role='combobox']",
            f"label:has-text('{kind.title()}') + * button",
            f"label:has-text('{kind.title()}') ~ * button",
            f"div:has(> label:has-text('{kind.title()}')) button",
            f"div:has(> label:has-text('{kind.title()}')) [role='combobox']",
        ):
            try:
                el = self.page.query_selector(sel)
                if el and self._visible(el):
                    return el
            except Exception:
                continue

        # Walk labels and use the next button/combobox/select sibling.
        try:
            labels = self.page.query_selector_all("label, legend, span, div, p") or []
        except Exception:
            labels = []
        for lab in labels:
            try:
                text = (lab.inner_text() or "").strip().lower()
                if not text or len(text) > 40:
                    continue
                # Exact-ish match: "Country*", "Province *", "State/Province"
                if kind not in text:
                    continue
                if not any(k in text for k in (kind, f"{kind}*", f"{kind} ")):
                    continue
                # Prefer short field titles over body copy.
                if len(text) > 24 and "required" not in text:
                    continue
                handle = lab.evaluate_handle(
                    """node => {
                        const root = node.closest('div, fieldset, li, section, form') || node.parentElement;
                        if (!root) return null;
                        return root.querySelector(
                          "button, [role='combobox'], [role='listbox'], select, [aria-haspopup='listbox']"
                        );
                    }"""
                )
                if handle:
                    el = handle.as_element() if hasattr(handle, "as_element") else handle
                    if el and self._visible(el):
                        return el
            except Exception:
                continue

        # Last resort: unselected "–Select–" / "-Select-" buttons ordered Country first.
        try:
            buttons = self.page.query_selector_all(
                "button[aria-label*='Select' i], button[aria-label*='select' i], "
                "button:has-text('Select'), [role='combobox']"
            ) or []
        except Exception:
            buttons = []
        for button in buttons:
            try:
                if not self._visible(button):
                    continue
                blob = (
                    (button.get_attribute("aria-label") or "")
                    + " "
                    + (button.inner_text() or "")
                    + " "
                    + (self._label_text_for(button) or "")
                ).lower()
                if kind in blob:
                    return button
            except Exception:
                continue
        return None

    def _pick_open_menu_option(self, desired: str) -> bool:
        """Click a visible option from an open Bamboo dropdown/listbox."""
        desired_low = desired.lower().strip()
        selectors = (
            f"[role='option']:has-text('{desired}')",
            f"[role='menuitem']:has-text('{desired}')",
            f"[role='listbox'] [role='option']:has-text('{desired}')",
            f"li[role='option']:has-text('{desired}')",
            f"ul[role='listbox'] li:has-text('{desired}')",
            f"div[role='listbox'] div:has-text('{desired}')",
            f"[class*='MenuOption']:has-text('{desired}')",
            f"li:has-text('{desired}')",
        )
        for selector in selectors:
            try:
                candidates = self.page.query_selector_all(selector) or []
            except Exception:
                candidates = []
            for candidate in reversed(candidates):
                try:
                    if not self._visible(candidate):
                        continue
                    txt = (candidate.inner_text() or "").strip()
                    # Avoid clicking huge containers that merely contain the word.
                    if len(txt) > 80:
                        continue
                    if desired_low not in txt.lower():
                        continue
                    if self._safe_click(candidate, force=True):
                        return True
                    try:
                        candidate.click(force=True, timeout=2000)
                        return True
                    except Exception:
                        continue
                except Exception:
                    continue
        # get_by_role path (Playwright)
        try:
            for r in ("option", "menuitem"):
                opt = self.page.get_by_role(r, name=re.compile(re.escape(desired), re.I)).first
                if opt.count() and self._visible(opt):
                    opt.click(force=True)
                    return True
        except Exception:
            pass
        return False

    def _fill_custom_dropdowns(self) -> int:
        """Fill BambooHR button-backed dropdowns such as Province/Country."""
        filled = 0
        try:
            buttons = self.page.query_selector_all(
                "button[aria-label*='Select'], button[aria-label*='select'], "
                "button:has-text('–Select–'), button:has-text('-Select-'), "
                "button:has-text('Select'), [role='combobox']"
            ) or []
        except Exception:
            buttons = []
        # BambooHR clears Province when Country changes, so Country must be
        # committed first even if the DOM lists Province first.
        def _sort_key(button: Any) -> int:
            blob = (
                (button.get_attribute("aria-label") or "")
                + " "
                + (button.inner_text() or "")
                + " "
                + (self._label_text_for(button) or "")
            ).lower()
            if "country" in blob:
                return 0
            if "province" in blob or "state" in blob:
                return 1
            return 2

        buttons = [b for b in buttons if b]
        try:
            buttons.sort(key=_sort_key)
        except Exception:
            pass

        for button in buttons:
            try:
                if not self._visible(button):
                    continue
                label = (
                    (button.get_attribute("aria-label") or "")
                    + " "
                    + (button.inner_text() or "")
                    + " "
                    + (self._label_text_for(button) or "")
                ).strip()
                label_low = label.lower()
                if any(k in label_low for k in ("submit", "cancel", "file", "date", "upload")):
                    continue
                # Skip menus that already have a real value.
                if (
                    "select" not in label_low
                    and any(v.lower() in label_low for v in (
                        self._profile_country(), self._profile_province(), "canada", "british"
                    ))
                ):
                    continue

                if "province" in label_low or "state" in label_low:
                    desired = self._profile_province()
                    prefs = [desired, "British Columbia", "BC"]
                elif "country" in label_low:
                    desired = self._profile_country()
                    prefs = [desired, "Canada", "CA"]
                else:
                    # Unknown Select menu — skip rather than guess.
                    continue

                self._safe_click(button, force=True)
                time.sleep(0.3)
                committed = False
                for pref in prefs:
                    if self._pick_open_menu_option(pref):
                        time.sleep(0.2)
                        current = (
                            (button.get_attribute("aria-label") or "")
                            + " "
                            + (button.inner_text() or "")
                        ).lower()
                        if pref.lower() in current or "select" not in current:
                            filled += 1
                            committed = True
                            self._log(f"custom dropdown → {pref!r}")
                            break
                        try:
                            self.page.keyboard.press("Enter")
                            time.sleep(0.15)
                        except Exception:
                            pass
                        current = (
                            (button.get_attribute("aria-label") or "")
                            + " "
                            + (button.inner_text() or "")
                        ).lower()
                        if pref.lower() in current or "select" not in current:
                            filled += 1
                            committed = True
                            break
                if not committed:
                    try:
                        self.page.keyboard.press("Escape")
                    except Exception:
                        pass
            except Exception:
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
        return filled

    def submit(self) -> bool:
        self._dismiss_overlays()
        # NOTE: repair_required_fields is intentionally NOT called here.
        # The engine runs it before CAPTCHA solve; calling it again here
        # re-renders BambooHR's reCAPTCHA widget and invalidates the token.
        selectors = [
            # Current BambooHR React forms render the action as a button
            # associated with the form by id rather than nesting it inside
            # the form element.
            "button[type='submit'][form='job-application-form']",
            "form#job-application-form button[type='submit']",
            "button[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Send application')",
            "button:has-text('Send Application')",
            "button:has-text('Apply for this job')",
            "button:has-text('Apply')",
            "input[type='submit']",
            "button.btn-primary:has-text('Submit')",
            "button.btn-primary:has-text('Apply')",
            "[data-testid='submit-button']",
            "form button.btn-primary",
            "form button:last-of-type",
        ]
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
                    if any(bad in label for bad in ("cancel", "back", "upload", "choose file")):
                        continue
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    if self._safe_click(el, force=True):
                        time.sleep(1.2)
                        return True
                    try:
                        el.click(force=True, timeout=3000)
                        time.sleep(1.2)
                        return True
                    except Exception:
                        continue
                except Exception:
                    continue
        # requestSubmit() bypasses some React click handlers that ignore synthetic clicks.
        try:
            ok = self.page.evaluate(
                """() => {
                    const form = document.querySelector('form#job-application-form, form');
                    if (form && typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                        return true;
                    }
                    if (form) { form.submit(); return true; }
                    return false;
                }"""
            )
            if ok:
                time.sleep(1.2)
                return True
        except Exception:
            pass
        return False

    def verify_submission(self) -> str | None:
        """Page-primary confirmation (email is secondary / not required).

        Bamboo demos show ``Thank You / Your application was submitted
        successfully`` on the same careers URL — require that copy (or a
        confirmation URL). Form disappearance after CAPTCHA is **not** success.
        """
        from ..confirmation import (
            classify_page_confirmation,
            evidence_for_bamboo_copy,
        )

        self.confirmation_evidence = ""
        url = self.page.url or ""
        text = self._page_text(24000) or ""
        status, evidence = classify_page_confirmation(
            url, text, page=self.page, platform="bamboohr",
        )
        if status == "submitted":
            # Prefer human Bamboo labels used in queue reasons / tests.
            if "success text" in (evidence or ""):
                self.confirmation_evidence = evidence_for_bamboo_copy(text)
            else:
                self.confirmation_evidence = evidence or "visible application-success text"
        elif evidence:
            self.confirmation_evidence = evidence
        return status

    # ── BambooHR-specific handlers ────────────────────────────────────

    def _open_application_form(self) -> None:
        """Navigate to the application form if on a job detail page."""
        url = (self.page.url or "").lower()
        # BambooHR's detail page can carry a hidden form template. Prefer the
        # visible job-detail action when it exists so Chrome actually opens
        # the live application form.
        try:
            apply_button = self.page.get_by_role(
                "button", name=re.compile(r"apply for this job|apply now|apply", re.I)
            ).first
            if apply_button.count() and self._visible(apply_button):
                self._safe_click(apply_button, force=True)
                time.sleep(1.0)
        except Exception:
            pass
        # If already on an application form, stay
        try:
            form_fields = self.page.query_selector_all(
                "form input[type='text'], form input[type='email'], form textarea, "
                "input[name*='first'], input[name*='last'], input[name*='email']"
            ) or []
            # Job-detail pages can contain hidden application templates. Only
            # treat the form as open when a real field is visible in Chrome.
            if any(self._visible(field) for field in form_fields):
                return
        except Exception:
            pass

        # Click Apply button on job detail page
        for sel in (
            "a:has-text('Apply')",
            "button:has-text('Apply')",
            "a:has-text('Apply Now')",
            "button:has-text('Apply Now')",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply for this job')",
            "a[href*='/apply']",
            "a[href*='application']",
            ".btn:has-text('Apply')",
            "[data-testid*='apply']",
        ):
            try:
                el = self.page.query_selector(sel)
                if not el or not el.is_visible():
                    continue
                href = (el.get_attribute("href") or "").strip()
                if href and href.startswith("http"):
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

    def _fill_bamboohr_fields(self) -> FillStats:
        """Fill BambooHR-specific form fields."""
        stats = FillStats()

        try:
            fields = self.page.query_selector_all("input, textarea, select")
        except Exception:
            return stats

        # BambooHR's date field often exposes only a placeholder (for example
        # ``02/dd/yyyy``), so its question text is not available to the brain.
        for el in fields:
            try:
                if not self._visible(el) or (el.get_attribute("type") or "").lower() != "text":
                    continue
                marker = " ".join((el.get_attribute(k) or "") for k in ("placeholder", "aria-label", "id", "name")).lower()
                if "dd/yyyy" in marker or "dateavailable" in marker or "date_available" in marker:
                    if not (el.input_value() or "").strip():
                        if self._fill_input(el, date.today().strftime("%m/%d/%Y")):
                            stats.filled += 1
            except Exception:
                continue

        mapping = [
            (("first name", "firstname", "given-name", "fname", "first_name"), self.profile.get("first_name", "")),
            (("last name", "lastname", "family-name", "lname", "surname", "last_name"), self.profile.get("last_name", "")),
            (("email", "e-mail"), self.profile.get("email", "")),
            (("phone", "mobile", "tel", "phone_number"), self.profile.get("phone", "")),
            (("linkedin",), self.profile.get("linkedin", "")),
            (("website", "portfolio", "github"), self.profile.get("website", "")),
            (("address", "street"), self.profile.get("street", "")),
            (("city",), self.profile.get("city", "")),
            (("state", "province"), self.profile.get("state", "")),
            (("zip", "postal"), self.profile.get("zipcode", "")),
            (("country",), self.profile.get("country", "")),
            (("date available", "available date", "start date", "availability"),
             date.today().strftime("%m/%d/%Y")),
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

                try:
                    existing = (el.input_value() or "").strip()
                except Exception:
                    existing = ""
                if existing and existing.lower() not in {"", "n/a", "select..."}:
                    stats.skipped += 1
                    continue

                # Try brain first
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
                if self._fill_input(el, str(value)):
                    stats.filled += 1
            except Exception:
                continue

        return stats

    def _fill_custom_questions(self) -> int:
        """Fill BambooHR custom questionnaire fields."""
        filled = 0
        try:
            for el in self.page.query_selector_all(
                "input[type='text'], input:not([type]), textarea, "
                "input[type='number'], input[type='url'], input[type='date']"
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
                q_low = q.lower()
                # Honeypot / anti-bot fields must stay empty.
                if any(
                    k in q_low or k in blob
                    for k in (
                        "leave this field blank",
                        "leave blank",
                        "nickname_hp",
                        "hpcsaf",
                        "honeypot",
                        "do not fill",
                        "bots only",
                    )
                ):
                    continue
                if any(k in q_low for k in ("first name", "last name", "email", "phone")) and len(q) < 24:
                    continue
                typ = (el.get_attribute("type") or "").lower()
                if typ == "file":
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
                        "availability", "relocate", "remote",
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

    def _fill_eeo_section(self) -> int:
        """Fill EEO (Equal Employment Opportunity) section — prefer decline."""
        filled = 0
        try:
            # EEO fields are typically selects or radio groups
            for sel in self.page.query_selector_all("select"):
                blob = self._field_blob(sel)
                q = self._visible_question_text(sel) or blob
                if not any(k in q.lower() for k in (
                    "gender", "race", "ethnicity", "veteran", "disability",
                    "equal opportunity", "eeo", "self-identify", "hispanic",
                )):
                    continue
                try:
                    opts = sel.evaluate(
                        "el => Array.from(el.options).map(o => (o.text||'').trim()).filter(Boolean)"
                    ) or []
                except Exception:
                    continue
                # Prefer "Decline" / "Prefer not to say" / "I don't wish to answer"
                decline_opts = [o for o in opts if any(
                    k in o.lower() for k in ("decline", "prefer not", "don't wish", "do not wish", "not to say")
                )]
                if decline_opts:
                    if self._select_native(sel, decline_opts):
                        filled += 1
                        continue
                # Try brain
                prefs = self._resolve_for_field(
                    q,
                    profile=self.profile,
                    options=list(opts),
                    job_context=self.job_context,
                    hint=_ats_ai_hint(q, list(opts)),
                )
                if prefs and self._select_native(sel, prefs):
                    filled += 1
        except Exception:
            pass
        return filled

    def _fill_radio_groups(self) -> int:
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
            val_options = [(r.get_attribute("value") or "").strip() for r in group_radios]
            context_label = self._question_text_from_group(group_radios[0], val_options)
            option_labels = []
            for r in group_radios:
                lb = self._label_text_for(r)
                option_labels.append(lb)
                if lb and not context_label:
                    context_label = lb
            if not val_options:
                continue
            context_low = (context_label or group_name).lower()
            is_pronoun_group = "pronoun" in context_low or "pronoun" in group_name.lower()
            prefs = self._resolve_for_field(
                context_label or group_name,
                profile=self.profile,
                options=val_options,
                job_context=self.job_context,
                hint=_ats_ai_hint(context_label or group_name, val_options),
            ) or []
            if is_pronoun_group:
                prefs = ["he/him/his", "he/him", "he"] + prefs
            chosen = None
            for pref in prefs:
                pl = pref.lower().strip()
                for idx, r in enumerate(group_radios):
                    rv = (r.get_attribute("value") or "").lower()
                    rl = (option_labels[idx] or "").lower()
                    if (is_pronoun_group and "he/him" in rl) or pl == rv or pl in rv or rv in pl:
                        chosen = r
                        break
                if chosen:
                    break
            # Correct a wrong preselected template value too.
            if chosen and (not any(x.is_checked() for x in group_radios) or not chosen.is_checked()):
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
            except Exception:
                continue
        return filled

    def _fill_dropdowns(self) -> int:
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
                continue
            if not opts:
                continue
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
