from ._bootstrap import *  # noqa: F403

def _fill_contact_info(page) -> None:
    print_lg("    [SmartApply] Filling contact info…")
    for sel in ["input[name='firstName']", "input[id*='first']",
                "input[placeholder*='First']", "input[autocomplete='given-name']"]:
        el = page.query_selector(sel)
        if el:
            if not el.get_attribute('value'):
                _type_into(page, el, first_name)
            break

    for sel in ["input[name='lastName']", "input[id*='last']",
                "input[placeholder*='Last']", "input[autocomplete='family-name']"]:
        el = page.query_selector(sel)
        if el:
            if not el.get_attribute('value'):
                _type_into(page, el, last_name)
            break

    # Country code dropdown — covers both classic and new Indeed screener layout
    # (new layout: label='Select Country Code and enter Preferred Phone Number')
    cc_el = page.query_selector(
        "select[id*='country'], select[name*='country'], "
        "select[aria-label*='country' i], select[id*='phoneCountry'], "
        "select[name*='phoneCountry'], "
        "select[aria-label*='country code' i], select[id*='countryCode'], "
        "select[name*='countryCode']"
    )
    if cc_el:
        try:
            opts = cc_el.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
            for pref in ("Canada (+1)", "Canada", "+1", "CA"):
                for opt in opts:
                    if pref.lower() in opt.lower():
                        cc_el.select_option(label=opt)
                        break
                else:
                    continue
                break
        except Exception:
            pass

    local_ph = _local_phone(phone_number) if phone_number else ""
    for sel in [
        # classic Indeed SmartApply
        "input[name='phoneNumber']", "input[id*='phone']",
        "input[type='tel']", "input[placeholder*='Phone']",
        "input[autocomplete='tel']", "input[id*='Phone']", "input[name*='phone' i]",
        # new Indeed screener (no type=tel, plain text input near 'Preferred Phone Number')
        "input[placeholder*='phone number' i]", "input[id*='preferredPhone' i]",
        "input[name*='preferredPhone' i]",
    ]:
        el = page.query_selector(sel)
        if el:
            cur = el.get_attribute('value') or ''
            if not cur or any(c in cur for c in ('+', '-', '.')):
                _type_into(page, el, local_ph)
            break

    # New Indeed screener: country + state/province below phone number
    # Handles: "Select your country then your state/province" dropdowns
    country_el = page.query_selector(
        "select[aria-label*='country' i]:not([id*='phoneCountry']):not([name*='phoneCountry']):not([id*='countryCode']):not([name*='countryCode']), "
        "select[id*='applicantCountry' i], select[name*='applicantCountry' i]"
    )
    if country_el:
        try:
            opts = country_el.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
            for pref in ("Canada", "CA"):
                for opt in opts:
                    if pref.lower() == opt.lower() or opt.lower().startswith(pref.lower()):
                        country_el.select_option(label=opt)
                        break
                else:
                    continue
                break
        except Exception:
            pass

    province_el = page.query_selector(
        "select[id*='province' i], select[name*='province' i], "
        "select[aria-label*='province' i], select[aria-label*='state/province' i], "
        "select[id*='applicantProvince' i], select[name*='applicantProvince' i]"
    )
    if province_el:
        try:
            opts = province_el.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
            for pref in (state or "BC", "British Columbia"):
                for opt in opts:
                    if pref.lower() == opt.lower() or opt.lower().startswith(pref.lower()):
                        province_el.select_option(label=opt)
                        break
                else:
                    continue
                break
        except Exception:
            pass


# ── Location ──────────────────────────────────────────────────────────────────

