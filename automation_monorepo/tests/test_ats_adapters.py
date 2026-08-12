"""Unit + integration tests for ATS adapters (mocked Playwright page, no browser)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ci_env(monkeypatch):
    monkeypatch.setenv("BOT_NAME", "ci-smoke")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "")
    monkeypatch.setenv("DD_METRICS_ENABLED", "0")
    monkeypatch.setenv("FORM_ANSWERS_DISABLE_AI", "1")
    monkeypatch.delenv("SENTRY_DSN", raising=False)


# ── shared mock page ─────────────────────────────────────────────────

class FakeElement:
    def __init__(self, *, visible=True, tag="input", typ="text", value=""):
        self._visible = visible
        self._tag = tag
        self._type = typ
        self._value = value
        self.clicked = False
        self.filled_with: list[str] = []

    def is_visible(self):
        return self._visible

    def get_attribute(self, name):
        return {"type": self._type}.get(name)

    def evaluate(self, script, *args):
        if "tagName" in script:
            return self._tag.upper()
        return ""

    def input_value(self):
        return self._value

    def fill(self, v):
        self.filled_with.append(v)
        self._value = v

    def click(self, **kw):
        self.clicked = True

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []


class FakePage:
    """Minimal Playwright Page stand-in."""

    def __init__(self, *, url="", body_text="", html=""):
        self.url = url
        self._body_text = body_text
        self._html = html or f"<html><body>{body_text}</body></html>"
        self.goto_calls: list[str] = []
        self._selectors: dict[str, FakeElement | None] = {}

    # navigation
    def goto(self, url, **kw):
        self.goto_calls.append(url)
        self.url = url

    def wait_for_load_state(self, *a, **kw):
        pass

    # DOM access
    def content(self):
        return self._html

    def evaluate(self, script, *args):
        if "innerText" in script:
            return self._body_text
        return ""

    def query_selector(self, sel):
        return self._selectors.get(sel)

    def query_selector_all(self, sel):
        return []

    @property
    def frames(self):
        return []

    @property
    def main_frame(self):
        return None

    def set(self, sel, el):
        self._selectors[sel] = el
        return self


# ── adapter basics ───────────────────────────────────────────────────

def test_adapters_have_platform_names():
    from core.ats.adapters import (
        GreenhouseAdapter, LeverAdapter, AshbyAdapter, BambooHRAdapter,
    )
    assert GreenhouseAdapter().platform_name == "greenhouse"
    assert LeverAdapter().platform_name == "lever"
    assert AshbyAdapter().platform_name == "ashby"
    assert BambooHRAdapter().platform_name == "bamboohr"


def test_adapters_implement_interface():
    from core.ats.base import ATSAdapter
    from core.ats.adapters import (
        GreenhouseAdapter, LeverAdapter, AshbyAdapter, BambooHRAdapter,
    )
    for cls in (GreenhouseAdapter, LeverAdapter, AshbyAdapter, BambooHRAdapter):
        inst = cls()
        assert isinstance(inst, ATSAdapter)
        for method in ("initialize", "authenticate", "upload_documents",
                       "fill_application", "answer_questions", "solve_captcha",
                       "submit", "verify_submission"):
            assert callable(getattr(inst, method)), f"{cls.__name__}.{method}"


# ── verify_submission with canned page text ──────────────────────────

@pytest.mark.parametrize("adapter_cls,url", [
    ("GreenhouseAdapter", "https://boards.greenhouse.io/acme/jobs/1"),
    ("LeverAdapter", "https://jobs.lever.co/acme/abc/apply"),
    ("AshbyAdapter", "https://jobs.ashbyhq.com/acme/abc"),
    ("BambooHRAdapter", "https://acme.bamboohr.com/careers/1"),
])
def test_verify_submission_success(adapter_cls, url):
    from core.ats import adapters
    inst = getattr(adapters, adapter_cls)()
    inst.page = FakePage(url=url, body_text="Application submitted. Thanks for applying!")
    # Give the form-disappearance fallback a form so it doesn't false-positive.
    inst.page.set("form, [role='form'], [class*='application']", FakeElement())
    inst.page.set("form, [role='form']", FakeElement())
    assert inst.verify_submission() == "submitted"


def test_bamboohr_confirmation_evidence_accepts_page_and_receipt_wording():
    from core.ats.adapters import BambooHRAdapter

    page = FakePage(
        url="https://tractionrec.bamboohr.com/careers/150",
        body_text="Thank You Your application was submitted successfully",
    )
    inst = BambooHRAdapter()
    inst.page = page
    assert inst.verify_submission() == "submitted"
    assert "submitted successfully" in inst.confirmation_evidence

    receipt = FakePage(
        url="https://tractionrec.bamboohr.com/careers/150",
        body_text="Thanks Jane! We received your application.",
    )
    inst.page = receipt
    inst.confirmation_evidence = ""
    assert inst.verify_submission() == "submitted"
    assert "received your application" in inst.confirmation_evidence


def test_ashby_nestmed_success_banner_is_recognized():
    from core.ats.adapters import AshbyAdapter

    inst = AshbyAdapter()
    inst.page = FakePage(
        url="https://jobs.ashbyhq.com/nestmed/example/application",
        body_text=(
            "Success Thanks for your application to Nestmed. "
            "It has been received and we will contact you if there are next steps."
        ),
    )
    assert inst.verify_submission() == "submitted"
    assert inst.confirmation_evidence


def test_ashby_nooks_style_successfully_submitted_banner():
    """Live Ashby UX: stays on /application with green Success banner."""
    from core.ats.adapters import AshbyAdapter

    inst = AshbyAdapter()
    inst.page = FakePage(
        url="https://jobs.ashbyhq.com/nooks/role-id/application",
        body_text="Success Your application was successfully submitted!",
    )
    assert inst.verify_submission() == "submitted"
    assert "successfully submitted" in inst.confirmation_evidence.lower()


def test_greenhouse_confirmation_url_is_page_primary():
    from core.ats.adapters import GreenhouseAdapter

    inst = GreenhouseAdapter()
    # URL-only confirmation (no soft body copy that would match first).
    inst.page = FakePage(
        url="https://boards.greenhouse.io/acme/jobs/123/confirmation",
        body_text="Your application is on file with our team.",
    )
    assert inst.verify_submission() == "submitted"
    assert "confirmation" in inst.confirmation_evidence.lower()

    # Body copy alone is also enough (page-primary, no email required).
    inst2 = GreenhouseAdapter()
    inst2.page = FakePage(
        url="https://boards.greenhouse.io/acme/jobs/123",
        body_text="Thanks for applying!",
    )
    assert inst2.verify_submission() == "submitted"
    assert inst2.confirmation_evidence


def test_lever_thanks_url_is_page_primary():
    from core.ats.adapters import LeverAdapter

    inst = LeverAdapter()
    inst.page = FakePage(
        url="https://jobs.lever.co/acme/abc/thanks",
        body_text="Application submitted",
    )
    assert inst.verify_submission() == "submitted"
    assert inst.confirmation_evidence


def test_form_gone_alone_is_not_success_for_any_ats():
    """Page confirmation is required — submit-button disappearance is not."""
    from core.ats import adapters

    cases = (
        ("GreenhouseAdapter", "https://boards.greenhouse.io/acme/jobs/1"),
        ("LeverAdapter", "https://jobs.lever.co/acme/abc/apply"),
        ("AshbyAdapter", "https://jobs.ashbyhq.com/acme/abc/application"),
        ("BambooHRAdapter", "https://acme.bamboohr.com/careers/1"),
    )
    for name, url in cases:
        inst = getattr(adapters, name)()
        # Job page fluff only — no submit control, no success copy.
        inst.page = FakePage(
            url=url,
            body_text="Join our team. About the role. Benefits. Equal opportunity employer.",
        )
        assert inst.verify_submission() is None, f"{name} must not accept form-gone alone"


def test_soft_marketing_copy_is_not_page_confirmation():
    from core.ats.adapters import AshbyAdapter, GreenhouseAdapter

    soft = "Thanks for your interest in this role. We look forward to hearing from you."
    for cls, url in (
        (AshbyAdapter, "https://jobs.ashbyhq.com/acme/abc/application"),
        (GreenhouseAdapter, "https://boards.greenhouse.io/acme/jobs/1"),
    ):
        inst = cls()
        inst.page = FakePage(url=url, body_text=soft)
        assert inst.verify_submission() is None


def test_page_confirmation_policy_module_exports():
    from core.ats.confirmation import (
        SUCCESS_RE,
        classify_page_confirmation,
        url_looks_like_confirmation,
    )

    assert SUCCESS_RE.search("Your application was submitted successfully")
    assert SUCCESS_RE.search("Your application was successfully submitted")
    assert url_looks_like_confirmation(
        "https://boards.greenhouse.io/x/jobs/1/confirmation"
    )
    status, evidence = classify_page_confirmation(
        "https://acme.bamboohr.com/careers/1",
        "Thank You Your application was submitted successfully",
        platform="bamboohr",
    )
    assert status == "submitted"
    assert evidence


@pytest.mark.parametrize("adapter_cls,url", [
    ("GreenhouseAdapter", "https://boards.greenhouse.io/acme/jobs/1"),
    ("LeverAdapter", "https://jobs.lever.co/acme/abc/apply"),
    ("AshbyAdapter", "https://jobs.ashbyhq.com/acme/abc"),
    ("BambooHRAdapter", "https://acme.bamboohr.com/careers/1"),
])
def test_verify_submission_already_applied(adapter_cls, url):
    from core.ats import adapters
    inst = getattr(adapters, adapter_cls)()
    inst.page = FakePage(url=url, body_text="You have already submitted an application for this job.")
    inst.page.set("form, [role='form'], [class*='application']", FakeElement())
    inst.page.set("form, [role='form']", FakeElement())
    assert inst.verify_submission() == "already_applied"


@pytest.mark.parametrize("adapter_cls,url", [
    ("AshbyAdapter", "https://jobs.ashbyhq.com/acme/abc"),
    ("BambooHRAdapter", "https://acme.bamboohr.com/careers/1"),
])
def test_verify_submission_verification_required(adapter_cls, url):
    from core.ats import adapters
    inst = getattr(adapter_cls and adapters, adapter_cls)()
    inst.page = FakePage(url=url, body_text="A verification code was sent to your email.")
    inst.page.set("form, [role='form'], [class*='application']", FakeElement())
    inst.page.set("form, [role='form']", FakeElement())
    assert inst.verify_submission() == "verification_required"


# ── shared types ─────────────────────────────────────────────────────

def test_fill_stats_merge():
    from core.ats.types import FillStats
    a = FillStats(filled=3, skipped=1, combobox=2)
    b = FillStats(filled=2, skipped=4, radio=1)
    a.merge(b)
    assert a.filled == 5
    assert a.skipped == 5
    assert a.combobox == 2
    assert a.radio == 1


def test_application_result_as_tuple():
    from core.ats.types import ApplicationResult
    r = ApplicationResult(success=True, result_url="https://x", reason="done")
    assert r.as_tuple() == (True, "https://x", "done")


# ── engine flow with mocked page ─────────────────────────────────────

def test_engine_rejects_unsupported_url():
    from core.ats.engine import ApplicationEngine
    page = FakePage(url="https://example.com/jobs")
    engine = ApplicationEngine(page, title="SDET", company="Acme")
    result = engine.run("https://example.com/jobs")
    assert result.success is False
    assert "Unsupported ATS platform" in result.reason
    assert result.ats_platform == "unknown"


def test_greenhouse_and_lever_use_the_dedicated_application_email(monkeypatch):
    """Those ATS portals must not receive the primary Indeed/LinkedIn mailbox."""
    from core.ats.engine import _load_profile

    monkeypatch.setattr(
        "core.shared_modules.form_answers.load_profile",
        lambda: {"email": "user@example.com"},
    )
    monkeypatch.setenv("ATS_EMAIL", "user@example.com")
    monkeypatch.delenv("ATS_GREENHOUSE_LEVER_EMAIL", raising=False)

    assert _load_profile("greenhouse")["email"] == "user@example.com"
    assert _load_profile("lever")["email"] == "user@example.com"


def test_engine_run_on_page_undetectable():
    from core.ats.engine import ApplicationEngine
    page = FakePage(url="https://example.com", body_text="hello")
    engine = ApplicationEngine(page)
    result = engine.run_on_page()
    assert result.success is False
    assert "Could not detect ATS platform" in result.reason


def test_engine_success_flow_lever():
    """Full engine flow on a mocked Lever page: fill → submit → confirmed."""
    from core.ats.engine import ApplicationEngine

    page = FakePage(
        url="https://jobs.lever.co/acme/abc/apply",
        body_text="",  # starts empty; after submit we flip to success text
    )
    engine = ApplicationEngine(page, title="SDET", company="Acme")

    from core.ats.adapters.lever import LeverAdapter

    adapter = LeverAdapter()
    state = {"submitted": False}

    def fake_initialize(pg, profile, **kw):
        adapter.page = pg
        adapter.profile = profile

    def fake_verify():
        return "submitted" if state["submitted"] else None

    adapter.initialize = fake_initialize
    adapter.authenticate = lambda: True
    adapter.solve_captcha = lambda: True
    adapter.upload_documents = lambda: {"resume": True, "cover": False}
    from core.ats.types import FillStats
    adapter.fill_application = lambda: FillStats(filled=6, skipped=2)
    adapter.answer_questions = lambda: 4

    def fake_submit():
        state["submitted"] = True
        page._body_text = "Application submitted — thanks for applying!"
        return True

    adapter.submit = fake_submit
    adapter.verify_submission = fake_verify

    engine.adapter = adapter

    # Patch registry detection to return our staged adapter.
    import core.ats.engine as eng_mod
    orig_detect = eng_mod.detect_adapter_from_page
    eng_mod.detect_adapter_from_page = lambda pg: LeverAdapter
    orig_create = LeverAdapter.__call__ if hasattr(LeverAdapter, "__call__") else None
    try:
        # Force engine to use the staged instance by monkeypatching the class call.
        eng_mod.detect_adapter_from_page = lambda pg: (lambda: adapter)
        result = engine.run_on_page()
    finally:
        eng_mod.detect_adapter_from_page = orig_detect

    assert result.success is True
    assert result.ats_platform == "lever"
    assert "submitted" in result.reason.lower() or "lever" in result.reason.lower()


def test_lever_current_location_clicks_typeahead_result_and_binds_hidden_value(monkeypatch):
    """Lever requires selectedLocation; a plain text fill is cleared on blur."""
    from core.ats.adapters.lever import LeverAdapter

    class LocationInput(FakeElement):
        def __init__(self):
            super().__init__(value="")
            self.typed = ""

        def get_attribute(self, name):
            return {
                "type": "text", "name": "location", "id": "location-input",
                "class": "location-input", "data-qa": "location-input",
            }.get(name)

        def scroll_into_view_if_needed(self, **kw):
            pass

        def type(self, value, **kw):
            self.typed += value
            self._value = self.typed

    class LocationOption(FakeElement):
        def __init__(self, selected):
            super().__init__(tag="div")
            self.selected = selected

        def inner_text(self):
            return "Surrey, BC, CAN"

        def click(self, **kw):
            super().click(**kw)
            self.selected._value = '{"name":"Surrey, BC, CAN"}'

    class LocationPage(FakePage):
        def __init__(self):
            super().__init__()
            self.field = LocationInput()
            self.selected = FakeElement(typ="hidden")
            self.option = LocationOption(self.selected)

        def query_selector(self, sel):
            if "location-input" in sel or "input[name='location']" in sel:
                return self.field
            if sel == "#selected-location":
                return self.selected
            return None

        def query_selector_all(self, sel):
            return [self.option] if sel == ".dropdown-location" else []

        def wait_for_selector(self, sel, **kw):
            assert sel == ".dropdown-location"
            return self.option

    monkeypatch.setattr("core.ats.adapters.lever.time.sleep", lambda *_: None)
    page = LocationPage()
    adapter = LeverAdapter()
    adapter.page = page
    adapter.profile = {"location": "Surrey, BC, Canada"}

    assert adapter._fill_current_location() is True
    assert page.field.typed == "Surrey"
    assert page.option.clicked is True
    assert page.selected.input_value() == '{"name":"Surrey, BC, CAN"}'


@pytest.mark.parametrize("selected_selector", [
    "#selectedLocation",
    "input[name='selectedLocation']",
    "input[name='selected_location']",
])
def test_lever_current_location_accepts_alternate_hidden_binding_names(monkeypatch, selected_selector):
    """Hosted Lever variants do not consistently use #selected-location."""
    from core.ats.adapters.lever import LeverAdapter

    class LocationInput(FakeElement):
        def get_attribute(self, name):
            return {
                "type": "text", "name": "location", "id": "location-input",
                "class": "location-input", "data-qa": "location-input",
            }.get(name)

        def scroll_into_view_if_needed(self, **kw):
            pass

        def type(self, value, **kw):
            self._value = value

    class Option(FakeElement):
        def __init__(self, hidden):
            super().__init__(tag="div")
            self.hidden = hidden

        def inner_text(self):
            return "Surrey, BC, CAN"

        def click(self, **kw):
            super().click(**kw)
            self.hidden._value = "surrey-bc-can"

    class Page(FakePage):
        def __init__(self):
            super().__init__()
            self.field = LocationInput()
            self.hidden = FakeElement(typ="hidden")
            self.option = Option(self.hidden)

        def query_selector(self, selector):
            if "location-input" in selector or "input[name='location']" in selector:
                return self.field
            if selector == selected_selector:
                return self.hidden
            return None

        def query_selector_all(self, selector):
            return [self.option] if selector == ".dropdown-location" else []

        def wait_for_selector(self, selector, **kw):
            return self.option

    monkeypatch.setattr("core.ats.adapters.lever.time.sleep", lambda *_: None)
    adapter = LeverAdapter()
    adapter.page = Page()
    adapter.profile = {"location": "Surrey, BC, Canada"}

    assert adapter._fill_current_location() is True


