"""End-to-end pipeline on synthetic PDFs with a fake recognizer.

Checks the parts that tie everything together: margin region, candidate
detection, canonical coordinates, question start, statuses, outputs, CLI.
"""

import json

import cv2
import pytest

from qmd import cli
from qmd.models import ContentClass, MarkerStatus, ProcessingStatus, QuestionStartMethod
from qmd.pipeline import PageProcessor
from qmd.runner import Runner
from tests.conftest import fake_engine, make_ruled_page, write_pdf


def _script_pdf(tmp_path, name="S1"):
    cover = make_ruled_page()                                      # page 1: role COVER
    blank = make_ruled_page()                                      # page 2: role DO_NOT_WRITE
    p3 = make_ruled_page(markers=[(1, "Ans 1")], answer_lines=[1, 2, 3], valuer_marks=[(6, "7")])
    p4 = make_ruled_page(rule_x=185, markers=[(2, "Ans 2"), (9, "Ans 3")], answer_lines=[2, 3, 10, 11])
    p5 = make_ruled_page()                                         # blank answer page
    return write_pdf([cover.image, blank.image, p3.image, p4.image, p5.image], tmp_path / f"{name}.pdf"), p3, p4


def _reader(texts_by_call):
    """Recognizer returning 'Ans N' in order of calls."""
    it = iter(texts_by_call)

    def fn(_crop):
        return next(it, ("", 0.0))

    return fn


def test_script_end_to_end(tmp_path, settings, eight_questions, monkeypatch):
    pdf, p3, p4 = _script_pdf(tmp_path)
    engine = fake_engine(_reader([("Ans 1", 0.97), ("Ans 2", 0.96), ("Ans 3", 0.95)]))
    monkeypatch.setattr("qmd.pipeline.create_engine", lambda *a, **k: engine)

    with Runner(settings, eight_questions) as runner:
        result = runner.process_script(pdf)

    roles = [p.processing_status for p in result.pages]
    assert roles[:2] == [ProcessingStatus.SKIPPED_BY_ROLE] * 2
    assert result.pages[4].content_class == ContentClass.BLANK
    markers = [m for p in result.pages for m in p.markers if m.status != MarkerStatus.DISCARDED]
    assert [m.canonical_question_id for m in markers] == ["1", "2", "3"]
    assert all(m.status == MarkerStatus.ACCEPTED for m in markers), [m.review_reasons for m in markers]
    assert result.flags == []

    # Marker box is where we drew it (canonical page pixels), not in crop coordinates.
    m1 = markers[0]
    x, y, w, h = p3.marker_boxes[0]
    assert abs(m1.marker_x - x) <= 6 and abs(m1.marker_y - y) <= 8
    # Question start = margin rule x, top of the marker's ruled line.
    assert abs(m1.question_start_x - p3.rule_x) <= 2
    assert abs(m1.question_start_y - p3.line_ys[1]) <= 3
    assert m1.question_start_method == QuestionStartMethod.LANDMARK_LINE
    # "Ans 3" is on line 9 but its answer text starts on line 10 -> start snaps to line 10.
    m3 = markers[2]
    assert abs(m3.question_start_x - 185) <= 2 and abs(m3.question_start_y - p4.line_ys[10]) <= 3

    # The red valuer "7" was suppressed and recorded, never recognised.
    assert result.pages[2].suppressed_valuer_ink
    assert engine.recognizer.calls == 3

    out = settings.app.output_root / "S1"
    saved = json.loads((out / "script_result.json").read_text())
    assert saved["pages"][2]["markers"][0]["canonical_question_id"] == "1"
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["ocr_engine"] == "fake-1.0"
    assert (out / "pages" / "page_003.jpg").exists()
    assert (out / "debug" / "page_003_overlay.jpg").exists()
    assert list((out / "crops").glob("page_003_c*.png"))
    assert not (out / "pages" / "page_001.jpg").exists()          # cover page never decoded (NFR-09)


def test_rerun_is_idempotent(tmp_path, settings, eight_questions, monkeypatch):
    pdf, _, _ = _script_pdf(tmp_path)
    monkeypatch.setattr("qmd.pipeline.create_engine",
                        lambda *a, **k: fake_engine(lambda c: ("Ans 1", 0.97)))
    with Runner(settings, eight_questions) as runner:
        first = runner.process_script(pdf)
        second = runner.process_script(pdf)
    ids1 = [m.marker_result_id for p in first.pages for m in p.markers]
    ids2 = [m.marker_result_id for p in second.pages for m in p.markers]
    assert ids1 == ids2 and len(ids1) == len(set(ids1))
    # Same answer for Q1 three times -> duplicates flagged, sent to review.
    assert "DUPLICATE_QUESTION" in [f.value for f in second.flags]


