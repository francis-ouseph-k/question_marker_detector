"""Candidate detectors (TD §7) and confidence calibration (TD §19.2)."""

import json

import pytest

from qmd.config import DetectionConfig, InkConfig, LayoutConfig, RegionConfig
from qmd.detection import MarginComponentDetector, OcrModelDetector
from qmd.errors import ConfigurationError
from qmd.ink import to_student_gray
from qmd.ocr.base import TextRegion
from qmd.ocr.calibration import Calibrator
from qmd.page_layout import detect_layout
from qmd.region import build_margin_region
from tests.conftest import DPI, make_ruled_page


def _region(page):
    layout = detect_layout(page.image, DPI, LayoutConfig())
    gray, valuer = to_student_gray(page.image, InkConfig(), True)
    return build_margin_region(gray, valuer, layout, DPI, RegionConfig(), InkConfig())


def test_component_detector_finds_markers_not_answer_text():
    page = make_ruled_page(markers=[(2, "Ans 4"), (8, "5")], answer_lines=[2, 3, 8])
    region = _region(page)
    cands = MarginComponentDetector(DetectionConfig()).detect(region)
    assert len(cands) == 2
    for cand in cands:
        assert cand.box.x < region.rule_x           # starts in the margin


def test_component_detector_ignores_red_valuer_marks():
    page = make_ruled_page(valuer_marks=[(3, "7"), (9, "3")])
    assert MarginComponentDetector(DetectionConfig()).detect(_region(page)) == []


class _FakeTextDetector:
    """Returns one box per call at the same place inside each tile."""

    def __init__(self):
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        if self.calls == 1:
            return [TextRegion([[5, 40], [80, 40], [80, 70], [5, 70]], 0.9),     # margin marker
                    TextRegion([[110, 40], [300, 40], [300, 70], [110, 70]], 0.9)]  # answer text
        return []


def test_ocr_model_detector_keeps_margin_boxes_only():
    page = make_ruled_page(markers=[(0, "Ans 1")], answer_lines=[0])
    region = _region(page)
    fake = _FakeTextDetector()
    cands = OcrModelDetector(fake, DetectionConfig()).detect(region)
    assert fake.calls > 3                      # the strip was tiled
    assert len(cands) == 1 and cands[0].box.x == 5
    assert cands[0].polygon == [[5, 40], [80, 40], [80, 70], [5, 70]]   # detector outline kept (TD §8)


def test_merged_fragments_have_no_single_polygon():
    from qmd.detection import RegionCandidate, merge_boxes_by_line
    from qmd.models import BBox

    region = _region(make_ruled_page())
    y = region.line_ys[3] + 20
    a = RegionCandidate(BBox(x=5, y=y, width=30, height=30), 0.9, polygon=[[5, y], [35, y], [35, y + 30], [5, y + 30]])
    b = RegionCandidate(BBox(x=40, y=y, width=20, height=30), 0.8, polygon=[[40, y], [60, y], [60, y + 30], [40, y + 30]])
    single = merge_boxes_by_line([a], region, gap_px=10)
    assert single[0].polygon == a.polygon
    merged = merge_boxes_by_line([a, b], region, gap_px=10)
    assert len(merged) == 1 and merged[0].polygon is None


def test_calibrator(tmp_path):
    assert Calibrator()(0.7) == 0.7
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({"points": [[0, 0], [0.8, 0.5], [1.0, 0.9]]}))
    cal = Calibrator.from_file(path)
    assert cal(0.9) == pytest.approx(0.7)
    assert not cal.is_identity
    for empty in ([], [[0.5, 0.5]]):
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"points": empty}))
        with pytest.raises(ConfigurationError, match="at least 2 points"):
            Calibrator.from_file(p)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"points": [[0, 0.9], [1, 0.1]]}))
    with pytest.raises(ConfigurationError):
        Calibrator.from_file(bad)


def test_bundled_onnx_model_is_hashed():
    pytest.importorskip("rapidocr")
    from qmd.config import OcrConfig
    from qmd.ocr import create_engine

    info = create_engine(OcrConfig(backend="onnx"), need_detector=True).info
    assert info.ocr_model.count("@sha256:") == 2        # det + rec both identified by content (NFR-06)