def test_engine_does_not_count_code_exchange_and_form_clear_as_submission(monkeypatch):
    """An OTP/security-code exchange is not proof that the application posted."""
    from core.ats.engine import ApplicationEngine

    page = FakePage(url="https://boards.greenhouse.io/acme/jobs/123")
    engine = ApplicationEngine(page)

    class Adapter:
        platform_name = "greenhouse"

        def verify_submission(self):
            return "verification_required"

        def _complete_email_verification(self, profile, *, not_before):
            return True, "Verification code submitted via IMAP"

    engine.adapter = Adapter()
    monkeypatch.setattr("core.ats.engine.time.sleep", lambda *_: None)

    result = engine._wait_for_confirmation("greenhouse", page.url)

    assert result.success is False
    # OTP email is a gate only — still need page confirmation (primary signal).
    assert "page confirmation" in result.reason.lower() or "no confirmation" in result.reason.lower()


def test_engine_does_not_count_form_clear_without_confirmation_as_submission(monkeypatch):
    """A cleared form alone can be a redirect/render event, not a receipt."""
    from core.ats.engine import ApplicationEngine

    page = FakePage(url="https://jobs.lever.co/acme/job-123/apply")
    engine = ApplicationEngine(page)

    class Adapter:
        platform_name = "lever"

        def verify_submission(self):
            return None

    engine.adapter = Adapter()
    monkeypatch.setattr("core.ats.engine.time.sleep", lambda *_: None)

    result = engine._wait_for_confirmation("lever", page.url)

    assert result.success is False
    assert "page confirmation" in result.reason.lower() or "no confirmation" in result.reason.lower()


