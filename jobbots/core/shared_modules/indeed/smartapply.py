from ._bootstrap import *  # noqa: F403
from pathlib import Path
from jobbots.core.evasion._handlers import (
    watch_for_captcha_after_submit,
    handle_recaptcha_widget,
    handle_recaptcha_challenge,
)
from jobbots.core.evasion._detection import is_recaptcha_challenge, is_recaptcha_expired, is_recaptcha_widget_present



def _is_submitted(page) -> bool:
    try:
        url = (page.url or "").lower()
        if "smart-apply-action=post_apply" in url or "smart-apply-action=post-apply" in url:
            return True
        if "/post-apply" in url or "post-apply" in url:
            return True

        active_form_steps = (
            _STEP_CONTACT, _STEP_LOCATION, _STEP_RESUME, _STEP_PRIVACY,
            _STEP_EXPERIENCE, _STEP_REVIEW, _STEP_QUAL, _STEP_EMP_QUESTIONS,
            _STEP_RESUME_SELECT, _STEP_APPLY_BY_ID, "review-module",
            "questions/", "resume-selection",
        )
        if SMARTAPPLY_DOMAIN in url and any(step in url for step in active_form_steps):
            return False

        title = ""
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""
        body = page.query_selector('body')
        if body:
            text = body.inner_text().lower()
            has_confirmation = any(kw in text for kw in _SUBMITTED_KEYWORDS)
            has_post_apply_controls = "return to job search" in text or "keep track of your applications" in text
            if has_confirmation and (has_post_apply_controls or "application has been submitted" in title):
                return True
    except Exception:
        pass
    return False

def _is_already_applied_notice(page) -> bool:
    try:
        body = page.query_selector('body')
        if body:
            text = body.inner_text().lower()
            return any(kw in text for kw in _ALREADY_APPLIED_KEYWORDS)
    except Exception:
        pass
    return False



def _smartapply_result_url(page, fallback: str = "") -> str:
    """Prefer live confirmation URL over the stale step URL captured at entry."""
    try:
        url = (page.url or "").strip()
        if url:
            return url
    except Exception:
        pass
    return (fallback or "").strip()



def _is_submission_error_modal_present(page) -> bool:
    try:
        body = page.query_selector('body')
        if body:
            text = (body.inner_text() or "").lower()
            return "having some trouble submitting" in text or "trouble submitting your application" in text
    except Exception:
        pass
    return False