def _fill_location(page) -> None:
    print_lg("    [SmartApply] Filling location…")
    el = page.query_selector("select[id*='country'], select[name*='country'], select[aria-label*='country' i]")
    if el:
        try:
            el.select_option(label="Canada")
        except Exception:
            try:
                el.select_option(value="CA")
            except Exception:
                pass

    for sel in ["input[id*='postal']", "input[id*='zip']", "input[name*='postal']",
                "input[placeholder*='Postal']", "input[autocomplete='postal-code']"]:
        el = page.query_selector(sel)
        if el:
            val = (el.get_attribute('value') or '').strip().replace(" ", "").lower()
            target = (zipcode or '').strip().replace(" ", "").lower()
            if not val or val != target:
                _type_into(page, el, zipcode)
            break

    for sel in ["input[id*='city']", "input[name*='city']",
                "input[placeholder*='City']", "input[autocomplete='address-level2']"]:
        el = page.query_selector(sel)
        if el:
            val = (el.get_attribute('value') or '').strip().lower()
            target = (current_city or "Surrey").strip().lower()
            if not val or val != target:
                _type_into(page, el, current_city or "Surrey")
            break

    for sel in ["input[id*='street']", "input[name*='street']",
                "input[placeholder*='Street']", "input[autocomplete='street-address']"]:
        el = page.query_selector(sel)
        if el:
            val = (el.get_attribute('value') or '').strip().lower()
            target = (street or '').strip().lower()
            if not val or val != target:
                _type_into(page, el, street)
            break

    for sel in ["select[id*='state']", "select[name*='state']", "select[id*='province']", "select[name*='province']", "select[aria-label*='state' i]", "select[aria-label*='province' i]",
                "input[id*='state']", "input[name*='state']", "input[id*='province']", "input[name*='province']", "input[placeholder*='State']", "input[placeholder*='Province']", "input[autocomplete='address-level1']"]:
        el = page.query_selector(sel)
        if el:
            tag = el.evaluate("el => el.tagName.toLowerCase()")
            target_state = state or "BC"
            if tag == "select":
                try:
                    opts = el.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
                    matched = False
                    for pref in (target_state, "British Columbia"):
                        for opt in opts:
                            if pref.lower() == opt.lower() or opt.lower().startswith(pref.lower()):
                                el.select_option(label=opt)
                                matched = True
                                break
                        if matched:
                            break
                except Exception:
                    pass
            else:
                val = (el.get_attribute('value') or '').strip().lower()
                if not val or val != target_state.lower():
                    _type_into(page, el, target_state)
            break



# ── Resume upload ─────────────────────────────────────────────────────────────

def _resolve_resume_path() -> str:
    import os
    from pathlib import Path
    from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
    from config.questions import default_resume_path

    
    tailored_path = os.getenv("INDEED_TAILORED_RESUME_PATH")
    if tailored_path and os.path.isfile(tailored_path):
        return tailored_path
    
    # Locate monorepo root dynamically starting from current file
    module_dir = Path(__file__).resolve().parent
    # Canonical anchor (Phase 2): the monorepo root no longer contains
    # core/supervised_bots.py, so resolve it directly instead of walking.
    monorepo_root = _MONOREPO_ROOT
    repo_root = _MONOREPO_ROOT.parent
    
    candidates = []
    if default_resume_path:
        candidates.append(default_resume_path)
        
    candidates.extend([
        "profiles/resumes/sample_resume_general.pdf",
        "profiles/resumes/sample_resume_it.pdf",
        "resume.pdf",
        "sample_resume.pdf",
    ])
    
    for cand in candidates:
        if not cand:
            continue
            
        # Try raw absolute path
        abs_cand = Path(os.path.expanduser(cand)).resolve()
        if abs_cand.is_file():
            return str(abs_cand)
            
        # Try relative to monorepo root
        if monorepo_root:
            p = (monorepo_root / cand).resolve()
            if p.is_file():
                return str(p)
                
        # Try relative to repo root
        if repo_root:
            p = (repo_root / cand).resolve()
            if p.is_file():
                return str(p)
            # Try searching under monorepo inside repo root
            p_monorepo = (repo_root / "automation_monorepo" / cand).resolve()
            if p_monorepo.is_file():
                return str(p_monorepo)
                
        # Try relative to CWD
        p_cwd = Path(cand).resolve()
        if p_cwd.is_file():
            return str(p_cwd)
            
    # Glob fallback: search for any PDF file in 'all resumes'
    if monorepo_root:
        for p in monorepo_root.glob("all resumes/*.pdf"):
            if p.is_file():
                return str(p)
                
    return ""