def test_engine_unsupported_platform_message_via_facade():
    from core.shared_modules.ats_apply import apply_url
    page = FakePage(url="https://example.com")
    ok, url, reason = apply_url(page, "https://example.com/jobs")
    assert ok is False
    assert url == "https://example.com/jobs"
    assert "not a supported ATS" in reason


def test_ashby_initialize_opens_application_tab():
    """Ashby hides the form behind an Application tab on public job pages."""
    from core.ats.adapters.ashby import AshbyAdapter

    page = FakePage(url="https://jobs.ashbyhq.com/acme/job-id")
    tab = FakeElement(tag="button")
    tab.inner_text = lambda: "Application"
    page.query_selector_all = lambda selector: [tab] if "button" in selector else []

    adapter = AshbyAdapter()
    adapter._wait_for_form = lambda: None
    adapter._dismiss_overlays = lambda: None
    adapter.initialize(page, {})

    assert tab.clicked is True


def test_ashby_button_choice_does_not_toggle_a_committed_selection(monkeypatch):
    """A second synthetic click deselects Ashby's hidden-checkbox choices."""
    from core.ats.adapters.ashby import AshbyAdapter

    class ChoiceButton:
        def __init__(self):
            self.selected = False
            self.clicks = 0
            self.scripts: list[str] = []

        def scroll_into_view_if_needed(self, **kwargs):
            pass

        def click(self, **kwargs):
            self.clicks += 1
            self.selected = True

        def evaluate(self, script, *args):
            self.scripts.append(script)
            if "MouseEvent" in script:
                # This mirrors Ashby's toggle behavior if a duplicate click
                # sequence is mistakenly sent after the native click.
                self.selected = not self.selected
                return None
            return self.selected

    adapter = AshbyAdapter()
    button = ChoiceButton()
    monkeypatch.setattr("core.ats.adapters.ashby.time.sleep", lambda *_: None)

    assert adapter._react_click(button) is True
    assert button.clicks == 1
    assert button.selected is True
    assert not any("MouseEvent" in script for script in button.scripts)


