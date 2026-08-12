import pytest
from jobbots.core.ats.dom_autofill import DOMAutofillEngine, FillStats, INJECT_VALUES_JS, SCAN_FIELDS_JS


class MockPage:
    def __init__(self, scanned_fields=None, fill_count=0):
        self.scanned_fields = scanned_fields or []
        self.fill_count = fill_count
        self.evaluated_scripts = []

    def evaluate(self, script, *args):
        self.evaluated_scripts.append((script, args))
        if "cleanText" in script:
            return self.scanned_fields
        if "injectionPayload" in script:
            return self.fill_count or len(args[0] if args else [])
        return None


def test_scan_page_fields():
    mock_fields = [
        {"id": "ats_field_1", "tag": "input", "type": "text", "label": "First Name", "current_value": ""},
        {"id": "ats_field_2", "tag": "input", "type": "text", "label": "Last Name", "current_value": ""},
        {"id": "ats_field_3", "tag": "input", "type": "email", "label": "Email", "current_value": ""},
        {"id": "ats_field_4", "tag": "select", "type": "select", "label": "Country", "options": ["Canada", "USA"], "current_value": ""},
    ]
    page = MockPage(scanned_fields=mock_fields)
    res = DOMAutofillEngine.scan_page_fields(page)
    assert len(res) == 4
    assert res[0]["label"] == "First Name"


def test_scan_page_fields_keeps_child_frame_context():
    main = MockPage(scanned_fields=[{"id": "ats_field_1", "type": "text", "label": "Name"}])
    child = MockPage(scanned_fields=[{"id": "ats_field_1", "type": "text", "label": "Embedded question"}])
    main.frames = [main, child]

    res = DOMAutofillEngine.scan_page_fields(main)

    assert [field["_frame_index"] for field in res] == [0, 1]
    # Same DOM ids are safe because later injection is grouped by frame.
    assert [field["id"] for field in res] == ["ats_field_1", "ats_field_1"]


def test_scanner_and_injector_cover_open_shadow_dom_and_rich_text():
    """Keep the scanner/injector traversal in sync for non-native controls."""
    assert "queryAllPiercing('select')" in SCAN_FIELDS_JS
    assert "queryAllPiercing('input[type=\"radio\"]')" in SCAN_FIELDS_JS
    assert "contenteditable" in SCAN_FIELDS_JS
    assert "findByAtsId" in INJECT_VALUES_JS


def test_resolve_field_answers():
    fields = [
        {"id": "ats_field_1", "tag": "input", "type": "text", "label": "First Name", "current_value": ""},
        {"id": "ats_field_2", "tag": "input", "type": "text", "label": "Last Name", "current_value": ""},
        {"id": "ats_field_3", "tag": "input", "type": "email", "label": "Email", "current_value": ""},
        {"id": "ats_field_4", "tag": "input", "type": "tel", "label": "Phone Number", "current_value": ""},
        {"id": "ats_field_5", "tag": "select", "type": "select", "label": "Country", "options": ["Canada", "USA"], "current_value": ""},
    ]
    profile = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane.doe@example.com",
        "phone": "5551234567",
        "country": "Canada",
    }
    payload = DOMAutofillEngine.resolve_field_answers(fields, profile)
    assert len(payload) == 5
    by_id = {item["id"]: item["value"] for item in payload}
    assert by_id["ats_field_1"] == "Jane"
    assert by_id["ats_field_2"] == "Doe"
    assert by_id["ats_field_3"] == "jane.doe@example.com"
    assert by_id["ats_field_4"] == "5551234567"
    assert by_id["ats_field_5"] == "Canada"


def test_autofill_end_to_end_mock():
    mock_fields = [
        {"id": "ats_field_1", "tag": "input", "type": "text", "label": "First Name", "current_value": ""},
        {"id": "ats_field_2", "tag": "input", "type": "text", "label": "Last Name", "current_value": ""},
    ]
    profile = {"first_name": "Jane", "last_name": "Doe"}
    page = MockPage(scanned_fields=mock_fields, fill_count=2)
    stats = DOMAutofillEngine.autofill(page, profile)
    assert stats.total == 2
    assert stats.filled == 2
    assert stats.skipped == 0