def _debug_resume_upload_ui(page) -> dict:
    """Snapshot resume-upload DOM for debugging (Indeed-side picker often breaks)."""
    info: dict = {
        "file_inputs": 0,
        "file_inputs_enabled": 0,
        "upload_buttons": [],
        "checked_label": "",
        "url": "",
    }
    try:
        info["url"] = (page.url or "")[:180]
        info["checked_label"] = (_read_selected_resume_label(page) or "")[:120]
        inputs = page.query_selector_all("input[type='file']")
        info["file_inputs"] = len(inputs)
        for fi in inputs:
            try:
                disabled = bool(fi.is_disabled()) if hasattr(fi, "is_disabled") else False
                visible = bool(fi.is_visible()) if hasattr(fi, "is_visible") else False
                if not disabled:
                    info["file_inputs_enabled"] += 1
                print_lg(
                    f"      [ResumeDebug] file_input visible={visible} disabled={disabled} "
                    f"accept={fi.get_attribute('accept')!r}"
                )
            except Exception as exc:
                print_lg(f"      [ResumeDebug] file_input inspect error: {exc}")
        for btn_text in (
            "Upload a resume", "Upload resume", "Use a different resume",
            "Upload a different resume", "Replace resume", "Add a resume",
        ):
            btn = page.query_selector(f"button:has-text('{btn_text}')") or page.query_selector(
                f"a:has-text('{btn_text}')"
            )
            if not btn:
                continue
            try:
                visible = bool(btn.is_visible()) if hasattr(btn, "is_visible") else True
                disabled = bool(btn.is_disabled()) if hasattr(btn, "is_disabled") else False
                info["upload_buttons"].append(
                    {"text": btn_text, "visible": visible, "disabled": disabled}
                )
                print_lg(
                    f"      [ResumeDebug] button '{btn_text}' visible={visible} disabled={disabled}"
                )
            except Exception as exc:
                print_lg(f"      [ResumeDebug] button '{btn_text}' inspect error: {exc}")
        print_lg(
            f"      [ResumeDebug] url={info['url']!r} checked_label={info['checked_label']!r} "
            f"file_inputs={info['file_inputs']} enabled={info['file_inputs_enabled']} "
            f"upload_btns={len(info['upload_buttons'])}"
        )
    except Exception as exc:
        print_lg(f"      [ResumeDebug] snapshot failed: {exc}")
    return info