def test_ashby_required_multi_select_checkbox_group_is_answered():
    from core.ats.adapters.ashby import AshbyAdapter

    class Checkbox:
        def __init__(self, label):
            self.label = label
            self.checked = False

        def is_checked(self):
            return self.checked

        def check(self, **kwargs):
            self.checked = True

        def get_attribute(self, name):
            return self.label if name == "name" else None

        def evaluate(self, script, *args):
            return self.label

    class Title:
        def inner_text(self):
            return "Which software team(s) are you most interested in?"

    class Group:
        def __init__(self, boxes):
            self.boxes = boxes

        def is_visible(self):
            return True

        def query_selector_all(self, selector):
            return self.boxes if "checkbox" in selector else []

        def query_selector(self, selector):
            return Title() if "label" in selector else None

    boxes = [Checkbox("Full Stack"), Checkbox("Systems Software")]
    adapter = AshbyAdapter()
    adapter.page = type("Page", (), {"query_selector_all": lambda _, __: [Group(boxes)]})()
    adapter._resolve_for_field = lambda *args, **kwargs: ["Full Stack", "Systems Software"]

    assert adapter._fill_checkbox_groups() == 2
    assert all(box.checked for box in boxes)


def test_ashby_radio_resolution_uses_field_heading_not_option_label():
    from core.ats.adapters.ashby import AshbyAdapter

    class Radio:
        def __init__(self, label):
            self.label = label
            self.checked = False

        def get_attribute(self, name):
            return {"name": "graduation-year", "value": self.label}.get(name)

        def is_checked(self):
            return self.checked

        def check(self, **kwargs):
            self.checked = True

        def evaluate(self, script, *args):
            if "data-field-entry-id" in script:
                return "Expected Graduation Year"
            return self.label

    radios = [Radio("2026"), Radio("2027")]
    adapter = AshbyAdapter()
    adapter.page = type("Page", (), {"query_selector_all": lambda _, __: radios})()
    seen = []

    def resolve(question, **kwargs):
        seen.append(question)
        return ["2026"]

    adapter._resolve_for_field = resolve
    assert adapter._fill_radio_groups() == 1
    assert radios[0].checked is True
    assert seen == ["Expected Graduation Year"]


