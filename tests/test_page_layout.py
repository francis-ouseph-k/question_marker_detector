"""Page geometry on synthetic ruled pages (TD §6.1)."""

import cv2
import numpy as np
import pytest

from qmd import geometry
from qmd.config import LayoutConfig
from qmd.page_layout import detect_layout, prepare_work_image
from tests.conftest import DPI, HEADER_Y, PITCH, make_ruled_page


@pytest.mark.parametrize("rule_x", [60, 100, 185])
def test_margin_rule_and_lines(rule_x):
    page = make_ruled_page(rule_x=rule_x, answer_lines=[2, 5, 6])
    layout = detect_layout(page.image, DPI, LayoutConfig())
    assert layout.found
    assert abs(layout.margin_rule_x - rule_x) <= 2
    assert abs(layout.header_y - HEADER_Y) <= 3
    assert abs(layout.line_pitch_px - PITCH) <= 2
    assert abs(layout.ruled_line_ys[1] - (HEADER_Y + PITCH)) <= 3


def test_band_index():
    layout = detect_layout(make_ruled_page().image, DPI, LayoutConfig())
    y = layout.ruled_line_ys[3] + 10
    assert layout.band_index(y) == 3
    assert layout.band_index(10) == -1


def test_blank_image_has_no_landmarks():
    img = np.full((2576, 1488, 3), 255, np.uint8)
    layout = detect_layout(img, DPI, LayoutConfig())
    assert not layout.found and layout.margin_rule_x is None


def _rotate(img, angle):
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))


@pytest.mark.parametrize("angle", [1.0, -1.2])
def test_deskew_makes_rule_vertical(angle):
    page = make_ruled_page(answer_lines=[3])
    skewed = _rotate(page.image, angle)
    result = prepare_work_image(skewed, DPI, LayoutConfig())
    assert result.deskewed
    assert abs(abs(result.skew_deg) - abs(angle)) < 0.3
    again = prepare_work_image(result.image, DPI, LayoutConfig())
    assert abs(again.skew_deg) < 0.2
    # Mapping a work point back to canonical and forward again is consistent.
    back = geometry.invert(result.canonical_to_work)
    p = geometry.apply_points(result.canonical_to_work, geometry.apply_points(back, [[500, 900]]))
    assert np.allclose(p, [[500, 900]])


def test_small_skew_not_corrected():
    page = make_ruled_page()
    result = prepare_work_image(_rotate(page.image, 0.1), DPI, LayoutConfig())
    assert not result.deskewed