def _upload_tailored_resume(page, resolved_path: str) -> bool:
    import time
    import os
    from pathlib import Path

    resume_path = Path(resolved_path)
    print_lg(f"    [ResumeUpload] Attempting upload: {resolved_path}")
    if not resume_path.is_file():
        print_lg(f"    [ResumeUpload] ✗ path does not exist: {resolved_path}")
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta("resume_upload_failed", reason=f"missing_file:{resolved_path}")
        return False
    try:
        header = resume_path.read_bytes()[:4]
        size = resume_path.stat().st_size
    except Exception as e:
        print_lg(f"    [ResumeUpload] ✗ could not read file: {e}")
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta("resume_upload_failed", reason=f"read_error:{e}")
        return False
    if header != b"%PDF" or size < 10_000:
        print_lg(
            "    [ResumeUpload] ✗ refusing non-PDF / tiny file "
            f"(bytes={size}, header={header!r}): {resolved_path}"
        )
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta(
            "resume_upload_failed", reason=f"invalid_pdf:bytes={size}:header={header!r}"
        )
        return False

    print_lg(f"    [ResumeUpload] local file OK ({size} bytes); probing Indeed upload UI…")
    ui_before = _debug_resume_upload_ui(page)

    # 1. Check if file input is visible. If not, look for and click the trigger button
    if not page.query_selector("input[type='file']"):
        clicked = False
        for btn_text in [
            "Upload a resume", "Upload resume", "Use a different resume",
            "Upload a different resume", "Replace resume",
        ]:
            btn = page.query_selector(f"button:has-text('{btn_text}')") or page.query_selector(
                f"a:has-text('{btn_text}')"
            )
            if btn:
                print_lg(f"    [ResumeUpload] Clicking uploader trigger '{btn_text}'")
                try:
                    btn.click(force=True)
                    clicked = True
                except Exception as exc:
                    print_lg(f"    [ResumeUpload] trigger click failed: {type(exc).__name__}: {exc}")
                time.sleep(1.0)
                break
        if not clicked:
            print_lg("    [ResumeUpload] ✗ no file input and no upload trigger button found")
            from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
            log_job_status_event_from_meta(
                "resume_upload_blocked",
                reason=(
                    f"no_file_input_or_trigger:label={ui_before.get('checked_label')!r}:"
                    f"btns={ui_before.get('upload_buttons')}"
                ),
            )
            _debug_resume_upload_ui(page)
            return False

    fi = page.query_selector("input[type='file']")
    if not fi:
        print_lg("    [ResumeUpload] ✗ file input still missing after trigger click (Indeed UI)")
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta(
            "resume_upload_blocked",
            reason="file_input_missing_after_trigger_click",
        )
        _debug_resume_upload_ui(page)
        return False

    try:
        disabled = bool(fi.is_disabled()) if hasattr(fi, "is_disabled") else False
    except Exception:
        disabled = False
    if disabled:
        print_lg("    [ResumeUpload] ✗ file input is disabled (Indeed-side)")
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta("resume_upload_blocked", reason="file_input_disabled")
        return False

    try:
        fi.set_input_files(resolved_path)
        print_lg(f"    [ResumeUpload] set_input_files OK: {resolved_path} ({size} bytes)")
    except Exception as exc:
        print_lg(f"    [ResumeUpload] ✗ set_input_files failed: {type(exc).__name__}: {exc}")
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta(
            "resume_upload_blocked", reason=f"set_input_files:{type(exc).__name__}:{exc}"
        )
        return False

    filename = os.getenv("INDEED_TAILORED_RESUME_PATH")
    if filename:
        filename = os.path.basename(filename)
    else:
        filename = os.path.basename(resolved_path)

    try:
        page.wait_for_selector(f"text={filename}", timeout=10000)
        print_lg(f"    [ResumeUpload] ✓ verified on page: {filename}")
    except Exception:
        print_lg(
            "    [ResumeUpload] verification timeout for filename on page; "
            "sleeping 5s (Indeed may still be processing)"
        )
        time.sleep(5)

    after_label = _read_selected_resume_label(page)
    print_lg(f"    [ResumeUpload] after upload, Indeed label: '{(after_label or '')[:120]}'")
    from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
    log_job_status_event_from_meta(
        "resume_upload_attempted",
        reason=f"path={filename}:after_label={(after_label or '')[:80]}",
    )
    return True


def _handle_resume(page) -> None:
    import os
    import time
    global _use_new_resume
    print_lg("    [SmartApply] Handling resume step…")

    resolved_path = _resolve_resume_path()
    is_tailored = bool(os.getenv("INDEED_TAILORED_RESUME_PATH"))
    selected_label = _read_selected_resume_label(page)
    if selected_label:
        print_lg(f"      [ResumeAwareness] current Indeed resume: '{selected_label[:120]}'")
    label_ok = _resume_label_matches_expected(selected_label, resolved_path)
    print_lg(
        f"      [ResumeAwareness] tailored={is_tailored} intended={resolved_path or 'none'} "
        f"label_matches_intended={label_ok}"
    )

    if is_tailored and resolved_path:
        if _upload_tailored_resume(page, resolved_path):
            _use_new_resume = False
            return
        print_lg("      [ResumeAwareness] tailored upload failed; falling back to existing selection")

    radios = page.query_selector_all("input[type='radio']")
    if radios:
        for r in radios:
            rid = r.get_attribute("id") or ""
            lbl = page.query_selector(f'label[for="{rid}"]') if rid else None
            ltext = lbl.inner_text().lower() if lbl else ""
            if "build" not in ltext:
                if not r.is_checked():
                    r.click(force=True)
                shown = (lbl.inner_text() if lbl else selected_label or "")[:120]
                print_lg(f"      [ResumeAwareness] accepting existing resume radio: '{shown}'")
                from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
                log_job_status_event_from_meta(
                    "resume_selected_existing_match" if label_ok else "resume_selected_existing_unverified",
                    reason=f"label={shown}",
                )
                return
        if not radios[0].is_checked():
            radios[0].click(force=True)
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta(
            "resume_selected_existing_unverified",
            reason=f"label={(selected_label or '')[:80]}",
        )
        return

    if resolved_path:
        print_lg("      [ResumeAwareness] no radios — attempting file upload fallback")
        _debug_resume_upload_ui(page)
        fi = page.query_selector("input[type='file']")
        if fi:
            fi.set_input_files(resolved_path)
            print_lg(f"    [ResumeUpload] Uploaded resume (no-radio fallback): {resolved_path}")
            _use_new_resume = False

            filename = os.path.basename(resolved_path)
            try:
                page.wait_for_selector(f"text={filename}", timeout=10000)
                print_lg(f"    [ResumeUpload] Verified resume upload: {filename}")
            except Exception:
                time.sleep(5)
        else:
            print_lg("      [ResumeAwareness] ✗ no file input for fallback upload")
            from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
            log_job_status_event_from_meta("resume_upload_blocked", reason="no_radio_no_file_input")