def test_invisible_recaptcha_marker_does_not_block_ats_fill():
    from core.ats.mixins.captcha import CaptchaMixin

    class Page:
        def evaluate(self, script):
            return False  # widget exists in markup but is not visible

        def content(self):
            return '<iframe src="https://www.recaptcha.net/recaptcha/api2"></iframe>'

    class Adapter(CaptchaMixin):
        page = Page()

    assert Adapter()._page_has_captcha() is False


@pytest.mark.parametrize("module_name,class_name", [
    ("core.ats.adapters.greenhouse", "GreenhouseAdapter"),
    ("core.ats.adapters.lever", "LeverAdapter"),
    ("core.ats.adapters.bamboohr", "BambooHRAdapter"),
])
def test_ats_radio_groups_resolve_against_parent_question(module_name, class_name):
    """All adapters must not send an option label as the AI question."""
    module = __import__(module_name, fromlist=[class_name])
    adapter = getattr(module, class_name)()

    class Radio:
        def __init__(self, value):
            self.value = value
            self.checked = False

        def get_attribute(self, name):
            return {"name": "expected-graduation", "value": self.value}.get(name)

        def is_checked(self):
            return self.checked

        def check(self, **kwargs):
            self.checked = True

        def evaluate(self, script, *args):
            return "Expected Graduation Year" if "node.closest" in script else self.value

    radios = [Radio("2026"), Radio("2027")]
    adapter.page = type("Page", (), {"query_selector_all": lambda _, __: radios})()
    seen = []
    adapter._resolve_for_field = lambda question, **kwargs: seen.append(question) or ["2026"]

    assert adapter._fill_radio_groups() == 1
    assert radios[0].checked is True
    assert seen == ["Expected Graduation Year"]


