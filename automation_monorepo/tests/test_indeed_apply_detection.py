"""Unit tests for Easy Apply false-fail detection fixes."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_session_helpers():
    from jobbots.core.shared_modules.indeed import session
    return session.__dict__


class _FakePage:
    def __init__(self, url, title="", body=""):
        self.url = url
        self._title = title
        self._body = body

    def title(self):
        return self._title

    def query_selector(self, sel):
        if sel == "body":
            class B:
                def __init__(self, t):
                    self._t = t

                def inner_text(self):
                    return self._t

            return B(self._body)
        return None


def test_indeed_auth_is_not_external_wall():
    ns = _load_session_helpers()
    is_s, reason = ns["_is_sign_in_page"](
        _FakePage("https://secure.indeed.com/auth?continue=https://ca.indeed.com")
    )
    assert is_s is False
    assert reason == ""


def test_external_login_still_detected():
    ns = _load_session_helpers()
    is_s, reason = ns["_is_sign_in_page"](
        _FakePage("https://company.workday.com/en-US/login")
    )
    assert is_s is True
    assert "login" in reason.lower() or "Login" in reason


def test_indeed_property_url():
    ns = _load_session_helpers()
    assert ns["_is_indeed_property_url"]("https://ca.indeed.com/viewjob?jk=abc")
    assert ns["_is_indeed_property_url"]("https://smartapply.indeed.com/beta/x")
    assert not ns["_is_indeed_property_url"]("https://evil.example/?ref=indeed.com")


def test_profile_lease_default_ttl_two_hours():
    import sys
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from core.browser.profile_lease import ProfileLease

    assert ProfileLease("p").ttl_seconds == 7200