# ── Visibility ────────────────────────────────────────────────────────────────

def _handle_visibility(page) -> None:
    print_lg("    [SmartApply] Setting visibility…")
    radios = page.query_selector_all("input[type='radio']")
    for r in radios:
        rid = r.get_attribute("id") or ""
        lbl = page.query_selector(f'label[for="{rid}"]') if rid else None
        ltext = lbl.inner_text().lower() if lbl else ""
        if "find you" in ltext or "recommended" in ltext:
            if not r.is_checked():
                r.click(force=True)
            return
    if radios and not radios[0].is_checked():
        radios[0].click(force=True)


# ── Experience ────────────────────────────────────────────────────────────────

def _handle_experience(page) -> None:
    print_lg("    [SmartApply] Filling experience…")
    try:
        page_text = (page.inner_text("body") or "").lower()
    except Exception:
        page_text = ""
    is_relevant_experience_page = any(k in page_text for k in (
        "enter a job that shows relevant experience",
        "we share one job title with the employer",
        "introduce you as a candidate",
    ))
    if not is_relevant_experience_page:
        print_lg("      [Experience] Not the relevant-experience prompt; leaving fields unchanged.")
        return

    profile_type = os.environ.get("JOB_PROFILE", "IT").upper()
    if profile_type == "GENERAL":
        job_title = "Porter"
        company_name = "Vancouver Coastal Health"
    else:
        job_title = RELEVANT_EXPERIENCE_JOB_TITLE
        company_name = RELEVANT_EXPERIENCE_COMPANY

    title_filled = False
    company_filled = False

    for sel in ["input[id*='jobTitle']", "input[name*='jobTitle']",
                "input[id*='job-title' i]", "input[name*='job-title' i]",
                "input[aria-label*='Job title' i]",
                "input[placeholder*='Job title' i]", "input[placeholder*='title' i]"]:
        el = page.query_selector(sel)
        if el:
            if (el.get_attribute('value') or "").strip() != job_title:
                _type_into(page, el, job_title)
            title_filled = (el.get_attribute('value') or "").strip() == job_title
            break

    for sel in ["input[id*='company']", "input[name*='company']",
                "input[aria-label*='Company' i]", "input[placeholder*='Company' i]"]:
        el = page.query_selector(sel)
        if el:
            if (el.get_attribute('value') or "").strip() != company_name:
                _type_into(page, el, company_name)
            company_filled = (el.get_attribute('value') or "").strip() == company_name
            break

    if title_filled and company_filled:
        print_lg(f"      [Experience] Filled values for {profile_type} profile: {job_title} at {company_name}")
    else:
        missing = []
        if not title_filled:
            missing.append("job title")
        if not company_filled:
            missing.append("company")
        print_lg(
            "      [Experience] Could not find/fill "
            f"{', '.join(missing)}; skipping AI fallback for relevant experience."
        )
        log_training_event(
            "question_skipped",
            job=_current_job_meta,
            control_type="experience",
            question="Enter a job that shows relevant experience",
            answer="",
            decision_source="hardcoded_relevant_experience_not_fillable",
            page=page_dom_snapshot(page, limit=25),
        )