def test_detect_dead_job_lever_404():
    from core.ats.engine import _detect_dead_job

    page = FakePage(
        url="https://jobs.lever.co/jobgether/4d0970fa/apply",
        body_text=(
            "Sorry, we couldn't find anything here. "
            "The job posting you're looking for might have closed, or it has been removed. (404 error)."
        ),
    )
    reason = _detect_dead_job(page, page.url)
    assert reason is not None
    assert "unavailable" in reason.lower() or "404" in reason.lower() or "closed" in reason.lower()


def test_detect_dead_job_greenhouse_error_true():
    from core.ats.engine import _detect_dead_job

    page = FakePage(
        url="https://job-boards.greenhouse.io/lawzero?error=true",
        body_text="Jobs at LawZero",
    )
    reason = _detect_dead_job(page, page.url)
    assert reason is not None
    assert "error=true" in reason.lower()


def test_detect_spam_block_ashby():
    from core.ats.engine import _detect_spam_or_block

    page = FakePage(
        url="https://jobs.ashbyhq.com/cerebras/x/application",
        body_text=(
            "We couldn't submit your application. "
            "Your application submission was flagged as possible spam."
        ),
    )
    reason = _detect_spam_or_block(page)
    assert reason is not None
    assert "spam" in reason.lower()


def test_ashby_spam_is_not_treated_as_submitted():
    from core.ats.adapters.ashby import AshbyAdapter

    inst = AshbyAdapter()
    inst.page = FakePage(
        url="https://jobs.ashbyhq.com/cerebras/x/application",
        body_text=(
            "We couldn't submit your application. "
            "Your application submission was flagged as possible spam. "
            "Submit Application"
        ),
    )
    # Keep a submit button so form-gone fallback cannot fire.
    inst.page.set(
        "button:has-text('Submit'), button[type='submit'], "
        "input[type='submit'], button:has-text('Submit application'), "
        "[data-testid='submit-button'], "
        "button.ashby-application-form-submit-button",
        FakeElement(tag="button"),
    )
    assert inst.verify_submission() is None


