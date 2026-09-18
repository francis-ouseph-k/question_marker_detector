"""Valuer-ink separation (TD §6.3) and page content class (TD §17.2)."""

import cv2
import numpy as np

from qmd.config import InkConfig, TriageConfig
from qmd.ink import is_colour_scan, to_student_gray, valuer_ink_mask
from qmd.models import ContentClass
from qmd.triage import classify_content
from tests.conftest import DPI, HEADER_Y, make_ruled_page


def test_red_valuer_ink_removed_blue_kept():
    page = make_ruled_page(markers=[(4, "5")], valuer_marks=[(2, "7")])
    cfg = InkConfig()
    mask = valuer_ink_mask(page.image, cfg)
    gray, _ = to_student_gray(page.image, cfg, colour_scan=True)
    red_band = gray[page.line_ys[2] + 5: page.line_ys[3] - 5, 0:page.rule_x - 5]
    blue_band = gray[page.line_ys[4] + 5: page.line_ys[5] - 5, 0:page.rule_x - 5]
    assert mask.any()
    assert red_band.min() > 200          # red "7" painted white
    assert blue_band.min() < 150         # student's "5" untouched


def test_grayscale_scan_detected():
    page = make_ruled_page(markers=[(1, "2")])
    gray = cv2.cvtColor(cv2.cvtColor(page.image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    assert is_colour_scan(page.image, InkConfig())
    assert not is_colour_scan(gray, InkConfig())


def test_colour_separation_skipped_for_grayscale():
    page = make_ruled_page(valuer_marks=[(2, "7")])
    _, mask = to_student_gray(page.image, InkConfig(), colour_scan=False)
    assert not mask.any()


def test_content_class():
    cfg, ink = TriageConfig(), InkConfig()
    blank = make_ruled_page()
    g, _ = to_student_gray(blank.image, ink, True)
    assert classify_content(g, DPI, cfg, ink.dark_ink_threshold, HEADER_Y)[0] == ContentClass.BLANK

    full = make_ruled_page(answer_lines=list(range(0, 28)))
    g, _ = to_student_gray(full.image, ink, True)
    assert classify_content(g, DPI, cfg, ink.dark_ink_threshold, HEADER_Y)[0] == ContentClass.NORMAL

    red_only = make_ruled_page(valuer_marks=[(3, "7"), (8, "3")])
    g, _ = to_student_gray(red_only.image, ink, True)
    assert classify_content(g, DPI, cfg, ink.dark_ink_threshold, HEADER_Y)[0] == ContentClass.BLANK


def test_faint_show_through_is_blank():
    page = make_ruled_page()
    cv2.putText(page.image, "show through text", (300, 900), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (205, 205, 205), 3)
    g, _ = to_student_gray(page.image, InkConfig(), True)
    cls, _ = classify_content(g, DPI, TriageConfig(), InkConfig().dark_ink_threshold, HEADER_Y)
    assert cls == ContentClass.BLANK
    assert np.all(g >= 0)