# ── Resume selection ──────────────────────────────────────────────────────────

# ── Resume selection Continue button selectors (confirmed from HTML dump) ─────
# data-testid='continue-button'  /  'hp-continue-button-{n}'
_RESUME_CONTINUE_SELECTORS = [
    "button[data-testid='continue-button']",
    "button[data-testid='hp-continue-button-0']",
    "button[data-testid='hp-continue-button-1']",
    "button[data-testid='hp-continue-button-2']",
    "button[data-testid*='continue-button']",
]

_RESUME_CONTINUE_XPATHS = [
    "//button[@data-testid='continue-button']",
    "//button[contains(@data-testid,'hp-continue-button')]",
    "//div[@data-testid='resume-selection-footer']//button",
    "//button[contains(normalize-space(),'Continue')]",
]


def _click_resume_continue(page) -> bool:
    """Click the blue Continue button on the resume selection page."""
    for sel in _RESUME_CONTINUE_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                btn_txt = el.inner_text().strip()[:40]
                print_lg(f"      → Clicking Continue: '{btn_txt}'")
                try:
                    el.click(timeout=5000)
                except Exception as e:
                    print_lg(f"      → Continue click raised {type(e).__name__} (may be OK)")
                return True
        except Exception:
            continue
    for xp in _RESUME_CONTINUE_XPATHS:
        try:
            el = page.query_selector(f"xpath={xp}")
            if el and el.is_visible():
                print_lg(f"      → Clicking Continue (xpath): '{el.inner_text().strip()[:40]}'")
                try:
                    el.click(timeout=5000)
                except Exception as e:
                    print_lg(f"      → Continue click raised {type(e).__name__}")
                return True
        except Exception:
            continue
    return False


def _read_selected_resume_label(page) -> str:
    """Return the visible label for the currently checked resume radio/card."""
    try:
        for r in page.query_selector_all("input[type='radio']"):
            try:
                if not r.is_checked():
                    continue
                rid = r.get_attribute("id") or ""
                lbl = page.query_selector(f'label[for="{rid}"]') if rid else None
                if lbl:
                    return (lbl.inner_text() or "").strip()
                value = (r.get_attribute("value") or "").strip()
                if value and value.lower() not in {"file", "build", "create"}:
                    return value
            except Exception:
                continue
        for sel in (
            "[data-testid='resume-selection-file-resume-radio-card']",
            "label[data-testid='resume-selection-file-resume-radio-card-label']",
        ):
            el = page.query_selector(sel)
            if el and el.is_visible():
                txt = (el.inner_text() or "").strip()
                if txt:
                    return txt
    except Exception:
        pass
    return ""


def _resume_label_matches_expected(label: str, resolved_path: str) -> bool:
    """True when the Indeed-selected resume is clearly our intended file."""
    import os
    from pathlib import Path

    label_l = (label or "").lower().replace(" ", "_").replace("-", "_")
    if not label_l:
        return False
    expected_names = []
    if resolved_path:
        expected_names.append(Path(resolved_path).name.lower())
        expected_names.append(Path(resolved_path).stem.lower())
    tailored = os.getenv("INDEED_TAILORED_RESUME_PATH") or ""
    if tailored:
        expected_names.append(Path(tailored).name.lower())
        expected_names.append(Path(tailored).stem.lower())
    # Only include default resumes if NOT tailored
    if not tailored and not (resolved_path and "tailored" in Path(resolved_path).name.lower()):
        expected_names.extend([
            "resume_it.pdf", "resume_it",
            "resume_general.pdf", "resume_general",
            "resume.pdf", "sample_resume.pdf",
        ])
    for name in expected_names:
        token = name.replace(".pdf", "").replace(" ", "_").replace("-", "_")
        if token and token[:24] in label_l:
            return True
    return False