def test_bamboohr_and_ashby_expose_repair_required_fields():
    from core.ats.adapters import AshbyAdapter, BambooHRAdapter, GreenhouseAdapter

    for cls in (AshbyAdapter, BambooHRAdapter, GreenhouseAdapter):
        assert callable(getattr(cls(), "repair_required_fields"))


def test_bamboohr_routes_generic_uploads_by_their_local_slot(tmp_path, monkeypatch):
    """BambooHR React inputs must not borrow a sibling field's label."""
    from core.ats.adapters import BambooHRAdapter

    class FileInput:
        def __init__(self, slot_text):
            self.slot_text = slot_text
            self.path = ""

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return "file" if name == "type" else None

        def evaluate(self, script, *args):
            if "querySelectorAll('input[type=\"file\"]')" in script:
                return self.slot_text
            if "e.files" in script:
                return 1 if self.path else 0
            return ""

        def set_input_files(self, path):
            self.path = path

    class Page:
        def __init__(self, inputs):
            self.inputs = inputs
            self.frames = []
            self.main_frame = None

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def evaluate(self, *args, **kwargs):
            return ""

        def query_selector_all(self, selector):
            return self.inputs if selector == "input[type='file']" else []

    monkeypatch.setattr("core.ats.mixins.upload.time.sleep", lambda *_: None)
    resume = tmp_path / "resume.pdf"
    cover = tmp_path / "cover.pdf"
    resume.write_text("resume")
    cover.write_text("cover")
    resume_input = FileInput("Resume * Choose File")
    cover_input = FileInput("Cover Letter Choose File")

    adapter = BambooHRAdapter()
    adapter.page = Page([cover_input, resume_input])
    result = adapter.upload_documents(
        resume_path=str(resume), cover_letter_path=str(cover)
    )

    assert result == {"resume": True, "cover": True}
    assert resume_input.path == str(resume)
    assert cover_input.path == str(cover)


def test_bamboohr_never_guesses_resume_slot_from_multiple_unlabeled_uploads(tmp_path, monkeypatch):
    """A bad guess is worse than leaving a required file visibly unresolved."""
    from core.ats.adapters import BambooHRAdapter

    class FileInput:
        def __init__(self):
            self.path = ""

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return "file" if name == "type" else None

        def evaluate(self, script, *args):
            if "e.files" in script:
                return 1 if self.path else 0
            return ""

        def set_input_files(self, path):
            self.path = path

    class Page:
        def __init__(self, inputs):
            self.inputs = inputs
            self.frames = []
            self.main_frame = None

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def evaluate(self, *args, **kwargs):
            return ""

        def query_selector_all(self, selector):
            return self.inputs if selector == "input[type='file']" else []

    monkeypatch.setattr("core.ats.mixins.upload.time.sleep", lambda *_: None)
    resume = tmp_path / "resume.pdf"
    resume.write_text("resume")
    first, second = FileInput(), FileInput()

    adapter = BambooHRAdapter()
    adapter.page = Page([first, second])
    result = adapter.upload_documents(resume_path=str(resume))

    assert result["resume"] is False
    assert first.path == ""
    assert second.path == ""


def test_engine_submit_retries_after_validation(monkeypatch):
    """Soft fail + validation → repair → resubmit → success."""
    from core.ats.engine import ApplicationEngine

    page = FakePage(url="https://creator.bamboohr.com/careers/101")
    engine = ApplicationEngine(page)
    monkeypatch.setattr("core.ats.engine.time.sleep", lambda *_: None)

    state = {"attempt": 0, "repairs": 0}

    class Adapter:
        platform_name = "bamboohr"

        def submit(self):
            state["attempt"] += 1
            return True

        def verify_submission(self):
            # First click leaves form open; second succeeds.
            return "submitted" if state["attempt"] >= 2 else None

        def repair_required_fields(self):
            state["repairs"] += 1
            return 2

        def fill_application(self):
            return None

        def answer_questions(self):
            return 0

        def solve_captcha(self):
            return True

        def _page_has_captcha(self):
            return False

        def _validation_errors(self):
            return ["Please fill in this field.", "Please make a selection."] if state["attempt"] < 2 else []

    engine.adapter = Adapter()
    engine._page_has_captcha = lambda: False
    result = engine._submit_and_confirm("bamboohr", page.url)

    assert result.success is True
    assert state["attempt"] >= 2
    assert state["repairs"] >= 1


