"""Browser adapter for the deterministic Indeed work-history model."""
from __future__ import annotations

from jobbots.core.shared_modules.indeed_history import Employer, WORK_HISTORY, valid_work_history_date


def fill_work_history(page) -> bool:
    """Fill Indeed's employer section once, stopping after the second employer."""
    if not _has_employer_section(page):
        return False
    for index, employer in enumerate(WORK_HISTORY):
        _fill_employer(page, index, employer)
        if index == 0:
            _click_text(page, "Add additional employer")
    _click_text(page, "No")
    return True


def _has_employer_section(page) -> bool:
    try:
        text = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return "employer" in text and ("add additional employer" in text or "current employer" in text)


def _fill_employer(page, index: int, employer: Employer) -> None:
    selectors = {
        "company": ["input[name*='company' i]", "input[id*='company' i]"],
        "title": ["input[name*='jobTitle' i]", "input[id*='jobTitle' i]", "input[name*='title' i]"],
        "start_date": ["input[name*='start' i]", "input[id*='start' i]"],
        "end_date": ["input[name*='end' i]", "input[id*='end' i]"],
    }
    values = {"company": employer.company, "title": employer.title,
              "start_date": employer.start_date, "end_date": employer.end_date}
    for field, value in values.items():
        if field in {"start_date", "end_date"} and not valid_work_history_date(value):
            value = ""
        element = _field(page, selectors[field], index)
        if element is not None:
            _type_into(page, element, value)
    _click_text(page, "Yes" if employer.current else "No", index=index)


def _field(page, selectors: list[str], index: int):
    for selector in selectors:
        elements = page.query_selector_all(selector)
        if index < len(elements):
            return elements[index]
    return None


def _click_text(page, text: str, index: int | None = None) -> None:
    locator = page.get_by_text(text, exact=True)
    if index is not None and locator.count() > index:
        locator.nth(index).click(force=True)
    elif locator.count():
        locator.last.click(force=True)


def _type_into(page, element, value: str) -> None:
    try:
        element.fill(value)
    except Exception:
        element.click(force=True)
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type(value)
