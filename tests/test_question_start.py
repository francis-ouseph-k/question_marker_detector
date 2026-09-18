"""Question start (TD §15, FR-11)."""

from qmd.config import InkConfig, LayoutConfig, QuestionStartConfig
from qmd.ink import to_student_gray
from qmd.models import BBox, QuestionStartMethod
from qmd.page_layout import WorkLayout, detect_layout
from qmd.question_start import QuestionStartFinder, answer_ink_mask
from tests.conftest import DPI, make_ruled_page


def _setup(answer_lines):
    page = make_ruled_page(answer_lines=answer_lines)
    layout = detect_layout(page.image, DPI, LayoutConfig())
    gray, _ = to_student_gray(page.image, InkConfig(), True)
    return page, layout, answer_ink_mask(gray, 150, DPI)


def _marker_in_band(layout, band):
    y = layout.ruled_line_ys[band] + 20
    return BBox(x=10, y=y, width=60, height=35)


def test_answer_on_marker_line():
    page, layout, ink = _setup([5, 6])
    start = QuestionStartFinder(QuestionStartConfig()).find(ink, layout, _marker_in_band(layout, 5), None, DPI)
    assert start.method == QuestionStartMethod.LANDMARK_LINE
    assert start.y == layout.ruled_line_ys[5]
    assert abs(start.x - page.rule_x) <= 2
    assert start.confidence >= 0.9


def test_answer_starts_two_lines_below():
    _, layout, ink = _setup([7, 8])
    start = QuestionStartFinder(QuestionStartConfig()).find(ink, layout, _marker_in_band(layout, 5), None, DPI)
    assert start.y == layout.ruled_line_ys[7]
    assert start.lines_below_marker == 2


def test_search_stops_at_next_marker():
    _, layout, ink = _setup([8])
    start = QuestionStartFinder(QuestionStartConfig()).find(ink, layout, _marker_in_band(layout, 5), 7, DPI)
    assert start.y == layout.ruled_line_ys[5]
    assert start.confidence < 0.5          # no ink before the next marker -> uncertain


def test_fallback_without_landmarks():
    ink = answer_ink_mask(make_ruled_page().image[:, :, 0] * 0 + 255, 150, DPI)
    start = QuestionStartFinder(QuestionStartConfig()).find(ink, WorkLayout(), BBox(x=10, y=500, width=60, height=30),
                                                            None, DPI)
    assert start.method == QuestionStartMethod.FALLBACK_OFFSET
    assert start.x > 70 and start.y < 500