def _active_resume_selection_page(page):
    """Prefer the populated resume-picker tab over an empty stale SmartApply tab.

    Indeed occasionally leaves a blank ``resume-selection`` target behind while
    opening the actual picker in a sibling tab.  Driving the blank tab produces
    no radio, file input, or Continue button and eventually trips the
    same-URL guard.  There is only one Indeed apply worker per profile, so the
    populated sibling is the safe form target.
    """
    best_page, best_score = page, -1
    try:
        candidates = list(page.context.pages)
    except Exception:
        candidates = [page]
    for candidate in candidates:
        try:
            if candidate.is_closed():
                continue
            url = (candidate.url or "").lower()
            if "smartapply.indeed.com" not in url or "resume-selection" not in url:
                continue
            score = 0
            for selector in (
                "input[name='resume-selection']",
                "input[type='radio']",
                "input[type='file']",
                "button[data-testid*='continue']",
            ):
                try:
                    if candidate.query_selector(selector):
                        score += 1
                except Exception:
                    continue
            try:
                body = candidate.query_selector("body")
                text = (body.inner_text() or "").lower() if body else ""
                if "add a resume" in text or "select a resume" in text:
                    score += 2
            except Exception:
                pass
            if score > best_score:
                best_page, best_score = candidate, score
        except Exception:
            continue
    if best_page is not page and best_score > 0:
        print_lg(
            f"      [ResumeAwareness] switched from blank resume tab to populated picker "
            f"(signals={best_score})"
        )
    return best_page


