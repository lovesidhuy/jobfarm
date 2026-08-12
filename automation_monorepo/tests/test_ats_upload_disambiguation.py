import pytest
from jobbots.core.ats.mixins.upload import UploadMixin


class MockElement:
    def __init__(self, element_id="", name="", aria_label="", class_name="", has_files=False, text_content=""):
        self.id = element_id
        self.name = name
        self.aria_label = aria_label
        self.class_name = class_name
        self._has_files = has_files
        self.text_content = text_content
        self.files_set = []

    def get_attribute(self, attr):
        if attr == "id":
            return self.id
        if attr == "name":
            return self.name
        if attr == "aria-label":
            return self.aria_label
        if attr == "class":
            return self.class_name
        return ""

    def evaluate(self, script, *args):
        if "e.files && e.files.length" in script:
            return 1 if self._has_files else 0
        return self.text_content

    def set_input_files(self, path):
        self.files_set.append(path)
        self._has_files = True


class MockPage:
    def __init__(self, elements):
        self.elements = elements

    def query_selector_all(self, selector):
        if "input[type='file']" in selector:
            return self.elements
        return []

    def evaluate(self, script, *args):
        return ""

    def wait_for_load_state(self, *args, **kwargs):
        pass

    @property
    def frames(self):
        return []

    @property
    def main_frame(self):
        return self


class DummyAdapter(UploadMixin):
    def __init__(self, page):
        self.page = page
        self.profile = {}


def test_bamboohr_cover_filled_resume_empty_edge_case(tmp_path):
    """Test when cover letter input has a file but resume input was left empty."""
    resume_file = tmp_path / "resume.pdf"
    cover_file = tmp_path / "cover.pdf"
    resume_file.write_text("resume")
    cover_file.write_text("cover")

    resume_input = MockElement(element_id="resume", name="resume", has_files=False)
    cover_input = MockElement(element_id="cover_letter", name="cover_letter", has_files=True)

    page = MockPage([resume_input, cover_input])
    adapter = DummyAdapter(page)

    res = adapter.upload_documents(resume_path=str(resume_file), cover_letter_path=str(cover_file))

    assert res["resume"] is True
    assert res["cover"] is True
    assert len(resume_input.files_set) >= 1
    assert str(resume_file) in resume_input.files_set[0]
    assert len(cover_input.files_set) >= 1
    assert str(cover_file) in cover_input.files_set[0]


def test_resume_and_cover_both_empty(tmp_path):
    resume_file = tmp_path / "resume.pdf"
    cover_file = tmp_path / "cover.pdf"
    resume_file.write_text("resume")
    cover_file.write_text("cover")

    resume_input = MockElement(element_id="resume", name="resume", has_files=False)
    cover_input = MockElement(element_id="cover_letter", name="cover_letter", has_files=False)

    page = MockPage([resume_input, cover_input])
    adapter = DummyAdapter(page)

    res = adapter.upload_documents(resume_path=str(resume_file), cover_letter_path=str(cover_file))

    assert res["resume"] is True
    assert res["cover"] is True
    assert str(resume_file) in resume_input.files_set[0]
    assert str(cover_file) in cover_input.files_set[0]


def test_resume_single_slot_form(tmp_path):
    resume_file = tmp_path / "resume.pdf"
    resume_file.write_text("resume")

    resume_input = MockElement(element_id="resume_file", name="resume", has_files=False)

    page = MockPage([resume_input])
    adapter = DummyAdapter(page)

    res = adapter.upload_documents(resume_path=str(resume_file))

    assert res["resume"] is True
    assert str(resume_file) in resume_input.files_set[0]
