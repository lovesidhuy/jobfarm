from __future__ import annotations

from ._bootstrap import *  # noqa: F403

def _find_save_job_button(page):
    # Give React a moment to render the save button after domcontentloaded
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass

    selectors = [
        # Explicit detail pane container (highest priority to avoid matching wrong list cards)
        "#saveJobButtonContainer button",
        "#saveJobButtonContainer [role='button']",
        "[id*='saveJobButton'] button",
        # Explicit data-testid variants (Indeed uses these in newer UI)
        "button[data-testid='saveJobButton']",
        "button[data-testid='saveJob']",
        "button[data-testid='save-job-button']",
        "[data-testid*='saveJob']",
        "[data-testid*='save-job']",
        # aria-label variants
        "button[aria-label*='Save']",
        "button[aria-label*='save']",
        "button[aria-label^='Save this']",
        "button[aria-label^='Save job']",
        # id / class variants
        "button[id*='save']",
        "button[class*='SaveJob']",
        "button[class*='saveJob']",
        "button[class*='save-job']",
        # text variants
        "button:has-text('Save job')",
        "button:has-text('Save Job')",
        "button:has-text('Save')",
        "button:has-text('Saved')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc and loc.count() and loc.is_visible():
                return loc.element_handle()
        except Exception:
            continue

    # Broad fallback: scan all interactive elements
    try:
        for el in page.query_selector_all("button, a, [role='button']"):
            text = " ".join((el.inner_text() or "").split()).lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            testid = (el.get_attribute("data-testid") or "").lower()
            if "save" in text or "save" in aria or "save" in testid:
                if el.is_visible():
                    return el
    except Exception:
        pass
    return None


def _find_result_card_for_job(page, job_id: str, title: str):
    for card in _find_job_cards(page):
        try:
            info = _extract_card_info(card, page)
            if job_id and info[0] == job_id:
                return card
            if title and title.lower() in (info[1] or "").lower():
                return card
        except Exception:
            continue
    return None


def _job_already_saved_on_indeed(page) -> bool:
    try:
        btn = _find_save_job_button(page)
        if not btn:
            return False
        text = " ".join((btn.inner_text() or "").split()).lower()
        aria = (btn.get_attribute("aria-label") or "").lower()
        pressed = (btn.get_attribute("aria-pressed") or "").lower()
        return "saved" in text or "saved" in aria or pressed == "true"
    except Exception:
        return False


def _save_job_on_indeed(page, job_id: str, title: str) -> bool:
    try:
        if _job_already_saved_on_indeed(page):
            return True
        btn = _find_save_job_button(page)
        if not btn:
            return False
        btn.click(force=True)
        time.sleep(_T_ACTION)
        return _job_already_saved_on_indeed(page) or True
    except Exception as e:
        print_lg(f"  [Indeed] Save click failed for '{title}': {e}")
        return False


def _save_result_card_on_indeed(page, job_id: str, title: str) -> bool:
    if _page_has_job_detail(page):
        saved = _save_job_on_indeed(page, job_id, title)
        if saved:
            return True

    card = _find_result_card_for_job(page, job_id, title)
    if card:
        try:
            btn = card.query_selector(
                "button[aria-label*='Save'], button[aria-label*='save'], "
                "button[data-testid*='save'], button:has-text('Save')"
            )
            if btn and btn.is_visible():
                btn.click(force=True)
                time.sleep(_T_ACTION)
                return True
        except Exception:
            pass
    return _save_job_on_indeed(page, job_id, title)


def _return_to_search_results_after_external_save(page, search_url: str) -> None:
    try:
        current = page.url or ""
    except Exception:
        current = ""
    if current.startswith(search_url):
        return
    _goto_page(page, search_url, timeout=15000)


# ─────────────────────────────────────────────────────────────────────────────
# Apply button detection  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _find_apply_button(page):
    """Returns (element, is_easy_apply: bool)."""
    try:
        page_low = page.content().lower()
    except Exception:
        page_low = ""

    def _classify_apply_el(el):
        href = (el.get_attribute('href') or '').lower()
        form_url = (el.get_attribute('data-indeed-apply-formurl') or '').lower()
        cls = (el.get_attribute('class') or '').lower()
        testid = (el.get_attribute('data-testid') or '').lower()
        aria = (el.get_attribute('aria-label') or '').lower()
        text = ((el.inner_text() or '') if hasattr(el, "inner_text") else "").lower()
        is_easy = (
            SMARTAPPLY_DOMAIN in href or
            'indeed-apply' in cls or
            'indeedapply' in cls or
            bool(form_url) or
            'indeedapplybutton' in testid or
            'apply now' in text or
            'continue applying' in text or
            'apply with indeed' in text or
            ('apply' in text and 'indeed' in text)
        )
        is_external = 'apply on company' in text or 'apply on company' in aria
        return is_easy, is_external, href

    def _visible_match(xp: str, easy_hint: bool | None = None):
        for el in page.query_selector_all(f"xpath={xp}"):
            try:
                if not el.is_visible():
                    continue
                is_easy, is_external, href = _classify_apply_el(el)
                if any(skip in href for skip in ('javascript:', 'sign', 'login', 'register')):
                    continue
                if easy_hint is True:
                    return el, True
                if easy_hint is False:
                    return el, False
                if is_external:
                    return el, False
                return el, is_easy
            except Exception:
                continue
        return None

    # Give Indeed's right-pane CTA a moment to render after navigation.
    for _ in range(4):
        for xp, easy_hint in [
            ("//a[contains(@href,'smartapply.indeed.com')]", True),
            ("//button[@data-indeed-apply-formurl]", True),
            ("//*[@data-indeed-apply-jobid]", True),
            ("//button[contains(@class,'indeed-apply') or contains(@class,'IndeedApply')]", True),
            ("//a[contains(@class,'indeed-apply') or contains(@class,'IndeedApply')]", True),
            ("//*[contains(@class,'indeed-apply-button')]", True),
            ("//*[contains(@class,'indeed-apply-widget')]//button", True),
            ("//*[contains(@class,'indeed-apply-widget')]//a", True),
            ("//button[@data-testid='IndeedApplyButton']", True),
            ("//button[@data-testid='applyButton']", True),
            ("//*[@data-testid='indeed-apply']", True),
            ("//button[contains(@data-testid,'apply')]", None),
            ("//a[contains(@data-testid,'apply')]", None),
            ("//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'easily apply')]", True),
            ("//span[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'easily apply')]", True),
            ("//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply now')]", None),
            ("//a[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply now')]", None),
            ("//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'easy apply')]", True),
            ("//button[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply now')]", None),
            ("//a[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply now')]", None),
            ("//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply on company')]", False),
            ("//a[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply on company')]", False),
            ("//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply on')]", False),
        ]:
            found = _visible_match(xp, easy_hint)
            if found:
                return found
        time.sleep(0.35)

    candidates = page.query_selector_all(
        "xpath=//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply')]"
        " | //a[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply')]"
    )
    for el in candidates:
        try:
            if not el.is_visible():
                continue
            is_easy, is_external, href = _classify_apply_el(el)
            if any(skip in href for skip in ('javascript:', 'sign', 'login', 'register')):
                continue
            return el, False if is_external else is_easy
        except Exception:
            continue

    return None, False


def _page_has_job_detail(page) -> bool:
    """Return True when the page appears to show a real job-detail view."""
    try:
        if (_get_job_description(page) or "").strip():
            return True
    except Exception:
        pass

    for sel in [
        "[data-testid='jobsearch-JobInfoHeader-title']",
        "h1.jobsearch-JobInfoHeader-title",
        "h1[data-testid='jobsearch-jobTitle']",
        "div.jobsearch-JobComponent",
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        except Exception:
            continue
    return False


def _open_job_detail_from_results(page, job_id: str, title: str = "") -> bool:
    """
    If we're still on the search-results page, click the matching job card/title
    so Indeed renders the actual detail/apply view before CTA detection runs.
    """
    selectors = []
    if job_id and job_id != "Unknown":
        selectors.extend([
            f"a[data-jk='{job_id}']",
            f"[data-jk='{job_id}'] h2 a",
            f"[data-jk='{job_id}'] a.jcs-JobTitle",
            f"[data-jk='{job_id}']",
        ])

    normalized_title = " ".join((title or "").split()).strip()

    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if not el or not el.is_visible():
                continue
            try:
                el.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            el.click(force=True, timeout=4000)
            time.sleep(_T_ACTION)
            if _page_has_job_detail(page):
                return True
        except Exception:
            continue

    if normalized_title:
        try:
            candidates = page.query_selector_all("h2 a, a.jcs-JobTitle, [data-jk] h2 a")
        except Exception:
            candidates = []
        for el in candidates:
            try:
                if not el.is_visible():
                    continue
                text = " ".join((el.inner_text() or "").split()).strip()
                if text.lower() != normalized_title.lower():
                    continue
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                el.click(force=True, timeout=4000)
                time.sleep(_T_ACTION)
                if _page_has_job_detail(page):
                    return True
            except Exception:
                continue

    return False


def _debug_visible_apply_elements(page) -> list[str]:
    """Return short debug lines for visible apply-like elements on the page."""
    lines = []
    try:
        candidates = page.query_selector_all(
            "xpath=//button | //a | //*[@role='button']"
        )
    except Exception as e:
        return [f"debug-scan-error={e}"]

    for el in candidates:
        try:
            if not el.is_visible():
                continue
            text = " ".join((el.inner_text() or "").split())
            aria = (el.get_attribute("aria-label") or "").strip()
            testid = (el.get_attribute("data-testid") or "").strip()
            href = (el.get_attribute("href") or "").strip()
            cls = (el.get_attribute("class") or "").strip()
            low = f"{text} {aria} {testid} {href} {cls}".lower()
            if (
                "apply" not in low and
                "indeed-apply" not in low and
                "indeedapply" not in low and
                "smartapply" not in low
            ):
                continue
            lines.append(
                f"text='{text[:80]}' aria='{aria[:60]}' testid='{testid[:40]}' href='{href[:90]}' class='{cls[:60]}'"
            )
            if len(lines) >= 12:
                break
        except Exception:
            continue
    return lines


def _accessible_apply_button(page):
    """Fallback lookup using accessible roles/names for Indeed's right-pane CTA."""
    for role_name, is_easy in [
        ("Apply now", True),
        ("Easily apply", True),
        ("Apply on company site", False),
        ("Apply on company", False),
    ]:
        try:
            locator = page.get_by_role("button", name=role_name, exact=False).first
            if locator and locator.is_visible():
                return locator.element_handle(), is_easy
        except Exception:
            pass
        try:
            locator = page.get_by_role("link", name=role_name, exact=False).first
            if locator and locator.is_visible():
                return locator.element_handle(), is_easy
        except Exception:
            pass
    return None, False


def _get_smartapply_link_from_page(page) -> str:
    for lnk in page.query_selector_all('a'):
        try:
            href = lnk.get_attribute('href') or ''
            if SMARTAPPLY_DOMAIN in href.lower():
                return href
        except Exception:
            continue
    for btn in page.query_selector_all('[data-indeed-apply-formurl]'):
        try:
            url = btn.get_attribute('data-indeed-apply-formurl') or ''
            if url:
                return url
        except Exception:
            continue
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# SmartApply form helpers  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _type_into(page, element, text: str, clear: bool = True) -> None:
    try:
        if clear:
            element.fill(str(text))
        else:
            element.type(str(text))
    except Exception:
        try:
            element.evaluate(
                """
                (el, value) => {
                    el.value = value;
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                }
                """,
                str(text),
            )
        except Exception:
            pass


def _set_input_value_direct(page, element, value: str) -> bool:
    value = str(value)
    try:
        element.fill(value)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            element.evaluate("el => el.blur()")
        except Exception:
            pass
        if (element.get_attribute("value") or "").strip() == value:
            return True
    except Exception:
        pass

    try:
        element.evaluate(
            """
            (el, value) => {
                const proto = el instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
                if (setter) setter.call(el, value);
                else el.value = value;
                el.dispatchEvent(new InputEvent("input", {
                    bubbles: true,
                    inputType: "insertText",
                    data: value,
                }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
                el.blur();
            }
            """,
            value,
        )
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _click_continue_force(page, timeout: int = 3) -> bool:
    xpaths = [
        "//button[@type='submit']",
        "//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review your application')]",
        "//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review my application')]",
        "//a[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review your application')]",
        "//a[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review my application')]",
        "//button[contains(normalize-space(),'Continue')]",
        "//button[contains(normalize-space(),'Next')]",
        "//button[contains(normalize-space(),'Submit')]",
        "//button[contains(normalize-space(),'Apply')]",
    ]
    for xp in xpaths:
        try:
            el = page.wait_for_selector(f"xpath={xp}", timeout=timeout * 1000, state='visible')
            if el:
                el.click()
                return True
        except Exception:
            continue

    for xp in xpaths[:9]:
        try:
            el = page.query_selector(f"xpath={xp}")
            if el:
                el.evaluate("el => { el.removeAttribute('disabled'); el.click(); }")
                return True
        except Exception:
            continue
    return False


def _get_question_context(page, element) -> str:
    try:
        eid = element.get_attribute("id") or ""
        if eid:
            lbl = page.query_selector(f'label[for="{eid}"]')
            if lbl:
                return lbl.inner_text().lower()

        labelledby = element.get_attribute("aria-labelledby") or ""
        if labelledby:
            lbl = page.query_selector(f"#{labelledby}")
            if lbl:
                return lbl.inner_text().lower()

        al = element.get_attribute("aria-label") or ""
        if al:
            return al.lower()

        # Ancestor fallback: walk up at most 4 levels, but prefer the SMALLEST
        # ancestor with meaningful text. A huge innerText (e.g. > 200 chars)
        # usually means the wrapper contains many unrelated fields/labels and
        # would cause cross-field hint contamination (e.g. every textarea on a
        # form section that mentions "LinkedIn" once would get the LinkedIn
        # URL typed in). We only return such large text as a last resort.
        best_small = ""
        best_large = ""
        for depth in range(1, 5):
            js = ("el => { let e = el; "
                  + "if (!e.parentElement) return ''; e = e.parentElement; " * depth
                  + "return e ? e.innerText : ''; }")
            text = (element.evaluate(js) or "").strip()
            if not text or len(text) <= 3:
                continue
            if len(text) <= 200:
                # Small, scoped ancestor — return immediately.
                return text[:300].lower()
            if not best_small and not best_large:
                best_large = text
        if best_large:
            # No scoped ancestor found — return a trimmed slice rather than the
            # whole section text. Trim to first sentence/line so unrelated
            # labels in the wider section don't poison the hint.
            head = re.split(r"[\n\r\.\?\!]", best_large, maxsplit=1)[0]
            return head.strip()[:300].lower()
    except Exception:
        pass
    return ""


# ── Contact info ──────────────────────────────────────────────────────────────