def _handle_resume_selection(page):
    """
    Handle the 'Upload or create a resume' step.

    From HTML dump analysis:
      - Radio:    input[name='resume-selection'][value='file']
                  label = existing resume filename (e.g. 'resume.pdf')
      - Card:     div[data-testid='resume-selection-file-resume-radio-card']
      - Continue: button[data-testid='continue-button']
                  button[data-testid='hp-continue-button-0']  (multiples = responsive layout)

    Awareness + debug only for now (Indeed upload UI is often broken after
    resume delete). Log the visible resume label, intended path, and upload
    UI state. Prefer upload when tailored/mismatch, but if Indeed blocks the
    file picker we fall back with an explicit unverified warning — do not
    silently assume the pre-selected resume is correct.
    """
    import os
    global _use_new_resume
    page = _active_resume_selection_page(page)
    print_lg("    [SmartApply] Selecting resume…")
    time.sleep(_T_RESUME)

    resolved_path = _resolve_resume_path()
    is_tailored = bool(os.getenv("INDEED_TAILORED_RESUME_PATH"))
    selected_label = _read_selected_resume_label(page)
    label_ok = _resume_label_matches_expected(selected_label, resolved_path)
    print_lg(
        f"      [ResumeAwareness] shown='{(selected_label or '')[:120]}' "
        f"intended={resolved_path or 'none'} tailored={is_tailored} "
        f"matches_intended={label_ok}"
    )
    from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
    log_job_status_event_from_meta(
        "resume_selection_seen",
        reason=(
            f"shown={(selected_label or '')[:80]}|"
            f"intended={os.path.basename(resolved_path) if resolved_path else 'none'}|"
            f"match={label_ok}|tailored={is_tailored}"
        ),
    )
    _debug_resume_upload_ui(page)

    # Prefer uploading our intended resume whenever the pre-selected one is wrong
    # or unknown. If Indeed's uploader is broken, we log resume_upload_blocked.
    should_upload = bool(resolved_path) and (is_tailored or _use_new_resume or not label_ok)
    if should_upload and resolved_path:
        if label_ok and is_tailored and not _use_new_resume:
            print_lg("      [ResumeAwareness] pre-selected already matches intended; keeping it")
        else:
            reason = (
                "tailored resume required" if is_tailored
                else ("pre-selected resume does not match IT/default" if selected_label and not label_ok
                      else "forcing upload of intended resume")
            )
            print_lg(f"      [ResumeAwareness] upload attempt ({reason}): {resolved_path}")
            if _upload_tailored_resume(page, resolved_path):
                _use_new_resume = False
                time.sleep(1.0)
                new_label = _read_selected_resume_label(page)
                if new_label:
                    print_lg(f"      [ResumeAwareness] after upload Indeed shows: '{new_label[:120]}'")
                log_job_status_event_from_meta(
                    "resume_uploaded_intended",
                    reason=f"after={(new_label or '')[:80]}",
                )
                if _click_resume_continue(page):
                    print_lg("    [SmartApply] ✓ Resume selection Continue clicked (after upload)")
                    time.sleep(_T_RESUME)
                return page
            print_lg(
                "      [ResumeAwareness] upload failed/blocked (Indeed-side likely); "
                "continuing with existing selection — flagged unverified"
            )

    if selected_label and not label_ok:
        print_lg(
            f"    [ResumeAwareness] ⚠ NON-MATCHING resume '{selected_label[:80]}' "
            f"— intended={resolved_path or 'none'}"
        )
        log_job_status_event_from_meta(
            "resume_mismatch_warning",
            reason=f"shown={selected_label[:80]}|intended={resolved_path or 'none'}",
        )

    # ── Step 1: Select existing resume radio ─────────────────────────────────
    resume_selected = False

    # By name attribute (most reliable from dump: name='resume-selection')
    for sel in [
        "input[name='resume-selection'][value='file']",
        "input[name='resume-selection']",
        "input[type='radio'][value='file']",
    ]:
        try:
            el = page.query_selector(sel)
            if el:
                if not el.is_checked():
                    el.click(force=True)
                    print_lg("      → Selected resume radio (by name attr)")
                else:
                    print_lg("      → Resume radio already selected")
                resume_selected = True
                log_job_status_event_from_meta(
                    "resume_selected_existing_match" if label_ok else "resume_selected_existing_unverified",
                    reason=f"label={(selected_label or '')[:80]}|match={label_ok}",
                )
                break
        except Exception:
            continue

    # Fallback: iterate all radios, skip 'build'/'create' options
    if not resume_selected:
        for r in page.query_selector_all("input[type='radio']"):
            try:
                rid   = r.get_attribute("id") or ""
                lbl   = page.query_selector(f'label[for="{rid}"]') if rid else None
                ltext = (lbl.inner_text().lower() if lbl else "")
                value = (r.get_attribute("value") or "").lower()
                if "build" in ltext or "create" in ltext or "build" in value or "create" in value:
                    continue
                if not r.is_checked():
                    r.click(force=True)
                shown = (lbl.inner_text() if lbl else value)[:80]
                print_lg(f"      [ResumeAwareness] selected radio (fallback): '{shown}'")
                resume_selected = True
                log_job_status_event_from_meta(
                    "resume_selected_existing_unverified",
                    reason=f"label={shown}",
                )
                break
            except Exception:
                continue

    # Fallback: click the resume card label directly
    if not resume_selected:
        for sel in [
            "label[data-testid='resume-selection-file-resume-radio-card-label']",
            "[data-testid='resume-selection-file-resume-radio-card']",
        ]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(force=True)
                    shown = (el.inner_text() or "")[:80]
                    print_lg(f"      [ResumeAwareness] clicked resume card: '{shown}'")
                    resume_selected = True
                    log_job_status_event_from_meta(
                        "resume_selected_existing_unverified",
                        reason=f"label={shown}",
                    )
                    break
            except Exception:
                continue

    time.sleep(_T_ACTION)

    # ── Step 2: Click the blue Continue button ───────────────────────────────
    if _click_resume_continue(page):
        print_lg("    [SmartApply] ✓ Resume selection Continue clicked")
        time.sleep(_T_RESUME)   # wait for spinner + page transition
    else:
        resolved_path = _resolve_resume_path()
        if resolved_path:
            print_lg("      [ResumeAwareness] Continue missing — upload fallback + debug")
            _debug_resume_upload_ui(page)
            fi = page.query_selector("input[type='file']")
            if fi:
                fi.set_input_files(resolved_path)
                print_lg(f"      [ResumeUpload] continue-fallback upload: {resolved_path}")
                _use_new_resume = False
                time.sleep(_T_RESUME)
                _click_resume_continue(page)
            else:
                print_lg("      [ResumeAwareness] ✗ Continue missing and no file input")
                log_job_status_event_from_meta(
                    "resume_upload_blocked", reason="continue_missing_no_file_input"
                )
        else:
            print_lg("    [SmartApply] ⚠ Could not click Continue on resume selection — check page")
    return page


# ── Qualification questions ───────────────────────────────────────────────────