def test_low_confidence_and_unknown_markers(tmp_path, settings, eight_questions, monkeypatch):
    pdf, _, _ = _script_pdf(tmp_path)
    monkeypatch.setattr("qmd.pipeline.create_engine",
                        lambda *a, **k: fake_engine(_reader([("Ans 1", 0.40), ("Ans 99", 0.99), ("Q.No.", 0.9)])))
    with Runner(settings, eight_questions) as runner:
        r = runner.process_script(pdf)
    ms = [m for p in r.pages for m in p.markers]
    assert ms[0].status == MarkerStatus.REVIEW_REQUIRED
    assert ms[1].status == MarkerStatus.UNRESOLVED and ms[1].canonical_question_id is None
    assert ms[2].status == MarkerStatus.UNRESOLVED     # prefix without an ID and nothing below it


def test_failed_page_does_not_lose_others(tmp_path, settings, eight_questions, monkeypatch):
    pdf, _, _ = _script_pdf(tmp_path)
    monkeypatch.setattr("qmd.pipeline.create_engine",
                        lambda *a, **k: fake_engine(lambda c: ("Ans 1", 0.97)))
    original = PageProcessor.process_page

    def flaky(self, pdf_path, script_id, page_index, out_dir):
        if page_index == 4:
            raise RuntimeError("boom")
        return original(self, pdf_path, script_id, page_index, out_dir)

    monkeypatch.setattr(PageProcessor, "process_page", flaky)
    with Runner(settings, eight_questions) as runner:
        r = runner.process_script(pdf)
    assert r.pages[3].processing_status == ProcessingStatus.FAILED_PERMANENT
    assert r.pages[3].error_code == "UNEXPECTED_ERROR"
    assert r.pages[2].markers                   # page 3 still processed
    assert "INCOMPLETE" in [f.value for f in r.flags]


def test_grayscale_scan_uses_stricter_gate(tmp_path, settings, eight_questions, monkeypatch):
    page = make_ruled_page(markers=[(1, "Ans 1")], answer_lines=[1])
    gray = cv2.cvtColor(cv2.cvtColor(page.image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    pdf = write_pdf([gray, gray, gray], tmp_path / "g.pdf")
    monkeypatch.setattr("qmd.pipeline.create_engine", lambda *a, **k: fake_engine(lambda c: ("Ans 1", 0.85)))
    with Runner(settings, eight_questions) as runner:
        r = runner.process_script(pdf)
    m = r.pages[2].markers[0]
    assert "COLOUR_SEPARATION_UNAVAILABLE" in r.pages[2].flags
    assert m.status == MarkerStatus.REVIEW_REQUIRED


def test_cli_run_and_validate(tmp_path, settings, monkeypatch, capsys):
    pdf, _, _ = _script_pdf(tmp_path, name="CLI")
    q = tmp_path / "q.txt"
    q.write_text("\n".join(str(i) for i in range(1, 9)))
    monkeypatch.setattr("qmd.pipeline.create_engine",
                        lambda *a, **k: fake_engine(_reader([("Ans 1", 0.97), ("Ans 2", 0.96), ("Ans 3", 0.95)])))
    code = cli.main(["run", "-i", str(tmp_path), "-q", str(q), "-o", str(tmp_path / "o"), "--no-debug-images"])
    out = capsys.readouterr().out
    assert code == 0
    assert "SCRIPT CLI" in out and "ACCEPTED" in out
    assert cli.main(["validate-questions", str(q)]) == 0


@pytest.mark.parametrize("args", [["run", "-i", "/nonexistent", "-q", "x.txt"]])
def test_cli_input_errors(args):
    assert cli.main(args) == 2


class _SlantedDetector:
    """Text detector returning one slanted (non-rectangular) outline in the first tile."""

    def __init__(self):
        self.first = True

    def detect(self, image):
        from qmd.ocr.base import TextRegion

        if not self.first:
            return []
        self.first = False
        return [TextRegion([[10, 100], [80, 92], [82, 122], [12, 130]], 0.95)]


def test_ocr_model_polygon_reaches_output(tmp_path, settings, eight_questions, monkeypatch):
    from qmd.config import Settings
    from qmd.ocr.base import EngineInfo, OcrEngine
    from tests.conftest import FakeRecognizer

    page = make_ruled_page(markers=[(1, "Ans 1")], answer_lines=[1])
    pdf = write_pdf([page.image, page.image, page.image], tmp_path / "p.pdf")
    data = settings.model_dump()
    data["detection"]["method"] = "ocr_model"
    cfg = Settings.model_validate(data)
    engine = OcrEngine(FakeRecognizer(lambda c: ("Ans 1", 0.97)), _SlantedDetector(),
                       EngineInfo("fake", "fake", "t"))
    monkeypatch.setattr("qmd.pipeline.create_engine", lambda *a, **k: engine)
    with Runner(cfg, eight_questions) as runner:
        r = runner.process_script(pdf)
    m = r.pages[2].markers[0]
    ys = [p[1] for p in m.marker_polygon]
    assert ys[0] != ys[1]                                   # slanted outline, not a synthesised box
    assert m.marker_polygon[0][0] == 10                     # region x offset is 0
    assert m.marker_y == min(ys) and m.marker_height == max(ys) - min(ys)
