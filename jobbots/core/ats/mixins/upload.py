"""File upload mixin — handles resume and cover letter uploads.

Shared across all ATS adapters.  Key rules:
  * NEVER click "Attach" buttons that open native OS file dialogs.
  * Always set files via hidden ``input[type=file]`` using ``set_input_files``.
  * Classify inputs as resume vs cover based on id/name/aria-label/label text.
"""
from __future__ import annotations

import time
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any


class UploadMixin:
    """Mixin providing file upload helpers for ATS adapters."""

    page: Any  # Playwright Page — set by adapter

    def _log(self, msg: str) -> None:
        """Implemented by the adapter or base class."""
        try:
            from jobbots.core.utils import print_lg  # type: ignore
            print_lg(msg)
        except Exception:
            print(msg)

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _visible(el: Any) -> bool:
        try:
            return bool(el and el.is_visible())
        except Exception:
            return False

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

    def _file_input_kind(self, el: Any) -> str:
        """Classify a file input as resume | cover | other."""
        blob = " ".join([
            el.get_attribute("id") or "",
            el.get_attribute("name") or "",
            el.get_attribute("aria-label") or "",
            el.get_attribute("data-testid") or "",
            self._label_text_for(el),
            (el.get_attribute("name") or "") + " " + (el.get_attribute("class") or ""),
        ]).lower()
        eid = (el.get_attribute("id") or "").lower()
        name = (el.get_attribute("name") or "").lower()

        if eid in {"cover_letter", "cover-letter", "coverletter"} or name in {
            "cover_letter", "cover-letter", "coverletter", "cover"
        }:
            return "cover"
        if "cover" in blob and "resume" not in blob and "cv" not in blob:
            return "cover"
        if eid in {"resume", "resume-upload-input", "resume_upload"} or name in {
            "resume", "cv", "resume-upload-input"
        }:
            return "resume"
        if any(k in blob for k in ("resume", "cv", "curriculum")) and "cover" not in blob:
            return "resume"
        return "other"

    def _unhide_file_inputs(self) -> None:
        """Light unhide — remove hidden/disabled attrs without breaking custom uploaders."""
        try:
            self.page.evaluate(
                """() => {
                  for (const el of document.querySelectorAll("input[type='file']")) {
                    el.removeAttribute('hidden');
                    el.removeAttribute('disabled');
                  }
                }"""
            )
        except Exception:
            pass

    def _all_file_inputs(self) -> list:
        """Collect file inputs from main page and child frames."""
        out = []
        try:
            out.extend(list(self.page.query_selector_all("input[type='file']")))
        except Exception:
            pass
        try:
            for fr in self.page.frames:
                if fr == self.page.main_frame:
                    continue
                try:
                    out.extend(list(fr.query_selector_all("input[type='file']")))
                except Exception:
                    continue
        except Exception:
            pass
        return out

    @staticmethod
    def _file_input_has_files(el: Any) -> bool:
        try:
            n = el.evaluate("e => (e.files && e.files.length) || 0")
            return int(n or 0) > 0
        except Exception:
            return False

    def _set_file(self, el: Any, path: str, label: str) -> bool:
        """Set a file on an input and dispatch events for React."""
        try:
            el.set_input_files(path)
            try:
                el.evaluate(
                    """(node) => {
                      node.dispatchEvent(new Event('input', {bubbles: true}));
                      node.dispatchEvent(new Event('change', {bubbles: true}));
                      node.setAttribute('data-ats-filled', '1');
                    }"""
                )
            except Exception:
                pass
            time.sleep(0.7)
            ok = self._file_input_has_files(el)
            self._log(
                f"Uploaded {label} via id={el.get_attribute('id')!r} "
                f"name={el.get_attribute('name')!r}: {Path(path).name} ok={ok}"
            )
            return True
        except Exception as exc:
            self._log(f"{label} upload failed on id={el.get_attribute('id')!r}: {exc}")
            return False

    def _resolve_default_paths(self, resume_path: str = "", cover_letter_path: str = "") -> tuple[str, str]:
        """Ensure valid absolute paths to default resume and cover letter."""
        monorepo = _MONOREPO_ROOT
        repo_root = monorepo.parent
        res = (resume_path or "").strip()
        if not res or not Path(res).is_file():
            candidate_resumes = (
                repo_root / "profiles" / "resumes" / "sample_resume_it.pdf",
                repo_root / "profiles" / "resumes" / "sample_resume_general.pdf",
                monorepo / "all resumes" / "resume_it.pdf",
                monorepo / "all resumes" / "resume.pdf",
                monorepo / "all resumes" / "ls_resume_it.pdf",
                monorepo / "all resumes" / "ls_resume_general.pdf",
            )
            for p in candidate_resumes:
                if p.is_file():
                    res = str(p.resolve())
                    break

        cov = (cover_letter_path or "").strip()
        if not cov or not Path(cov).is_file():
            candidate_covers = (
                repo_root / "profiles" / "resumes" / "sample_cover_letter_it.pdf",
                repo_root / "profiles" / "resumes" / "sample_cover_letter_general.pdf",
                monorepo / "all resumes" / "cover_letter.pdf",
                monorepo / "all resumes" / "cover_it.pdf",
                monorepo / "all resumes" / "cover_ls_it.pdf",
                monorepo / "all resumes" / "cover_ls_general.pdf",
            )
            for p in candidate_covers:
                if p.is_file():
                    cov = str(p.resolve())
                    break

        return res, cov

    def _page_has_resume_file(self) -> bool:
        """Check if any explicit resume/CV file input already has a file attached."""
        try:
            for el in self._all_file_inputs():
                kind = self._file_input_kind(el)
                if kind == "resume" and self._file_input_has_files(el):
                    return True
        except Exception:
            pass
        return False

    # ── public API ────────────────────────────────────────────────────

    def upload_documents(self, *, resume_path: str = "",
                         cover_letter_path: str = "") -> dict[str, bool]:
        """Upload resume and cover letter to their respective, verified slots.

        Prevents the BambooHR/ATS edge case where an automated filler or ambiguity attaches
        the resume to the cover letter slot and leaves the required resume field empty.

        Returns {"resume": bool, "cover": bool}.
        """
        try:
            self.page.wait_for_load_state("load", timeout=5000)
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            time.sleep(1.5)
        except Exception:
            pass

        resume_path, cover_letter_path = self._resolve_default_paths(resume_path, cover_letter_path)
        result = {"resume": False, "cover": False}

        if not resume_path and not cover_letter_path:
            self._log("No resume/cover files available")
            return result

        self._unhide_file_inputs()
        inputs = self._all_file_inputs()
        if not inputs:
            self._log("No file inputs found")
            return result

        resume_inputs = []
        cover_inputs = []
        other_inputs = []
        for el in inputs:
            kind = self._file_input_kind(el)
            if kind == "resume":
                resume_inputs.append(el)
            elif kind == "cover":
                cover_inputs.append(el)
            else:
                other_inputs.append(el)

        # 1. Inspect actual file attachment state per slot
        resume_slot_filled = False
        if resume_inputs:
            resume_slot_filled = any(self._file_input_has_files(el) for el in resume_inputs)
        elif len(inputs) == 1 and len(other_inputs) == 1:
            resume_slot_filled = self._file_input_has_files(other_inputs[0])

        cover_slot_filled = False
        if cover_inputs:
            cover_slot_filled = any(self._file_input_has_files(el) for el in cover_inputs)

        # 2. Handle the BambooHR Edge Case:
        # If resume_inputs exist but have NO file attached (or resume slot is empty),
        # we MUST upload the resume into resume_inputs even if another input has a file.
        if resume_path and not resume_slot_filled:
            self._log("Resume slot is empty — uploading resume...")
            uploaded = False
            for el in resume_inputs:
                if self._set_file(el, resume_path, "resume"):
                    result["resume"] = True
                    uploaded = True
                    break
            if not uploaded and len(inputs) == 1 and len(other_inputs) == 1:
                if self._set_file(other_inputs[0], resume_path, "resume"):
                    result["resume"] = True
                    uploaded = True
            if not uploaded:
                self._log("WARNING: could not place resume on a resume/CV slot")
        else:
            if resume_path and resume_slot_filled:
                result["resume"] = True
                self._log("Resume slot verified as already populated")

        # 3. Handle Cover Letter Upload:
        # If a dedicated cover letter input exists, ensure it is populated with our cover letter file.
        if cover_letter_path and cover_inputs:
            for el in cover_inputs:
                if not cover_slot_filled or not resume_slot_filled:
                    if self._set_file(el, cover_letter_path, "cover_letter"):
                        result["cover"] = True
                        break
                else:
                    result["cover"] = True

        return result

    def force_reupload_resume(self, profile: dict) -> bool:
        """Re-attach resume when form drops the file or validation errors occur."""
        path, _ = self._resolve_default_paths(profile.get("resume_path") or "")
        if not path:
            return False

        # Click common "Attach" / "Upload resume" UI to re-mount file inputs.
        for sel in (
            "button:has-text('Attach')",
            "button:has-text('Upload')",
            "a:has-text('Attach')",
            "label:has-text('Resume')",
            "label:has-text('CV')",
            "[data-provides='dropzone']",
            ".resume-upload",
            "#resume",
        ):
            try:
                el = self.page.query_selector(sel)
                if el and self._visible(el):
                    tag = ""
                    try:
                        tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
                        typ = (el.get_attribute("type") or "").lower()
                    except Exception:
                        typ = ""
                    if tag == "input" and typ == "file":
                        continue
                    try:
                        el.click(force=True, timeout=4000)
                    except Exception:
                        try:
                            el.evaluate("n => n.click()")
                        except Exception:
                            pass
                    time.sleep(0.25)
            except Exception:
                continue

        self._unhide_file_inputs()
        inputs = self._all_file_inputs()

        for el in inputs:
            try:
                eid = (el.get_attribute("id") or "").lower()
                if eid in {"resume", "resume-upload-input"} or self._file_input_kind(el) == "resume":
                    if self._set_file(el, path, "resume-resubmit"):
                        time.sleep(0.8)
                        return True
            except Exception:
                continue
        if len(inputs) == 1 and self._file_input_kind(inputs[0]) != "cover":
            if self._set_file(inputs[0], path, "resume-resubmit"):
                time.sleep(0.8)
                return True

        # Playwright locator fallback
        for sel in (
            "input#resume",
            "input[type='file'][id*='resume' i]",
            "input[type='file'][name*='resume' i]",
            "input[type='file']",
        ):
            try:
                loc = self.page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.set_input_files(path, timeout=5000)
                time.sleep(0.8)
                self._log(f"resume set via locator {sel!r}")
                return True
            except Exception:
                continue
        return False