def _find_and_click_close_modal_button(page) -> bool:
    selectors = [
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "button.icl-CloseButton",
        "button[data-testid='close-button']",
        "button[aria-label='Close modal']",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(force=True)
                return True
        except Exception:
            pass
    return False


def _find_and_click_save_job_and_exit_button(page) -> bool:
    selectors = [
        "button:has-text('Save job and exit')",
        "button:has-text('Save job & exit')",
        "button:has-text('Save job')",
        "button[data-testid='save-job-and-exit']",
        "button:has-text('exit')",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(force=True)
                return True
        except Exception:
            pass
    return False


def _visible_text_lines_from_page(page, limit: int = 80) -> list[str]:
    try:
        lines = page.evaluate(
            """
            limit => Array.from(document.querySelectorAll(
                "button, a, span, div, [role='button'], [aria-label]"
            ))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                           style.visibility !== "hidden" &&
                           style.display !== "none";
                })
                .map(el => {
                    const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
                    const aria = (el.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim();
                    return [text, aria].filter(Boolean);
                })
                .flat()
                .filter(Boolean)
                .slice(0, limit)
            """,
            limit,
        )
        return [str(line) for line in lines if str(line).strip()]
    except Exception:
        return []


def _title_tokens(text: str) -> set[str]:
    stop = {
        "and", "or", "the", "a", "an", "to", "for", "in", "on", "of", "with",
        "at", "by", "ii", "iii", "iv", "jr", "sr", "junior", "senior",
    }
    return {
        token for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 1 and token not in stop
    }


def _smartapply_visible_job_title(page) -> str:
    # First try DOM headers inside the apply modal/page
    selectors = [
        "h1.ia-JobHeader-title",
        ".ia-JobHeader-title",
        ".ia-JobHeader",
        ".ia-JobApplicationSteps-title",
        "h1",
        "h2"
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt and len(txt) > 5 and len(txt) < 140:
                    low = txt.lower()
                    if not any(k in low for k in ("skip to main content", "accessibility", "post a job", "find jobs", "apply", "indeed", "resume")):
                        return txt
        except Exception:
            pass

    # Fallback to body text lines but clean accessibility/navigation noise
    try:
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in (page.query_selector("body").inner_text() or "").splitlines()
        ]
    except Exception:
        return ""

    ignored = {
        "workopolis", "indeed", "exit", "continue", "submit application",
        "review your application", "preparing review", "select a resume",
        "loading", "loading...", "please wait", "please wait...",
        "skip to main content", "main content", "job details",
        "accessibility", "accessibility statement", "skip to main",
        "find jobs", "company reviews", "find salaries", "upload your resume",
        "sign in", "post a job", "employers", "salary guide", "salary search",
        "career guide"
    }

    for line in lines[:30]:
        low = line.lower().strip()
        if not line or low in ignored or low.endswith("%"):
            continue
        if any(ignored_term in low for ignored_term in ignored):
            continue
        if len(line) < 6 or len(line) > 140:
            continue
        if any(bit in low for bit in ("@", "http", "privacy", "terms", "captcha", "cookie", "feedback")):
            continue
        return line
    return ""


def _smartapply_job_title_matches(page, expected_title: str) -> tuple[bool, str]:
    # 1. Try to find the title inside the specific headers first
    visible_title = _smartapply_visible_job_title(page)

    # 2. Check if expected_title is a substring or token-matched subset of the page body text or visible_title
    try:
        body_text = re.sub(r"\s+", " ", page.query_selector("body").inner_text() or "").strip().lower()
    except Exception:
        body_text = ""

    exp_low = (expected_title or "").lower().strip()
    
    # Match 1: Substring match anywhere in body
    if exp_low and exp_low in body_text:
        return True, expected_title
        
    # Match 2: Substring match in visible title
    if exp_low and visible_title and exp_low in visible_title.lower():
        return True, visible_title

    # Match 3: Token overlap
    expected_tokens = _title_tokens(expected_title)
    if not expected_tokens:
        return True, visible_title
        
    # Check tokens against body text
    body_tokens = set(re.findall(r"[a-z0-9]+", body_text))
    body_overlap = len(expected_tokens & body_tokens)
    min_required_body = 1 if len(expected_tokens) <= 2 else 2
    if body_overlap >= min_required_body:
        return True, expected_title

    # Check tokens against visible title
    if visible_title:
        visible_tokens = _title_tokens(visible_title)
        if visible_tokens:
            overlap = len(expected_tokens & visible_tokens)
            min_required = 1 if len(expected_tokens) <= 2 else 2
            if overlap >= min_required:
                return True, visible_title

    # Keep safety check, but if we have absolutely no title text anywhere, we can default to True to avoid false block
    if not visible_title and not body_text:
        return True, ""

    return False, visible_title or "Unknown"


def _extract_page_questions_schema(page) -> list[dict]:
    """Scrapes active question fields from the current DOM context."""
    schema = []
    try:
        from jobbots.core.shared_modules.indeed.navigation import _get_question_context
    except ImportError:
        def _get_question_context(pg, element):
            return element.get_attribute("name") or element.get_attribute("id") or "question"

    try:
        elements = page.query_selector_all("input, textarea, select")
        seen_ids = set()
        
        for el in elements:
            try:
                if not el.is_visible():
                    continue
                    
                input_type = el.get_attribute("type") or ""
                tag_name = el.evaluate("el => el.tagName.toLowerCase()")
                field_id = el.get_attribute("id") or el.get_attribute("name") or ""
                
                if field_id and field_id in seen_ids and tag_name not in ("input"):
                    continue
                if field_id:
                    seen_ids.add(field_id)
                    
                # Resolve label
                label = _get_question_context(page, el) or ""
                
                # Required check
                required = (
                    el.get_attribute("required") is not None or
                    el.get_attribute("aria-required") == "true" or
                    "*" in label or
                    "required" in label.lower()
                )
                
                # Determine type
                if tag_name == "textarea":
                    in_type = "textarea"
                elif tag_name == "select":
                    in_type = "select"
                elif tag_name == "input":
                    if input_type in ("radio", "checkbox", "date", "text"):
                        in_type = input_type
                    else:
                        in_type = "text"
                else:
                    in_type = "unknown"
                    
                # Options
                options = []
                if in_type == "select":
                    options = el.evaluate(
                        "el => Array.from(el.options).filter(o => o.value).map(o => o.text.trim())"
                    )
                elif in_type == "radio":
                    name = el.get_attribute("name")
                    if name:
                        group_radios = page.query_selector_all(f"input[name='{name}']")
                        for r in group_radios:
                            r_id = r.get_attribute("id")
                            if r_id:
                                lbl = page.query_selector(f'label[for="{r_id}"]')
                                if lbl:
                                    options.append(lbl.inner_text().strip())
                                    
                # Current value
                current_value = ""
                if in_type == "select":
                    current_value = el.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.strip() : ''")
                elif in_type == "checkbox":
                    current_value = "checked" if el.is_checked() else "unchecked"
                elif in_type == "radio":
                    current_value = "checked" if el.is_checked() else "unchecked"
                else:
                    current_value = el.evaluate("el => el.value") or ""
                    
                # Validation error
                validation_error = ""
                parent_err = el.evaluate("""el => {
                    let p = el.parentElement;
                    for (let i = 0; i < 3; i++) {
                        if (!p) break;
                        let err = p.querySelector('[role="alert"], [class*="error"], [id*="error"]');
                        if (err) return err.innerText.trim();
                        p = p.parentElement;
                    }
                    return '';
                }""")
                if parent_err:
                    validation_error = parent_err
                    
                schema.append({
                    "field_id": field_id,
                    "label": label.strip(),
                    "required": required,
                    "input_type": in_type,
                    "options": options,
                    "current_value": current_value,
                    "validation_error": validation_error
                })
            except Exception:
                pass
    except Exception:
        pass
                
    return schema


def _text_has_indeed_applied_state(text: str) -> bool:
    low = (text or "").lower()
    if any(kw in low for kw in _ALREADY_APPLIED_KEYWORDS):
        return True

    for line in re.split(r"[\n\r]+", text or ""):
        norm = re.sub(r"\s+", " ", line).strip().lower()
        if norm in {"applied", "application submitted", "you applied"}:
            return True
        if re.fullmatch(r"applied\s+(today|yesterday|\d+\s+\w+\s+ago|on\s+.+)", norm):
            return True
    return False


def _text_has_indeed_saved_state(text: str) -> bool:
    for line in re.split(r"[\n\r]+", text or ""):
        norm = re.sub(r"\s+", " ", line).strip().lower()
        if norm in _INDEED_SAVED_KEYWORDS:
            return True
        if norm.startswith("saved ") or norm.endswith(" saved"):
            return True
    return False


def _text_has_indeed_hidden_state(text: str) -> bool:
    """Return True when card text indicates the user dismissed / hid this job."""
    low = (text or "").lower()
    return any(kw in low for kw in _INDEED_HIDDEN_KEYWORDS)


def _indeed_gui_job_state(page, card_text: str = "") -> str:
    """
    Return Indeed's own visible state for the job before AI gating.
    Values: "already_applied", "already_saved", "hidden", or "".
    """
    if _text_has_indeed_hidden_state(card_text):
        return "hidden"
    if _text_has_indeed_applied_state(card_text):
        return "already_applied"
    if _text_has_indeed_saved_state(card_text):
        return "already_saved"

    if page is None:
        return ""

    try:
        if _is_already_applied_notice(page):
            return "already_applied"
    except Exception:
        pass

    visible_lines = "\n".join(_visible_text_lines_from_page(page))
    if _text_has_indeed_hidden_state(visible_lines):
        return "hidden"
    if _text_has_indeed_applied_state(visible_lines):
        return "already_applied"
    if _job_already_saved_on_indeed(page) or _text_has_indeed_saved_state(visible_lines):
        return "already_saved"
    return ""


def _recaptcha_token_status(page) -> str:
    try:
        token_len = page.evaluate(
            """
            () => {
                const fields = Array.from(document.querySelectorAll(
                    "textarea[name='g-recaptcha-response'], textarea#g-recaptcha-response"
                ));
                return Math.max(0, ...fields.map((field) => (field.value || "").length));
            }
            """
        )
        return f"recaptcha_token_len={int(token_len or 0)}"
    except Exception as e:
        return f"recaptcha_token_check_failed={type(e).__name__}"


def _smartapply_review_diagnostics(page) -> str:
    parts = [_recaptcha_token_status(page)]
    try:
        disabled = page.evaluate(
            """
            () => {
                const btn = document.querySelector(
                    "button[data-testid='submit-application-button'], button[type='submit']"
                );
                if (!btn) return "button=missing";
                return `button_disabled=${Boolean(btn.disabled || btn.getAttribute("aria-disabled") === "true")}`;
            }
            """
        )
        parts.append(str(disabled))
    except Exception as e:
        parts.append(f"button_check_failed={type(e).__name__}")

    try:
        errors = page.evaluate(
            """
            () => Array.from(document.querySelectorAll(
                "[role='alert'], [aria-live='assertive'], .ia-FormErrorText, .icl-FormField-errorText"
            ))
                .map((el) => (el.innerText || el.textContent || "").trim())
                .filter(Boolean)
                .slice(0, 3)
            """
        )
        if errors:
            parts.append("errors=" + " | ".join(errors)[:240])
    except Exception:
        pass

    return ", ".join(parts)


def _is_submit_button_ready(page) -> bool:
    try:
        return bool(page.evaluate(
            """
            () => {
                const btn = document.querySelector(
                    "button[data-testid='submit-application-button'], button[type='submit']"
                );
                if (!btn) return false;
                const style = window.getComputedStyle(btn);
                const disabled = Boolean(btn.disabled || btn.getAttribute("aria-disabled") === "true");
                const hidden = style.display === "none" || style.visibility === "hidden";
                return !disabled && !hidden;
            }
            """
        ))
    except Exception:
        return False


def _review_still_preparing(page) -> bool:
    """Indeed sometimes parks on review-module with 'Preparing review' and no Submit yet."""
    try:
        text = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return "preparing review" in text


def _resume_section_edit_visible(page) -> bool:
    """True when the Resume card Edit control is on-screen (not Contact Information)."""
    scoped_selectors = (
        "section:has(h2:has-text('Resume')) a:has-text('Edit')",
        "section:has(h3:has-text('Resume')) a:has-text('Edit')",
        "div:has(> h2:has-text('Resume')) a:has-text('Edit')",
        "div:has(> h3:has-text('Resume')) a:has-text('Edit')",
        "[data-testid*='resume'] a:has-text('Edit')",
        "div:has(a:has-text('Download')):has-text('Resume') a:has-text('Edit')",
        "div:has(a:has-text('Download')) a:has-text('Edit')",
        "div:has(button:has-text('Download')) a:has-text('Edit')",
        "div:has-text('Uploaded just now') a:has-text('Edit')",
    )
    for sel in scoped_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return True
        except Exception:
            continue
    # DOM score fallback — same scoring as click helper.
    try:
        return bool(page.evaluate(
            """() => {
              const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
              const isEdit = (el) => {
                const t = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                return t === 'edit' || t.startsWith('edit ');
              };
              const candidates = Array.from(
                document.querySelectorAll('a, button, [role="button"]')
              ).filter((el) => {
                try { return el.offsetParent !== null && isEdit(el); } catch (e) { return false; }
              });
              for (const el of candidates) {
                let n = el.parentElement;
                for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                  const txt = norm(n.innerText || '');
                  if (!txt || txt.length > 2500) continue;
                  const hasResume = /\\bresume\\b/.test(txt);
                  const hasContact = /contact information/.test(txt);
                  const hasDownload = /\\bdownload\\b/.test(txt);
                  if (hasContact && !hasResume) break;
                  if (hasResume && (hasDownload || /uploaded|\\.pdf\\b/.test(txt))) return true;
                  if (hasResume) return true;
                }
              }
              return false;
            }"""
        ))
    except Exception:
        return False


def _wait_for_review_resume_ui(page, *, timeout_s: float = 45.0):
    """Wait until Preparing review clears enough for Resume Edit / Submit to appear."""
    deadline = time.time() + max(5.0, float(timeout_s))
    last_log = 0.0
    while time.time() < deadline:
        try:
            if _is_submitted(page) or _is_already_applied_notice(page):
                return page
            if _resume_section_edit_visible(page):
                print_lg("  [SmartApply] Resume section Edit is visible on review.")
                return page
            if _is_submit_button_ready(page) and not _review_still_preparing(page):
                return page
        except Exception:
            pass
        now = time.time()
        if now - last_log >= 8.0:
            print_lg("  [SmartApply] Waiting for review Resume card / Edit to appear…")
            last_log = now
        time.sleep(1.5)
        try:
            page = try_recover_page(page)
        except Exception:
            pass
    return page


def _resume_picker_open(page) -> bool:
    """True when resume-selection URL or a resume file input / picker UI is visible."""
    try:
        url = (page.url or "").lower()
        if "resume-selection" in url:
            return True
        if _resume_file_input_present(page):
            return True
        # Picker cards / upload CTA after Edit (may stay on review-module URL).
        for sel in (
            "[data-testid*='resume-selection']",
            "input[name='resume-selection']",
            "button:has-text('Upload a resume')",
            "button:has-text('Upload resume')",
            "label:has-text('Upload a resume')",
        ):
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _click_resume_section_edit_on_review(page) -> bool:
    """Click only the Resume card Edit link — never Contact Information Edit.

    Review UI often has two Edit links (Contact Information + Resume). Prefer the
    Edit that shares a card/section with Resume heading, Download, filename, or
    'Uploaded just now'.
    """
    # Playwright scoped selectors (Resume section first).
    scoped_selectors = (
        # Card/section containing Resume heading + Edit/Change (a or button or [role=button])
        "section:has(h2:has-text('Resume')) :is(a, button, [role='button']):has-text('Edit')",
        "section:has(h3:has-text('Resume')) :is(a, button, [role='button']):has-text('Edit')",
        "div:has(> h2:has-text('Resume')) :is(a, button, [role='button']):has-text('Edit')",
        "div:has(> h3:has-text('Resume')) :is(a, button, [role='button']):has-text('Edit')",
        "[data-testid*='resume']:has-text('Resume') :is(a, button, [role='button']):has-text('Edit')",
        "[data-testid*='resume'] :is(a, button, [role='button']):has-text('Edit')",
        
        "section:has(h2:has-text('Resume')) :is(a, button, [role='button']):has-text('Change')",
        "section:has(h3:has-text('Resume')) :is(a, button, [role='button']):has-text('Change')",
        "div:has(> h2:has-text('Resume')) :is(a, button, [role='button']):has-text('Change')",
        "[data-testid*='resume']:has-text('Resume') :is(a, button, [role='button']):has-text('Change')",
        "[data-testid*='resume'] :is(a, button, [role='button']):has-text('Change')",

        # Resume card that also has Download (Contact Information does not)
        "div:has(:is(a, button):has-text('Download')):has-text('Resume') :is(a, button, [role='button']):has-text('Edit')",
        "div:has(:is(a, button):has-text('Download')):has-text('Resume') :is(a, button, [role='button']):has-text('Change')",
        "div:has(:is(a, button):has-text('Download')) :is(a, button, [role='button']):has-text('Edit')",
        "div:has(:is(a, button):has-text('Download')) :is(a, button, [role='button']):has-text('Change')",

        # Filename / uploaded-just-now cues
        "div:has-text('Uploaded just now'):has(:is(a, button):has-text('Edit')) :is(a, button, [role='button']):has-text('Edit')",
        "div:has-text('.pdf'):has(:is(a, button):has-text('Download')) :is(a, button, [role='button']):has-text('Edit')",
        "div:has-text('.pdf'):has(:is(a, button):has-text('Download')) :is(a, button, [role='button']):has-text('Change')",
    )
    for sel in scoped_selectors:
        try:
            loc = page.locator(sel).first
            if not loc.count():
                continue
            if not loc.is_visible():
                continue
            print_lg(f"  [SmartApply] Opening resume picker via Resume section Edit/Change ({sel})…")
            loc.click(timeout=4000)
            time.sleep(1.5)
            return True
        except Exception:
            continue

    # DOM walk: find Edit whose nearest section/card mentions Resume (not Contact).
    try:
        handle = page.evaluate_handle(
            """() => {
              const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
              const isEdit = (el) => {
                const t = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
                return t === 'edit' || t.startsWith('edit ') || t === 'change' || t.startsWith('change ') || t === 'replace' || t.includes('resume');
              };
              const candidates = Array.from(
                document.querySelectorAll('a, button, [role="button"]')
              ).filter((el) => {
                try { return el.offsetParent !== null && isEdit(el); } catch (e) { return false; }
              });
              const score = (el) => {
                let n = el.parentElement;
                let best = 0;
                for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                  const txt = norm(n.innerText || '');
                  if (!txt || txt.length > 2500) continue;
                  const hasResume = /\\bresume\\b/.test(txt);
                  const hasContact = /contact information/.test(txt);
                  const hasDownload = /\\bdownload\\b/.test(txt);
                  const hasUploaded = /uploaded just now|uploaded/.test(txt);
                  const hasPdf = /\\.pdf\\b/.test(txt);
                  if (hasContact && !hasResume) return -100;
                  if (hasResume && hasDownload) return 100;
                  if (hasResume && (hasUploaded || hasPdf)) return 90;
                  if (hasResume) best = Math.max(best, 70);
                  if (hasDownload && hasPdf) best = Math.max(best, 60);
                }
                return best;
              };
              let bestEl = null, bestScore = 0;
              for (const el of candidates) {
                const s = score(el);
                if (s > bestScore) { bestScore = s; bestEl = el; }
              }
              return bestScore >= 60 ? bestEl : null;
            }"""
        )
        el = handle.as_element() if handle else None
        if el:
            print_lg("  [SmartApply] Opening resume picker via Resume section Edit (DOM-scoped)…")
            el.click(timeout=4000)
            time.sleep(1.5)
            return True
    except Exception as exc:
        print_lg(f"  [SmartApply] Resume Edit DOM scope failed: {type(exc).__name__}: {exc}")
    return False


def _navigate_to_resume_selection_from_review(page) -> bool:
    """Force resume step when Indeed skips questions and lands on review with a preselected resume.

    No-question Glassdoor/Indeed Easy Apply often jumps straight to review-module using the
    account's saved resume. We still need resume-selection so a tailored PDF can be uploaded.

    Order: Resume-section Edit → explicit resume CTAs → URL rewrite (last resort).
    """
    cur = (page.url or "")
    if _resume_picker_open(page):
        return True

    # 1) Preferred: Resume card Edit (not Contact Information Edit).
    if _click_resume_section_edit_on_review(page):
        page = try_recover_page(page)
        if _resume_picker_open(page):
            return True
        print_lg("  [SmartApply] Resume Edit clicked but picker not detected yet — continuing checks…")
        # Edit may navigate asynchronously; brief wait then re-check.
        time.sleep(1.5)
        page = try_recover_page(page)
        if _resume_picker_open(page):
            return True

    # 2) Explicit resume-change CTAs (safe labels; not bare "Edit").
    click_labels = (
        "Use a different resume",
        "Upload a resume",
        "Upload a different resume",
        "Replace resume",
        "Edit resume",
        "Change resume",
        "Add a resume",
    )
    for label in click_labels:
        try:
            btn = page.locator(
                f"button:has-text('{label}'), a:has-text('{label}'), [role='button']:has-text('{label}')"
            ).first
            if btn.count() and btn.is_visible():
                print_lg(f"  [SmartApply] Opening resume picker from review via '{label}'…")
                btn.click(timeout=4000)
                time.sleep(1.5)
                page = try_recover_page(page)
                if _resume_picker_open(page):
                    return True
        except Exception:
            continue

    # 3) URL rewrite last resort (often fails / hangs on Preparing review).
    new_url = cur
    if "form/review-module" in cur:
        new_url = cur.replace("form/review-module", "form/resume-selection-module/resume-selection", 1)
    elif "/review-module" in cur:
        new_url = cur.replace("/review-module", "/resume-selection-module/resume-selection", 1)
    if new_url != cur and "resume-selection" in new_url.lower():
        try:
            print_lg("  [SmartApply] Resume Edit/CTAs failed — URL rewrite to resume-selection (last resort)…")
            print_lg(f"  [SmartApply] resume-selection URL: {new_url[:140]}")
            page.goto(new_url, timeout=20000, wait_until="domcontentloaded")
            time.sleep(2.0)
            page = try_recover_page(page)
            landed = (page.url or "")
            if _resume_picker_open(page):
                return True
            print_lg(f"  [SmartApply] resume-selection redirect landed on: {landed[:140]}")
        except Exception as exc:
            print_lg(f"  [SmartApply] resume-selection redirect failed: {type(exc).__name__}: {exc}")
    return False


def _resume_file_input_present(page) -> bool:
    try:
        el = page.query_selector("input[type='file']")
        return bool(el)
    except Exception:
        return False


def _wait_for_review_submit_ready(page, *, timeout_s: float = 60.0):
    """Poll until Submit appears (or already-applied / submitted). Returns updated page."""
    deadline = time.time() + max(5.0, float(timeout_s))
    last_log = 0.0
    while time.time() < deadline:
        try:
            if _is_submitted(page) or _is_already_applied_notice(page):
                return page
            if _is_submit_button_ready(page) and not _review_still_preparing(page):
                return page
            if _is_submit_button_ready(page):
                # Submit exists even while preparing text briefly remains.
                return page
        except Exception:
            pass
        now = time.time()
        if now - last_log >= 8.0:
            print_lg("  [SmartApply] Waiting for review to finish preparing (Submit not ready yet)…")
            last_log = now
        time.sleep(2.0)
        try:
            page = try_recover_page(page)
        except Exception:
            pass
    return page


def _captcha_still_blocking(page) -> bool:
    try:
        # CapMonster can inject a valid token while the challenge iframe/checkbox
        # stays visible in the DOM. Indeed still enables Submit — match that signal
        # instead of failing the whole SmartApply run.
        if _is_submit_button_ready(page):
            return False
        return (
            is_cloudflare_challenge(page)
            or is_recaptcha_challenge(page)
            or is_recaptcha_expired(page)
            or is_recaptcha_widget_present(page)
        )
    except Exception:
        return False


def _smartapply_live_form_ui(page) -> dict:
    """Detect real SmartApply UI even when the URL is still applybyapplyablejobid.

    Indeed often keeps the redirect URL while already showing resume / review /
    CAPTCHA. Treating that as a dead redirect causes false ``stuck_same_url``
    aborts (seen on Workopolis → SmartApply after login).
    """
    out = {
        "submit": False,
        "review": False,
        "resume": False,
        "questions": False,
        "captcha": False,
        "any": False,
    }
    try:
        out["submit"] = bool(_is_submit_button_ready(page))
        out["review"] = bool(_review_still_preparing(page) or _resume_section_edit_visible(page))
        out["resume"] = bool(_resume_section_edit_visible(page))
        out["captcha"] = bool(
            is_recaptcha_challenge(page)
            or is_recaptcha_expired(page)
            or is_recaptcha_widget_present(page)
            or is_cloudflare_challenge(page)
        )
        body = page.query_selector("body")
        text = (body.inner_text() or "").lower() if body else ""
        if any(s in text for s in (
            "review your application",
            "submit your application",
            "preparing review",
        )):
            out["review"] = True
        if any(s in text for s in (
            "select a resume",
            "upload a resume",
            "upload resume",
            "choose a resume",
        )):
            out["resume"] = True
        if any(s in text for s in (
            "answer these questions",
            "additional questions",
            "employer questions",
            "relevant experience",
        )):
            out["questions"] = True
        out["any"] = any(out[k] for k in ("submit", "review", "resume", "questions", "captcha"))
    except Exception:
        pass
    return out


def _captcha_failure_reason(context: str) -> str:
    # Keep the word "captcha" so application_worker classifies as
    # captcha_cf_requeue (not permanent dead).
    return f"CAPTCHA failed or still blocking at {context}; requeue for retry"


def _try_clear_captcha_with_retries(page, sb, *, context: str, run_in_background: bool, rounds: int = 4) -> bool:
    """Solve / wait for CAPTCHA. True if page is clear enough to continue.

    Does not abandon the job after a single CapMonster miss — reCAPTCHA
    widgets often stay visible while token inject is still in flight.
    """
    for attempt in range(1, max(1, rounds) + 1):
        try:
            check_and_handle_captcha(
                page, sb, context=f"{context} (try {attempt}/{rounds})",
                run_in_background=run_in_background,
            )
        except Exception as exc:
            print_lg(f"  [SmartApply] CAPTCHA handler error: {exc}")
        page = try_recover_page(page)
        # Submit enabled → Indeed accepted the challenge even if iframe remains
        try:
            if _is_submit_button_ready(page):
                print_lg("  [SmartApply] CAPTCHA: Submit ready — continuing.")
                return True
        except Exception:
            pass
        if not _captcha_still_blocking(page):
            print_lg(f"  [SmartApply] CAPTCHA cleared after try {attempt}/{rounds}.")
            return True
        wait_s = min(8.0, 2.0 * attempt)
        print_lg(
            f"  [SmartApply] CAPTCHA still blocking at {context} "
            f"(try {attempt}/{rounds}) — waiting {wait_s:.0f}s for token…"
        )
        time.sleep(wait_s)
    return not _captcha_still_blocking(page)


# ─────────────────────────────────────────────────────────────────────────────
# SmartApply multi-step orchestrator  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

# ── Known submit button selectors (from HTML dump analysis) ─────────────────
_SUBMIT_BTN_SELECTORS = [
    "button[data-testid='submit-application-button']",   # ← confirmed from dump
    "button[type='submit']",
    "button[data-testid*='submit']",
    "button[data-testid*='Submit']",
]

_SUBMIT_BTN_XPATHS = [
    "//button[@data-testid='submit-application-button']",
    "//button[@type='submit']",
    "//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit your application')]",
    "//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit application')]",
]


def _click_submit_button(page) -> bool:
    """
    Click the final 'Submit your application' button.
    Returns True if a visible button was found and click was ATTEMPTED
    (even if el.click() throws a NavigationError — that means it worked).

    CRITICAL: return True as soon as the first visible button is found,
    do NOT continue iterating — that would spam-click the same button 5 times.
    """
    candidates = [(sel, False) for sel in _SUBMIT_BTN_SELECTORS]
    candidates.extend((f"xpath={xp}", True) for xp in _SUBMIT_BTN_XPATHS)

    for selector, is_xpath in candidates:
        try:
            el = page.query_selector(selector)
            if not el or not el.is_visible():
                continue

            btn_text = el.inner_text().strip()[:60]
            suffix = " (xpath)" if is_xpath else ""
            print_lg(f"  [SmartApply] Clicking Submit{suffix}: '{btn_text}'")

            try:
                el.click(timeout=8000)
                print_lg("  [SmartApply] Submit click completed.")
                return True
            except PlaywrightError as click_err:
                print_lg(
                    "  [SmartApply] Native submit click failed: "
                    f"{type(click_err).__name__}: {str(click_err).splitlines()[0][:140]}"
                )
                try:
                    el.evaluate(
                        """
                        (button) => {
                            button.scrollIntoView({block: "center", inline: "center"});
                            button.focus();
                            button.click();
                        }
                        """
                    )
                    print_lg("  [SmartApply] Submit JS fallback click fired.")
                    return True
                except Exception as js_err:
                    print_lg(
                        "  [SmartApply] Submit JS fallback failed: "
                        f"{type(js_err).__name__}: {str(js_err).splitlines()[0][:140]}"
                    )
                    return False
        except Exception:
            continue

    return False

def _is_cover_letter_screen(page) -> bool:
    try:
        # Check headings
        for h in page.query_selector_all("h1, h2, h3, [role='heading']"):
            h_text = (h.inner_text() or "").lower()
            if "cover letter" in h_text or "lettre de motivation" in h_text or "lettre d'accompagnement" in h_text:
                return True
        # Check labels / legends
        for lbl in page.query_selector_all("label, legend"):
            lbl_text = (lbl.inner_text() or "").lower()
            if "cover letter" in lbl_text or "lettre de motivation" in lbl_text or "lettre d'accompagnement" in lbl_text:
                return True
        # Check input/textarea hints (text field — not PDF upload)
        for inp in page.query_selector_all("textarea, input[type='file']"):
            name = (inp.get_attribute("name") or "").lower()
            iid = (inp.get_attribute("id") or "").lower()
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            aria = (inp.get_attribute("aria-label") or "").lower()
            if any("cover" in x for x in (name, iid, placeholder, aria)):
                return True
        # Indeed additional-documents step often hosts the cover-letter textarea
        url = (page.url or "").lower()
        if "additional-documents" in url or "cover-letter" in url:
            try:
                if page.query_selector("textarea"):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _fallback_cover_letter(title: str, company: str, location: str = "") -> str:
    co = (company or "").strip() or "your team"
    role = (title or "").strip() or "this role"
    loc = (location or "").strip()
    loc_bit = f" in {loc}" if loc else ""
    return (
        f"Dear Hiring Manager,\n\n"
        f"I am writing to apply for the {role} position at {co}{loc_bit}. "
        f"I am an IT student at Kwantlen Polytechnic University specializing in "
        f"Network Administration & Security, and an AWS Certified Solutions Architect "
        f"– Associate. I have hands-on experience with networking, Windows Server and "
        f"Linux, cloud infrastructure on AWS, and security tooling, plus three years of "
        f"customer-facing technical support at Bell Canada.\n\n"
        f"I am reliable, detail-oriented, and eager to contribute to your team. "
        f"I would welcome the opportunity to discuss how my skills can support {co}.\n\n"
        f"Sincerely,\n"
        f"Jane Doe\n"
        f"555-0199\n"
        f"user@example.com"
    )


def _generate_short_cover_letter(title: str, company: str, location: str = "") -> str:
    """Production-quality short cover letter for SmartApply textarea (plain text, no PDF)."""
    co = (company or "").strip()
    role = (title or "").strip()
    loc = (location or "").strip()
    # Prefer configured static letter (already production-quality), then AI, then stub.
    try:
        from config.questions import cover_letter as _cfg_cl
        static = (_cfg_cl or "").strip()
    except Exception:
        static = ""

    job_context = (
        f"Job title: {role or 'the role'}. "
        f"Company: {co or 'the employer'}. "
        f"Location: {loc or 'Metro Vancouver, BC'}. "
        "Write a complete short cover letter to paste into an Indeed SmartApply "
        "text field. Plain text only (no markdown, no bullets). 120–180 words. "
        "Candidate: Jane Doe, Surrey BC; BTech IT Network Administration & "
        "Security at KPU; AWS Solutions Architect Associate; 3 years Bell Canada "
        "tech support; skills networking, AWS, Windows/Linux, security, help desk, QA. "
        "Start with Dear Hiring Manager, end with Sincerely, Jane Doe and phone."
    )
    ai_text = ""
    try:
        from jobbots.core.shared_modules.indeed.ai import _ai_answer
        ai_text = (
            _ai_answer(
                question=(
                    "Write a short professional cover letter for this job application. "
                    "Output only the letter body ready to paste into a form."
                ),
                hint=job_context,
                job_context=job_context,
            )
            or ""
        ).strip()
    except Exception as exc:
        print_lg(f"  [SmartApply] Cover letter AI failed: {exc}")

    # Prefer AI when it produced a real letter; else config; else deterministic stub.
    letter = ""
    if ai_text and len(ai_text) >= 100 and "dear" in ai_text.lower()[:80]:
        letter = ai_text
        print_lg("  [SmartApply] Cover letter source: AI")
    elif ai_text and len(ai_text) >= 120:
        letter = ai_text
        print_lg("  [SmartApply] Cover letter source: AI (loose)")
    elif static and len(static) >= 80:
        # Wrap static blurb as a proper letter if it is only a paragraph.
        if "dear" not in static.lower()[:40]:
            letter = (
                f"Dear Hiring Manager,\n\n{static.strip()}\n\n"
                f"Sincerely,\nJane Doe\n555-0199"
            )
        else:
            letter = static
        print_lg("  [SmartApply] Cover letter source: config/questions.cover_letter")
    else:
        letter = _fallback_cover_letter(role, co, loc)
        print_lg("  [SmartApply] Cover letter source: fallback stub")

    # SmartApply allows up to ~4000 chars; keep short and production-ready.
    letter = letter.replace("\r\n", "\n").replace("\r", "\n").strip()
    # Collapse only runs of spaces/tabs (preserve newlines for paragraphs).
    import re as _re
    letter = _re.sub(r"[ \t]+", " ", letter)
    letter = _re.sub(r"\n{3,}", "\n\n", letter).strip()
    if "Dear" not in letter[:60]:
        letter = f"Dear Hiring Manager,\n\n{letter}"
    if "Sincerely" not in letter and "Regards" not in letter:
        letter = f"{letter.rstrip()}\n\nSincerely,\nJane Doe\n555-0199"
    if len(letter) > 1800:
        letter = letter[:1790].rsplit(" ", 1)[0] + "…"
    return letter


def _cover_letter_pdf_path() -> str:
    """Return the configured local cover-letter PDF, if it is a valid PDF."""
    candidates: list[str] = []
    configured = os.environ.get("INDEED_COVER_LETTER_PATH", "").strip()
    if configured:
        candidates.append(configured)
    try:
        from config.questions import cover_letter_pdf_path
        if cover_letter_pdf_path:
            candidates.append(str(cover_letter_pdf_path))
    except Exception:
        pass
    # A profile-specific cover PDF may live next to its configured resume.
    # Do not let the General profile silently use the IT cover letter.
    try:
        resume = Path(default_resume_path).expanduser().resolve()
        if resume.name.startswith("ls_resume_"):
            candidates.append(str(resume.parent / resume.name.replace("ls_resume_", "cover_ls_", 1)))
    except Exception:
        pass

    for candidate in candidates:
        try:
            resolved = Path(resolve_project_path(candidate)).expanduser().resolve()
            if resolved.is_file() and resolved.suffix.lower() == ".pdf":
                with resolved.open("rb") as pdf:
                    if pdf.read(5) == b"%PDF-":
                        return str(resolved)
        except Exception:
            continue
    return ""


def _cover_letter_file_input(page):
    """Find the file control belonging to a cover-letter upload, never a resume."""
    page_is_cover_letter_step = _is_cover_letter_screen(page)
    for file_input in page.query_selector_all("input[type='file']"):
        try:
            context = file_input.evaluate(
                """
                el => {
                    const attrs = ["name", "id", "aria-label", "accept", "data-testid"]
                        .map(name => el.getAttribute(name) || "").join(" ");
                    const parent = el.closest("label, fieldset, form, section, div");
                    return `${attrs} ${(parent?.innerText || "").slice(0, 900)}`.toLowerCase();
                }
                """
            ) or ""
            is_cover = any(token in context for token in (
                "cover letter", "cover_letter", "cover-letter",
                "lettre de motivation", "lettre d'accompagnement",
            ))
            is_resume_only = "resume" in context and not is_cover
            # Native upload controls are commonly visually hidden and sometimes
            # have no useful name. On a confirmed cover-letter step, the only
            # non-resume upload control is still the correct target.
            if (is_cover or page_is_cover_letter_step) and not is_resume_only:
                return file_input
        except Exception:
            continue
    return None


def _upload_cover_letter_pdf(page, file_input, path: str, job_id: str, title: str) -> bool:
    """Upload and verify the cover-letter PDF selected for this application."""
    try:
        file_input.set_input_files(path)
        selected = file_input.evaluate(
            "el => Array.from(el.files || []).map(file => file.name).join(', ')"
        ) or ""
        filename = Path(path).name
        if filename not in selected:
            raise RuntimeError(f"upload not reflected in file input: {selected!r}")
        print_lg(f"  [SmartApply] Cover-letter PDF uploaded: {filename}")
        log_training_event(
            "cover_letter_uploaded",
            job={**(_current_job_meta or {}), "job_id": job_id, "title": title},
            filename=filename,
            path=path,
        )
        return True
    except Exception as exc:
        print_lg(f"  [SmartApply] Cover-letter PDF upload failed: {type(exc).__name__}: {exc}")
        log_training_event(
            "cover_letter_upload_failed",
            job={**(_current_job_meta or {}), "job_id": job_id, "title": title},
            path=path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False


def _cover_textarea_context(textarea) -> str:
    try:
        return textarea.evaluate(
            """
            el => {
                const attrs = ["name", "id", "aria-label", "placeholder", "data-testid"]
                    .map(name => el.getAttribute(name) || "").join(" ");
                const label = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
                const parent = el.closest("label, fieldset, form, section, div");
                return `${attrs} ${label?.innerText || ""} ${(parent?.innerText || "").slice(0, 900)}`.toLowerCase();
            }
            """
        ) or ""
    except Exception:
        return ""


def _fill_cover_letter_textarea(page, letter: str) -> bool:
    """Type only into a known cover-letter textarea. Returns True if filled."""
    selectors = (
        'textarea[aria-label*="cover letter" i]',
        'textarea[aria-label*="Write a cover letter" i]',
        'textarea[data-testid*="cover" i]',
        'textarea[name*="cover" i]',
        'textarea[id*="cover" i]',
        'textarea[placeholder*="cover" i]',
        # French
        'textarea[aria-label*="lettre" i]',
    )
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if not loc.count():
                continue
            if not loc.is_visible(timeout=1200):
                continue
            loc.click(timeout=3000)
            try:
                loc.fill("")
            except Exception:
                pass
            loc.fill(letter)
            # Verify something landed
            try:
                val = loc.input_value(timeout=1500) or ""
            except Exception:
                val = letter
            if len((val or "").strip()) >= 40:
                return True
            # Some SmartApply fields need type() for React controlled inputs
            try:
                loc.click(timeout=2000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                loc.type(letter, delay=5)
                return True
            except Exception:
                continue
        except Exception:
            continue
    # Some Indeed forms omit accessible attributes. Allow the single textarea
    # only when its own nearby text explicitly identifies it as a cover letter.
    try:
        for el in page.query_selector_all("textarea"):
            if not el or not el.is_visible():
                continue
            context = _cover_textarea_context(el)
            if any(token in context for token in (
                "cover letter", "cover_letter", "cover-letter",
                "lettre de motivation", "lettre d'accompagnement",
            )):
                el.click()
                el.fill(letter)
                return True
    except Exception:
        pass
    return False


def _click_smartapply_continue(page) -> bool:
    for sel in (
        'button[data-testid="continue-button"]',
        'button:has-text("Continue")',
        'button:has-text("Continuer")',
        'button[type="submit"]:has-text("Continue")',
    ):
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible(timeout=1000) and btn.is_enabled(timeout=500):
                btn.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def _handle_cover_letter_screen(page, job_id: str, title: str) -> bool:
    """Upload a required PDF, or fill a clearly labelled text cover letter."""
    file_input = _cover_letter_file_input(page)
    if file_input is not None:
        pdf_path = _cover_letter_pdf_path()
        if not pdf_path:
            print_lg("  [SmartApply] Cover-letter upload required but no valid PDF is configured.")
            log_training_event(
                "cover_letter_upload_failed",
                job={**(_current_job_meta or {}), "job_id": job_id, "title": title},
                reason="cover_letter_pdf_not_configured_or_missing",
            )
            return False
        if not _upload_cover_letter_pdf(page, file_input, pdf_path, job_id, title):
            return False
        time.sleep(0.6)
        if _click_smartapply_continue(page):
            print_lg("  [SmartApply] Cover-letter upload Continue clicked.")
            time.sleep(1.2)
        return True

    company = (_current_job_meta or {}).get("company", "") or ""
    location = (_current_job_meta or {}).get("location", "") or ""
    letter = _generate_short_cover_letter(title, company, location)
    if not letter:
        return False
    if not _fill_cover_letter_textarea(page, letter):
        print_lg("  [SmartApply] Cover letter textarea not found/filled.")
        return False
    print_lg(f"  [SmartApply] Cover letter filled ({len(letter)} chars).")
    log_training_event(
        "cover_letter_filled",
        job={**(_current_job_meta or {}), "job_id": job_id, "title": title},
        chars=len(letter),
    )
    time.sleep(0.6)
    if _click_smartapply_continue(page):
        print_lg("  [SmartApply] Cover letter Continue clicked.")
        time.sleep(1.2)
    else:
        print_lg("  [SmartApply] Cover letter filled but Continue not clicked (may auto-advance).")
    return True


def _automate_smartapply(page, sb, job_id: str, title: str) -> tuple:
    """Drive the SmartApply multi-step form.  Returns (success, application_link)."""
    global pause_before_submit, pause_at_failed_question, _current_job_meta
    global _answered_field_keys, _last_smartapply_status
    # Reset per-job answered-field memo so we don't carry state from a prior
    # application but DO dedup across passes of the same form.
    _answered_field_keys = set()
    _last_smartapply_status = ""

    application_link = page.url
    print_lg(f"  [SmartApply] Automating form: {title}")
    log_training_event(
        "smartapply_started",
        job={**_current_job_meta, "job_id": job_id, "title": title},
        application_link=application_link,
        page=page_dom_snapshot(page, limit=35),
    )

    # Track whether we visited resume-selection. No-question applies often skip it
    # and land on review with a preselected account resume.
    saw_resume_step = False
    forced_resume_from_review = False
    step_counter     = 0
    review_attempts  = 0   # detect stalling on review step
    max_review_attempts = 4
    review_captcha_cycles = 0
    max_review_captcha_cycles = 3
    # Cover-letter screen must only be handled a few times.  Indeed often keeps
    # the cover-letter textarea visible after Continue (or Continue no-ops), which
    # previously re-generated the letter every step until max_steps with no submit.
    cover_letter_attempts = 0
    max_cover_letter_attempts = 2
    cover_letter_filled_once = False
    # Stuck detection: track URL across iterations so we abort if the same
    # SmartApply screen reappears too many times (root cause of the 2026-05-12
    # MSP loop where the bot ran _handle_employer_questions four+ times in a
    # row without the URL advancing).
    prev_step_url = ""
    same_url_count = 0
    max_same_url_count = 4

    for step_num in range(30):
        time.sleep(_T_STEP)

        # Dismiss "Save application progress before you exit" exit modal if present
        try:
            body = page.query_selector('body')
            if body and "save application progress" in (body.inner_text() or "").lower():
                print_lg("  [SmartApply] ⚠ Exit modal detected — attempting to close/dismiss it.")
                # Try clicking the Close 'X' button or pressing Escape
                if not _find_and_click_close_modal_button(page):
                    page.keyboard.press("Escape")
                time.sleep(1.0)
        except Exception:
            pass

        title_ok, visible_title = _smartapply_job_title_matches(page, title)
        if not title_ok:
            print_lg(
                "  [SmartApply] ⚠ Job title mismatch warning — "
                f"expected '{title}', page shows '{visible_title}'. Continuing anyway for volume."
            )


        # Cover letter screen: paste a short AI/config letter into the textarea
        # (Indeed "Write a cover letter" field — not PDF upload) and Continue.
        # Cap attempts so we never loop 15+ times generating the same letter.
        if _is_cover_letter_screen(page):
            cover_letter_attempts += 1
            if cover_letter_attempts > max_cover_letter_attempts:
                print_lg(
                    f"  [SmartApply] Cover letter still showing after "
                    f"{cover_letter_attempts - 1} fill(s) — forcing Continue / fallthrough."
                )
                advanced = _click_smartapply_continue(page)
                if not advanced:
                    try:
                        from jobbots.core.shared_modules.indeed.navigation import _click_continue_force as _ccf
                        advanced = _ccf(page)
                    except Exception:
                        advanced = False
                if advanced:
                    print_lg("  [SmartApply] Cover letter force-Continue clicked.")
                    time.sleep(1.5)
                    if not _is_cover_letter_screen(page):
                        continue
                # If still stuck on cover letter, try other form handlers below
                # rather than regenerating the letter forever.
            elif cover_letter_filled_once:
                # Already filled once — only re-click Continue, do not regenerate.
                print_lg("  [SmartApply] Cover letter already filled — re-clicking Continue.")
                advanced = _click_smartapply_continue(page)
                if not advanced:
                    try:
                        from jobbots.core.shared_modules.indeed.navigation import _click_continue_force as _ccf
                        advanced = _ccf(page)
                    except Exception:
                        advanced = False
                if advanced:
                    time.sleep(1.5)
                    continue
                # Fall through to step handlers (resume/questions/review may be co-visible)
            else:
                print_lg("  [SmartApply] Cover letter screen — uploading PDF or filling its labelled text field.")
                if _handle_cover_letter_screen(page, job_id, title):
                    cover_letter_filled_once = True
                    continue
                # Only skip if we truly cannot fill the field.
                print_lg("  [SmartApply] Cover letter fill failed — skipping job.")
                _screenshot(page, job_id, "Cover letter fill failed")
                _last_smartapply_status = "skipped_cover_letter"
                log_training_event(
                    "smartapply_finished",
                    status="skipped_cover_letter_fill_failed",
                    job={**_current_job_meta, "job_id": job_id, "title": title},
                    application_link=application_link,
                    page=page_dom_snapshot(page, limit=50),
                )
                company = (_current_job_meta or {}).get("company", "")
                location = (_current_job_meta or {}).get("location", "")
                from jobbots.core.shared_modules.indeed.persistence import _save_skipped
                _save_skipped(
                    job_id, title, company, location,
                    "Cover letter textarea fill failed",
                    job_link=application_link,
                )
                return False, application_link

        # CAPTCHA checkpoint at each step — multi-try solve, then requeue (not silent skip)
        captcha_seen = check_and_handle_captcha(
            page, sb, context=f"SmartApply step {step_num + 1}",
            run_in_background=run_in_background,
        )
        page = try_recover_page(page)
        if captcha_seen and _captcha_still_blocking(page):
            cleared = _try_clear_captcha_with_retries(
                page, sb,
                context=f"SmartApply step {step_num + 1}",
                run_in_background=run_in_background,
                rounds=4,
            )
            page = try_recover_page(page)
            if not cleared and _captcha_still_blocking(page):
                reason = _captcha_failure_reason(f"SmartApply step {step_num + 1}")
                _last_smartapply_status = "captcha_failed"
                _screenshot(page, job_id, "CAPTCHA failed")
                print_lg(f"  [SmartApply] ✗ {reason}")
                log_training_event("smartapply_finished", status="captcha_failed",
                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                   application_link=application_link,
                                   page=page_dom_snapshot(page, limit=50))
                return False, application_link
        if captcha_seen and review_attempts:
            if _is_submit_button_ready(page):
                print_lg("  [SmartApply] CAPTCHA token accepted and Submit is ready — proceeding.")
            else:
                review_captcha_cycles += 1
                print_lg(
                    "  [SmartApply] CAPTCHA handled on review page "
                    f"({review_captcha_cycles}/{max_review_captcha_cycles})."
                )
                if review_captcha_cycles > max_review_captcha_cycles:
                    _screenshot(page, job_id, "Repeated CAPTCHA on review step")
                    print_lg(
                        "  [SmartApply] ✗ CAPTCHA keeps returning on review page — "
                        "requeue (captcha), do not treat as permanent fail."
                    )
                    _last_smartapply_status = "captcha_failed"
                    log_training_event("smartapply_finished", status="repeated_review_captcha",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       diagnostics=_smartapply_review_diagnostics(page),
                                       application_link=application_link,
                                       page=page_dom_snapshot(page, limit=50))
                    return False, application_link

        url = page.url.lower()
        if "smart-apply-action=post_apply" in url or "smart-apply-action=post-apply" in url:
            print_lg(f"  [SmartApply] ✓ Post-apply redirect confirmed: {page.url}")
            log_training_event("smartapply_finished", status="submitted_post_apply_redirect",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link,
                               page=page_dom_snapshot(page, limit=35))
            return True, _smartapply_result_url(page, page.url)

        # ── URL-stuck detection ──────────────────────────────────────────
        # If the SmartApply URL hasn't changed across several iterations the
        # form isn't advancing (likely Continue is clicking but the page
        # rejects it, or we are re-running the same handler indefinitely).
        # Exception: applybyapplyablejobid (and other SPA shells) often keep
        # one URL while CAPTCHA / review / resume UI is already live.
        if url == prev_step_url:
            same_url_count += 1
        else:
            same_url_count = 0
            prev_step_url = url
        live_ui = _smartapply_live_form_ui(page)
        if same_url_count >= max_same_url_count:
            if live_ui.get("any") or captcha_seen:
                print_lg(
                    "  [SmartApply] Same URL but live form/CAPTCHA UI detected "
                    f"(submit={live_ui.get('submit')} review={live_ui.get('review')} "
                    f"resume={live_ui.get('resume')} captcha={live_ui.get('captcha')}) "
                    "— not treating as stuck."
                )
                same_url_count = 0
            else:
                print_lg(
                    f"  [SmartApply] ✗ Stuck on same URL for "
                    f"{same_url_count} iterations — aborting this job."
                )
                _screenshot(page, job_id, "Stuck on same step URL")
                log_training_event(
                    "smartapply_finished",
                    status="stuck_same_url",
                    job={**_current_job_meta, "job_id": job_id, "title": title},
                    application_link=application_link,
                    stuck_url=page.url,
                    same_url_count=same_url_count,
                    page=page_dom_snapshot(page, limit=50),
                )
                return False, application_link

        log_training_event(
            "smartapply_step",
            job={**_current_job_meta, "job_id": job_id, "title": title},
            step_num=step_num + 1,
            url=page.url,
            captcha_seen=captcha_seen,
            review_attempts=review_attempts,
            page=page_dom_snapshot(page, limit=35),
        )

        if _is_submitted(page):
            print_lg("  [SmartApply] ✓ Application submitted!")
            log_training_event("smartapply_finished", status="submitted",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link, page=page_dom_snapshot(page, limit=35))
            return True, _smartapply_result_url(page, application_link)

        if _is_already_applied_notice(page):
            print_lg("  [SmartApply] ✓ Already applied to this job.")
            log_training_event("smartapply_finished", status="already_applied",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link,
                               page=page_dom_snapshot(page, limit=35))
            _last_smartapply_status = "already_applied"
            return True, _smartapply_result_url(page, application_link)

        if SMARTAPPLY_DOMAIN not in url:
            if "applied=1" in url or _is_submitted(page):
                print_lg(f"  [SmartApply] ✓ Confirmed via redirect: {page.url}")
                log_training_event("smartapply_finished", status="submitted_redirect",
                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                   application_link=application_link, page=page_dom_snapshot(page, limit=35))
                return True, _smartapply_result_url(page, application_link)
            # Multi-tab / wrong-tab recovery: homepage often steals focus after CF or
            # login (title "Welcome, …") while SmartApply is still open elsewhere.
            recovered = None
            try:
                ctx = page.context
                for cand in list(ctx.pages):
                    try:
                        if cand.is_closed():
                            continue
                        cu = (cand.url or "").lower()
                        if SMARTAPPLY_DOMAIN in cu:
                            recovered = cand
                            break
                    except Exception:
                        continue
            except Exception:
                recovered = None
            if recovered is not None and recovered is not page:
                print_lg(
                    f"  [SmartApply] Recovered SmartApply tab after leave "
                    f"({page.url} → {recovered.url})"
                )
                page = recovered
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                application_link = page.url or application_link
                continue
            print_lg(f"  [SmartApply] Left SmartApply at step {step_num + 1}: {page.url}")
            log_training_event("smartapply_finished", status="left_smartapply",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link, page=page_dom_snapshot(page, limit=35))
            return False, application_link

        skip_continue = False

        # Recompute after submitted/already-applied checks (page may have changed).
        live_ui = _smartapply_live_form_ui(page)
        on_apply_by_id = _STEP_APPLY_BY_ID in url
        if on_apply_by_id and not live_ui.get("any"):
            print_lg(f"  [SmartApply] Redirect step {step_num + 1} — waiting…")
            time.sleep(_T_NAV)
            continue
        if on_apply_by_id and live_ui.get("any"):
            print_lg(
                "  [SmartApply] applybyapplyablejobid URL still showing, but live "
                f"UI detected (submit={live_ui.get('submit')} review={live_ui.get('review')} "
                f"resume={live_ui.get('resume')} captcha={live_ui.get('captcha')} "
                f"questions={live_ui.get('questions')}) — handling form step."
            )

        if _STEP_RESUME_SELECT in url or (on_apply_by_id and live_ui.get("resume") and not live_ui.get("submit")):
            saw_resume_step = True
            page = _handle_resume_selection(page)
            skip_continue = True

        elif _STEP_QUAL in url:
            _handle_qual_questions(page)
            step_counter = 0

        elif _STEP_EMP_QUESTIONS in url or (on_apply_by_id and live_ui.get("questions") and not live_ui.get("submit")):
            _handle_employer_questions(page)
            step_counter = 0

        elif _STEP_CONTACT in url:
            _fill_contact_info(page)
            step_counter = 0

        elif _STEP_LOCATION in url:
            _fill_location(page)
            step_counter = 0

        elif (
            _STEP_REVIEW in url
            or "review-module" in url
            or (on_apply_by_id and (live_ui.get("submit") or live_ui.get("review") or live_ui.get("captcha")))
        ):
            # ── Final submit step ────────────────────────────────────────
            tailored_path = (os.getenv("INDEED_TAILORED_RESUME_PATH") or "").strip()
            if (
                tailored_path
                and not saw_resume_step
                and not forced_resume_from_review
            ):
                print_lg(
                    "  [SmartApply] Review reached with no resume step "
                    "(preselected resume / no-questions flow) — forcing resume selection for tailored PDF."
                )
                # Wait for Resume card/Edit to render — "Preparing review" often
                # hides Edit; clicking too early falls through to broken URL rewrite.
                if _review_still_preparing(page) or not _resume_section_edit_visible(page):
                    print_lg("  [SmartApply] Waiting for Resume Edit on review before opening picker…")
                    page = _wait_for_review_resume_ui(page, timeout_s=45.0)
                    application_link = page.url or application_link
                    if _is_submitted(page):
                        print_lg("  [SmartApply] ✓ Application submitted!")
                        log_training_event("smartapply_finished", status="submitted",
                                           job={**_current_job_meta, "job_id": job_id, "title": title},
                                           application_link=application_link, page=page_dom_snapshot(page, limit=35))
                        return True, _smartapply_result_url(page, application_link)
                    if _is_already_applied_notice(page):
                        print_lg("  [SmartApply] ✓ Already applied to this job.")
                        log_training_event("smartapply_finished", status="already_applied",
                                           job={**_current_job_meta, "job_id": job_id, "title": title},
                                           application_link=application_link,
                                           page=page_dom_snapshot(page, limit=35))
                        _last_smartapply_status = "already_applied"
                        return True, _smartapply_result_url(page, application_link)
                forced_resume_from_review = True
                if _navigate_to_resume_selection_from_review(page):
                    page = try_recover_page(page)
                    application_link = page.url or application_link
                    # Edit may open picker without leaving review-module URL — handle
                    # upload here so we do not fall through to Submit with preselected resume.
                    print_lg("  [SmartApply] Resume picker open from review — selecting/uploading tailored resume…")
                    saw_resume_step = True
                    page = _handle_resume_selection(page)
                    page = try_recover_page(page)
                    application_link = page.url or application_link
                    skip_continue = True
                    continue
                print_lg("  [SmartApply] Could not open resume selection from review — continuing with preselected resume.")

            # Indeed often shows "Preparing review" / 100% with no Submit yet.
            # Wait for the real review UI before counting attempts / same-URL aborts.
            if _review_still_preparing(page) or not _is_submit_button_ready(page):
                print_lg("  [SmartApply] Review UI still preparing — waiting for Submit button…")
                page = _wait_for_review_submit_ready(page, timeout_s=60.0)
                if _is_submitted(page):
                    print_lg("  [SmartApply] ✓ Application submitted!")
                    log_training_event("smartapply_finished", status="submitted",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       application_link=application_link, page=page_dom_snapshot(page, limit=35))
                    return True, _smartapply_result_url(page, application_link)
                if _is_already_applied_notice(page):
                    print_lg("  [SmartApply] ✓ Already applied to this job.")
                    log_training_event("smartapply_finished", status="already_applied",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       application_link=application_link,
                                       page=page_dom_snapshot(page, limit=35))
                    _last_smartapply_status = "already_applied"
                    return True, _smartapply_result_url(page, application_link)
                if not _is_submit_button_ready(page):
                    print_lg("  [SmartApply] Review prepare timed out — Submit never appeared.")
                    # Don't burn the same-URL budget while Indeed is still spinning.
                    same_url_count = max(0, same_url_count - 1)
                    skip_continue = True
                    time.sleep(2.0)
                    continue

            review_attempts += 1
            print_lg(f"  [SmartApply] Review step — submitting… (attempt {review_attempts})")

            # Check for submission trouble modal first
            if _is_submission_error_modal_present(page):
                print_lg(f"  [SmartApply] ⚠ Submission trouble modal detected on review step (attempt {review_attempts}).")
                if review_attempts >= 2:
                    print_lg("  [SmartApply] Clicking 'Save job and exit' to exit cleanly...")
                    _screenshot(page, job_id, "Submission trouble save and exit")
                    if _find_and_click_save_job_and_exit_button(page):
                        time.sleep(2)
                    _last_smartapply_status = "submission_trouble_saved"
                    log_training_event("smartapply_finished", status="submission_trouble_saved",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       application_link=application_link,
                                       page=page_dom_snapshot(page, limit=35))
                    return False, application_link
                else:
                    print_lg("  [SmartApply] Closing error modal to retry submit...")
                    _screenshot(page, job_id, "Closing submission trouble modal")
                    if _find_and_click_close_modal_button(page):
                        time.sleep(1.5)

            title_ok, visible_title = _smartapply_job_title_matches(page, title)
            if not title_ok:
                print_lg(
                    "  [SmartApply] ⚠ Job title mismatch on review warning — "
                    f"expected '{title}', page shows '{visible_title}'. Continuing anyway."
                )


            if review_attempts > max_review_attempts:
                # Stuck on review page — give up
                _screenshot(page, job_id, "Stuck on review step")
                print_lg("  [SmartApply] ✗ Stuck on review page — aborting.")
                log_training_event("smartapply_finished", status="stuck_on_review",
                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                   diagnostics=_smartapply_review_diagnostics(page),
                                   application_link=application_link,
                                   page=page_dom_snapshot(page, limit=50))
                return False, application_link

            # Optional user confirmation
            if pause_before_submit and not run_in_background and review_attempts == 1:
                decision = pyautogui.confirm(
                    '1. Please verify your information.\n'
                    '2. DO NOT CLICK "Submit Application" manually.\n\n'
                    'You can turn off "pause_before_submit" in config/questions.py',
                    "Confirm Your Information",
                    ["Disable Pause", "Discard Application", "Submit Application"]
                )
                if decision == "Discard Application":
                    print_lg("  [SmartApply] Application discarded.")
                    return False, application_link
                if decision == "Disable Pause":
                    pause_before_submit = False

            # ── STEP 1: Solve reCAPTCHA v2 widget FIRST ──────────────────
            # The anchor iframe (title='reCAPTCHA') is confirmed ALWAYS visible
            # on the review page (from HTML dump analysis).  It MUST be solved
            # before clicking Submit or the form submission is silently blocked.
            if is_recaptcha_expired(page):
                print_lg("  [SmartApply] reCAPTCHA expired — solving again BEFORE Submit…")
                if not handle_recaptcha_widget(page, sb, run_in_background=run_in_background):
                    print_lg("  [SmartApply] ✗ reCAPTCHA solve failed — skipping this job.")
                    return False, application_link
                time.sleep(max(0.8, _T_NAV * 2))
                page = try_recover_page(page)
                if _captcha_still_blocking(page):
                    print_lg("  [SmartApply] ✗ reCAPTCHA still blocking — skipping this job.")
                    return False, application_link
                review_attempts = 0
                skip_continue = True
                continue

            if is_recaptcha_challenge(page):
                print_lg("  [SmartApply] reCAPTCHA image challenge open — solving BEFORE Submit…")
                if not handle_recaptcha_challenge(page, sb, timeout=90, run_in_background=run_in_background):
                    print_lg("  [SmartApply] ✗ reCAPTCHA image solve failed — skipping this job.")
                    return False, application_link
                time.sleep(max(0.8, _T_NAV * 2))
                page = try_recover_page(page)
                if _captcha_still_blocking(page):
                    print_lg("  [SmartApply] ✗ reCAPTCHA still blocking — skipping this job.")
                    return False, application_link
                review_captcha_cycles += 1
                if review_captcha_cycles > max_review_captcha_cycles:
                    _screenshot(page, job_id, "Repeated CAPTCHA before submit")
                    print_lg("  [SmartApply] ✗ reCAPTCHA keeps reopening before submit — aborting this job.")
                    log_training_event("smartapply_finished", status="repeated_review_captcha",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       diagnostics=_smartapply_review_diagnostics(page),
                                       application_link=application_link,
                                       page=page_dom_snapshot(page, limit=50))
                    return False, application_link
                if _is_submit_button_ready(page):
                    print_lg("  [SmartApply] CAPTCHA solved and Submit is ready — submitting now.")
                elif is_recaptcha_challenge(page) or is_recaptcha_expired(page):
                    print_lg("  [SmartApply] reCAPTCHA still not clear — will retry before submitting")
                    skip_continue = True
                    continue

            if is_recaptcha_widget_present(page):
                if _is_submit_button_ready(page):
                    print_lg("  [SmartApply] reCAPTCHA checkbox visible, but Submit is enabled — trying Submit first.")
                else:
                    print_lg("  [SmartApply] reCAPTCHA v2 widget detected — solving BEFORE Submit…")
                    if not handle_recaptcha_widget(page, sb, run_in_background=run_in_background):
                        print_lg("  [SmartApply] ✗ reCAPTCHA widget solve failed — skipping this job.")
                        return False, application_link
                    time.sleep(max(0.8, _T_NAV * 2))   # let reCAPTCHA settle briefly
                    page = try_recover_page(page)
                    if _captcha_still_blocking(page):
                        print_lg("  [SmartApply] ✗ reCAPTCHA still blocking — skipping this job.")
                        return False, application_link
                    review_captcha_cycles += 1
                    if review_captcha_cycles > max_review_captcha_cycles:
                        _screenshot(page, job_id, "Repeated CAPTCHA widget")
                        print_lg("  [SmartApply] ✗ reCAPTCHA widget keeps returning — aborting this job.")
                        log_training_event("smartapply_finished", status="repeated_review_captcha",
                                           job={**_current_job_meta, "job_id": job_id, "title": title},
                                           diagnostics=_smartapply_review_diagnostics(page),
                                           application_link=application_link,
                                           page=page_dom_snapshot(page, limit=50))
                        return False, application_link
                    # Re-check: if still stuck return to outer loop for retry
                    if _is_submit_button_ready(page):
                        print_lg("  [SmartApply] CAPTCHA widget solved and Submit is ready — submitting now.")
                    elif is_recaptcha_widget_present(page) or is_recaptcha_challenge(page) or is_recaptcha_expired(page):
                        print_lg("  [SmartApply] reCAPTCHA widget still present after solve attempt — will retry")
                        skip_continue = True
                        continue

            # ── STEP 2: Click Submit ──────────────────────────────────────
            if _click_submit_button(page):
                print_lg("  [SmartApply] Submit clicked — waiting for confirmation / CAPTCHA…")

                # Poll briefly: check for reCAPTCHA image challenge or confirmation.
                # Indeed often redirects quickly; long waits make failed/stale submits drag.
                deadline = time.time() + 20
                submitted = False
                while time.time() < deadline:
                    time.sleep(1)

                    # Trouble submitting modal?
                    if _is_submission_error_modal_present(page):
                        print_lg(f"  [SmartApply] ⚠ Submission trouble modal detected after click! (attempt {review_attempts})")
                        _screenshot(page, job_id, "Submission trouble modal after click")
                        if review_attempts >= 2:
                            print_lg("  [SmartApply] Clicking 'Save job and exit' to exit cleanly...")
                            if _find_and_click_save_job_and_exit_button(page):
                                time.sleep(2)
                            _last_smartapply_status = "submission_trouble_saved"
                            log_training_event("smartapply_finished", status="submission_trouble_saved",
                                               job={**_current_job_meta, "job_id": job_id, "title": title},
                                               application_link=application_link,
                                               page=page_dom_snapshot(page, limit=35))
                            return False, application_link
                        else:
                            print_lg("  [SmartApply] Closing error modal to retry submit...")
                            if _find_and_click_close_modal_button(page):
                                time.sleep(1.5)
                            break

                    # reCAPTCHA image challenge? (buses, fire hydrants, pumps…)
                    if is_recaptcha_challenge(page):
                        print_lg("  [SmartApply] reCAPTCHA image challenge after submit!")
                        if handle_recaptcha_challenge(
                            page, sb, timeout=90, run_in_background=run_in_background
                        ):
                            print_lg("  [SmartApply] CAPTCHA solved after submit. Re-clicking Submit...")
                            review_captcha_cycles += 1
                            if review_captcha_cycles > max_review_captcha_cycles:
                                _screenshot(page, job_id, "Repeated CAPTCHA after submit")
                                print_lg("  [SmartApply] ✗ reCAPTCHA keeps returning after submit — aborting this job.")
                                log_training_event("smartapply_finished", status="repeated_review_captcha",
                                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                                   diagnostics=_smartapply_review_diagnostics(page),
                                                   application_link=application_link,
                                                   page=page_dom_snapshot(page, limit=50))
                                return False, application_link
                            _click_submit_button(page)
                            deadline = time.time() + 20
                        else:
                            print_lg("  [SmartApply] ✗ reCAPTCHA solve failed after submit — skipping this job.")
                            return False, application_link
                        time.sleep(1)
                        page = try_recover_page(page)

                    # Cloudflare?
                    if is_cloudflare_challenge(page):
                        if handle_cloudflare_challenge(page, sb, run_in_background=run_in_background):
                            print_lg("  [SmartApply] Cloudflare cleared after submit — resetting review retry counter and re-clicking Submit.")
                            review_attempts = 0
                            _click_submit_button(page)
                            deadline = time.time() + 20
                        time.sleep(1)
                        page = try_recover_page(page)

                    # Submitted?
                    if _is_submitted(page):
                        submitted = True
                        break

                    if _is_already_applied_notice(page):
                        print_lg("  [SmartApply] ✓ Already applied to this job.")
                        log_training_event("smartapply_finished", status="already_applied",
                                           job={**_current_job_meta, "job_id": job_id, "title": title},
                                           application_link=application_link,
                                           page=page_dom_snapshot(page, limit=35))
                        _last_smartapply_status = "already_applied"
                        return True, _smartapply_result_url(page, application_link)

                    # URL changed away from SmartApply?
                    cur = page.url.lower()
                    if SMARTAPPLY_DOMAIN not in cur:
                        if _is_already_applied_notice(page):
                            _last_smartapply_status = "already_applied"
                            return True, _smartapply_result_url(page, page.url)
                        if "applied=1" in cur or _is_submitted(page):
                            return True, _smartapply_result_url(page, page.url)
                        break

                    # URL changed off review page?
                    if _STEP_REVIEW not in cur and "review-module" not in cur:
                        break  # progressed to next step — let outer loop handle it

                if submitted:
                    print_lg("  [SmartApply] ✓ Application submitted!")
                    log_training_event("smartapply_finished", status="submitted_after_click",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       application_link=application_link,
                                       page=page_dom_snapshot(page, limit=35))
                    return True, _smartapply_result_url(page, application_link)

                # Check once more
                if _is_submitted(page):
                    print_lg("  [SmartApply] ✓ Application submitted!")
                    log_training_event("smartapply_finished", status="submitted_after_click",
                                       job={**_current_job_meta, "job_id": job_id, "title": title},
                                       application_link=application_link,
                                       page=page_dom_snapshot(page, limit=35))
                    return True, _smartapply_result_url(page, application_link)

                try:
                    cur_after_submit = page.url
                except Exception:
                    cur_after_submit = "?"
                print_lg(
                    "  [SmartApply] No confirmation after submit wait — "
                    f"still at {cur_after_submit} "
                    f"({_smartapply_review_diagnostics(page)})"
                )
                log_training_event("submit_unconfirmed",
                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                   url=cur_after_submit,
                                   diagnostics=_smartapply_review_diagnostics(page),
                                   page=page_dom_snapshot(page, limit=50))

            # Skip generic _click_continue_force for review step
            # (we already tried the specific button above)
            skip_continue = True
            continue

        elif _STEP_PRIVACY in url or "visibility" in url:
            _handle_visibility(page)
            step_counter = 0

        elif _STEP_EXPERIENCE in url:
            _handle_experience(page)
            step_counter = 0

        elif _STEP_RESUME in url:
            _handle_resume(page)
            step_counter = 0

        else:
            step_counter += 1
            print_lg(f"  [SmartApply] Unrecognised step {step_num + 1}: {url}")
            _handle_employer_questions(page)

            if step_counter >= 15:
                if pause_at_failed_question and not run_in_background:
                    _screenshot(page, job_id, "Needed manual intervention")
                    pyautogui.alert(
                        "Couldn't answer questions automatically.\n"
                        "Please click OK once done answering manually.\n"
                        "DO NOT click Next/Back in the SmartApply window.\n\n"
                        "Disable: pause_at_failed_question = False in config/questions.py",
                        "Help Needed", "Continue"
                    )
                    step_counter = 0
                    continue
                _screenshot(page, job_id, "Failed at questions")
                try:  # capture the unresolved question AREA before the drop (all SmartApply-driven portals)
                    import os as _os
                    from jobbots.core.apply_diagnostics import capture_unhandled_question
                    capture_unhandled_question(
                        page,
                        portal=_os.getenv("BOT_NAME") or "indeed",
                        job_id=job_id,
                        reason="failed_at_questions",
                    )
                except Exception:
                    pass
                log_training_event("smartapply_finished", status="failed_at_questions",

                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                   application_link=application_link,
                                   page=page_dom_snapshot(page, limit=50))
                return False, application_link

        time.sleep(_T_ACTION)
        if skip_continue:
            continue

        # Indeed can complete the submission asynchronously while the form
        # handler is still on the previous step.  In that case the page is
        # already on post-apply and there is intentionally no Continue button.
        # Treat the confirmed post-apply surface as success instead of a false
        # "continue button missing" failure.
        if _is_submitted(page):
            print_lg("  [SmartApply] Submission confirmed before Continue check.")
            return True, application_link or page.url

        prev_url = url
        if not _click_continue_force(page):
            print_lg(f"  [SmartApply] ✗ No Continue button at step {step_num + 1}")
            log_training_event("smartapply_finished", status="continue_button_missing",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               step_num=step_num + 1,
                               application_link=application_link,
                               page=page_dom_snapshot(page, limit=50))
            return False, application_link

        time.sleep(_T_NAV)

        # Watch for CAPTCHA right after clicking Continue
        watch_for_captcha_after_submit(page, sb, poll_seconds=2, max_wait=12,
                                       run_in_background=run_in_background)

        try:
            current_after_captcha = page.url.lower()
        except Exception:
            current_after_captcha = ""
        if (
            (_STEP_REVIEW in current_after_captcha or "review-module" in current_after_captcha)
            and _is_submit_button_ready(page)
        ):
            if pause_before_submit and not run_in_background:
                print_lg(
                    "  [SmartApply] Review is ready after CAPTCHA — pause_before_submit is enabled, "
                    "so not auto-clicking Submit."
                )
                skip_continue = True
                continue
            print_lg("  [SmartApply] Post-CAPTCHA Submit is ready — clicking now.")
            if _click_submit_button(page):
                deadline = time.time() + 20
                while time.time() < deadline:
                    time.sleep(1)
                    if _is_submitted(page):
                        print_lg("  [SmartApply] ✓ Application submitted!")
                        log_training_event("smartapply_finished", status="submitted_post_captcha",
                                           job={**_current_job_meta, "job_id": job_id, "title": title},
                                           application_link=application_link,
                                           page=page_dom_snapshot(page, limit=35))
                        return True, _smartapply_result_url(page, application_link)
                    if _is_already_applied_notice(page):
                        print_lg("  [SmartApply] ✓ Already applied to this job.")
                        log_training_event("smartapply_finished", status="already_applied",
                                           job={**_current_job_meta, "job_id": job_id, "title": title},
                                           application_link=application_link,
                                           page=page_dom_snapshot(page, limit=35))
                        _last_smartapply_status = "already_applied"
                        return True, _smartapply_result_url(page, application_link)
                    try:
                        cur = page.url.lower()
                    except Exception:
                        cur = ""
                    if SMARTAPPLY_DOMAIN not in cur:
                        if _is_already_applied_notice(page):
                            _last_smartapply_status = "already_applied"
                            return True, _smartapply_result_url(page, page.url)
                        if "applied=1" in cur or _is_submitted(page):
                            return True, _smartapply_result_url(page, page.url)
                        break

        if _is_submitted(page):
            print_lg("  [SmartApply] ✓ Application submitted!")
            log_training_event("smartapply_finished", status="submitted",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link,
                               page=page_dom_snapshot(page, limit=35))
            return True, _smartapply_result_url(page, application_link)

        if _is_already_applied_notice(page):
            print_lg("  [SmartApply] ✓ Already applied to this job.")
            log_training_event("smartapply_finished", status="already_applied",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link,
                               page=page_dom_snapshot(page, limit=35))
            _last_smartapply_status = "already_applied"
            return True, _smartapply_result_url(page, application_link)

        new_url = page.url
        if SMARTAPPLY_DOMAIN not in new_url.lower():
            if "applied=1" in new_url.lower() or _is_submitted(page):
                print_lg(f"  [SmartApply] ✓ Confirmed: {new_url}")
                log_training_event("smartapply_finished", status="submitted_redirect",
                                   job={**_current_job_meta, "job_id": job_id, "title": title},
                                   application_link=new_url,
                                   page=page_dom_snapshot(page, limit=35))
                return True, new_url
            print_lg(f"  [SmartApply] Left SmartApply: {new_url}")
            log_training_event("smartapply_finished", status="left_smartapply",
                               job={**_current_job_meta, "job_id": job_id, "title": title},
                               application_link=application_link,
                               page=page_dom_snapshot(page, limit=35))
            return False, application_link

    print_lg("  [SmartApply] ✗ Exceeded max steps.")
    log_training_event("smartapply_finished", status="max_steps_exceeded",
                       job={**_current_job_meta, "job_id": job_id, "title": title},
                       application_link=application_link,
                       page=page_dom_snapshot(page, limit=50))
    return False, application_link


# ─────────────────────────────────────────────────────────────────────────────
# Single-job application  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

_INDEED_BASE = "https://ca.indeed.com"


def _make_absolute_url(href: str) -> str:
    """Convert a relative Indeed URL to an absolute one."""
    if not href:
        return href
    if href.startswith('http'):
        return href
    if href.startswith('//'):
        return f"https:{href}"
    # Relative path — prepend base
    return f"{_INDEED_BASE}{href if href.startswith('/') else '/' + href}"


def _preferred_job_urls(job_id: str, job_href: str) -> tuple[str, str]:
    """Return (preferred Indeed detail URL, fallback card href)."""
    job_href = _make_absolute_url(job_href)
    job_link = (f"{_INDEED_BASE}/viewjob?jk={job_id}"
                if job_id != 'Unknown' else job_href)
    job_link = _make_absolute_url(job_link)
    return job_link, job_href


def _search_pane_job_url(search_url: str, job_id: str) -> str:
    """Build the Indeed search-results two-pane URL for a selected job."""
    if not search_url or not job_id or job_id == 'Unknown':
        return ""
    try:
        parsed = urlparse(search_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["vjk"] = job_id
        return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        return ""


def _page_matches_job(page, job_id: str, nav_candidates: list[str]) -> bool:
    """Return True when the current page already appears to be the target job."""
    try:
        cur_url = _make_absolute_url(page.url or "")
    except Exception:
        return False

    if not cur_url:
        return False

    if job_id and job_id != 'Unknown' and _extract_job_id_from_url(cur_url) == job_id:
        return True

    return any(candidate and cur_url.startswith(candidate) for candidate in nav_candidates)


def _page_ready_for_job_apply(page, job_id: str, title: str, nav_candidates: list[str]) -> bool:
    if not _page_matches_job(page, job_id, nav_candidates):
        return False
    if _page_has_job_detail(page):
        return True
    try:
        return _open_job_detail_from_results(page, job_id, title)
    except Exception:
        return False