# ── captcha detection ────────────────────────────────────────────────

def test_captcha_detection_selectors():
    from core.ats.mixins.captcha import CaptchaMixin

    class Harness(CaptchaMixin):
        platform_name = "test"

    h = Harness()
    h.page = FakePage(url="https://x", html="<html><body>plain form</body></html>")
    assert h._page_has_captcha() is False

    h2 = Harness()
    page2 = FakePage(url="https://x", html="<html><body>plain</body></html>")
    page2.set("iframe[src*='recaptcha']", FakeElement())
    h2.page = page2
    assert h2._page_has_captcha() is True


def test_captcha_detect_type():
    from core.ats.mixins.captcha import CaptchaMixin

    class Harness(CaptchaMixin):
        platform_name = "test"

    h = Harness()
    page = FakePage(url="https://x")
    page.set("iframe[src*='recaptcha']", FakeElement())
    h.page = page
    assert h._detect_captcha_type() == "recaptcha_v2"

    h2 = Harness()
    page2 = FakePage(url="https://x")
    page2.set(".cf-turnstile", FakeElement())
    page2.set("iframe[src*='challenges.cloudflare.com']", FakeElement())
    h2.page = page2
    assert h2._detect_captcha_type() == "turnstile"

    h3 = Harness()
    h3.page = FakePage(url="https://x")
    assert h3._detect_captcha_type() is None


def test_captcha_solve_returns_true_when_absent():
    from core.ats.mixins.captcha import CaptchaMixin

    class Harness(CaptchaMixin):
        platform_name = "test"

    h = Harness()
    h.page = FakePage(url="https://x", html="<html>no captcha here</html>")
    assert h.solve_captcha() is True


# ── question helpers via package ─────────────────────────────────────

def test_package_level_clean_question_text():
    from core.ats.mixins.questions import _clean_question_text
    assert _clean_question_text("  Years of experience * \nYes\nNo  ") == "Years of experience"
    assert _clean_question_text("") == ""


def test_package_level_should_use_ai():
    from core.ats.mixins.questions import _reset_ai_budget, _should_use_ai
    _reset_ai_budget()
    assert _should_use_ai("First Name", None) is False
    assert _should_use_ai("Why do you want this role?", None) is True


def test_package_level_map_pref_to_option_gender_safe():
    from core.ats.mixins.questions import _map_pref_to_option
    assert _map_pref_to_option("male", ["Female", "Male"]) == "Male"
    assert _map_pref_to_option("male", ["Female", "Decline"]) is None
    assert _map_pref_to_option("male", ["Female", "Male", "Decline"]) == "Male"


def test_package_level_us_work_auth():
    from core.ats.mixins.questions import _contains_us_work_auth_question
    assert _contains_us_work_auth_question(
        "Are you legally authorized to work in the United States?"
    )
    assert not _contains_us_work_auth_question(
        "Are you legally authorized to work in Canada?"
    )


def test_package_level_format_required_fail_reason():
    from core.ats.mixins.questions import _format_required_fail_reason
    reason = _format_required_fail_reason(["email", "Years of professional experience?"])
    assert reason.startswith("required_fields_unanswered:")
    assert "email" in reason


# ── registry registration for extension ──────────────────────────────

def test_registry_allows_custom_adapter_registration():
    from core.ats import registry
    from core.ats.base import ATSAdapter

    class DummyAdapter(ATSAdapter):
        platform_name = "dummy"

        @classmethod
        def detect(cls, url):
            return "dummy.example.com" in (url or "")

        def initialize(self, page, profile, **kw):
            pass

        def upload_documents(self):
            return {"resume": False, "cover": False}

        def fill_application(self):
            from core.ats.types import FillStats
            return FillStats()

        def answer_questions(self):
            return 0

        def submit(self):
            return True

        def verify_submission(self):
            return "submitted"

    registry.register("dummy", DummyAdapter)
    assert "dummy" in registry.supported_platforms()
    assert registry._ADAPTERS.get("dummy") is DummyAdapter
    # Cleanup so other tests are unaffected.
    del registry._ADAPTERS["dummy"]
    registry._SPECS.pop("dummy", None)
