"""Shared test fixtures: synthetic answer pages, a fake OCR engine, question lists.

The tests never need real OCR models: ``FakeRecognizer`` returns scripted text,
so every component except the OCR library itself is exercised.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytest

from qmd.config import Settings, load_settings
from qmd.ocr.base import EngineInfo, OcrEngine, TextResult
from qmd.question_list import QuestionList, build_question_list

PAGE_W, PAGE_H = 1488, 2576
HEADER_Y = 235
PITCH = 74
RULE_X = 100
DPI = 200.0


@dataclass
class SyntheticPage:
    image: np.ndarray
    rule_x: int
    line_ys: list[int]
    marker_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)  # x, y, w, h


def make_ruled_page(
    rule_x: int = RULE_X,
    markers: Optional[list[tuple[int, str]]] = None,
    answer_lines: Optional[list[int]] = None,
    valuer_marks: Optional[list[tuple[int, str]]] = None,
) -> SyntheticPage:
    """Draw a ruled booklet page.

    ``markers``      [(band_index, text)] written in the margin (dark blue ink)
    ``answer_lines`` band indices that contain answer text right of the rule
    ``valuer_marks`` [(band_index, text)] written in RED in the margin
    """
    img = np.full((PAGE_H, PAGE_W, 3), 250, np.uint8)
    img[:, :, 0] = 246  # slight paper tint -> a colour scan
    cv2.line(img, (0, HEADER_Y), (PAGE_W, HEADER_Y), (60, 60, 60), 3)
    line_ys = list(range(HEADER_Y, PAGE_H - 60, PITCH))
    for y in line_ys[1:]:
        cv2.line(img, (0, y), (PAGE_W, y), (150, 150, 150), 2)
    cv2.line(img, (rule_x, HEADER_Y), (rule_x, PAGE_H - 40), (40, 40, 40), 3)
    page = SyntheticPage(img, rule_x, line_ys)
    for band, text in markers or []:
        y = line_ys[band] + PITCH - 18
        cv2.putText(img, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (120, 40, 20), 3)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 3)
        page.marker_boxes.append((8, y - th, tw, th))
    for band in answer_lines or []:
        y = line_ys[band] + PITCH - 18
        cv2.putText(img, "The answer text", (rule_x + 30, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (120, 40, 20), 3)
    for band, text in valuer_marks or []:
        y = line_ys[band] + PITCH - 18
        cv2.putText(img, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (30, 30, 220), 3)
    return page


def write_pdf(images: list[np.ndarray], path: Path, dpi: float = DPI) -> Path:
    """Write BGR images as a PDF with one embedded JPEG per page (like a scanner)."""
    from PIL import Image

    pil = [Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)) for im in images]
    pil[0].save(path, "PDF", resolution=dpi, save_all=True, append_images=pil[1:])
    return path


class FakeRecognizer:
    """Returns text from a callable (crop -> (text, confidence)) or a fixed list."""

    def __init__(self, fn: Callable[[np.ndarray], tuple[str, float]]) -> None:
        self.fn = fn
        self.calls = 0

    def recognize(self, images: list[np.ndarray]) -> list[TextResult]:
        self.calls += len(images)
        out = []
        for im in images:
            text, conf = self.fn(im)
            out.append(TextResult(text=text, recognition_confidence=conf))
        return out


def fake_engine(fn: Callable[[np.ndarray], tuple[str, float]]) -> OcrEngine:
    return OcrEngine(recognizer=FakeRecognizer(fn), detector=None,
                     info=EngineInfo(ocr_engine="fake-1.0", ocr_model="fake-rec", ocr_model_version="test"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = load_settings(Path("config/default.yaml"))
    data = s.model_dump()
    data["app"]["output_root"] = tmp_path / "out"
    return Settings.model_validate(data)


@pytest.fixture
def eight_questions() -> QuestionList:
    return build_question_list([str(i) for i in range(1, 9)], exam_id="TEST", version="1",
                               rules={"min_answers": 1, "max_answers": 8})


@pytest.fixture
def multilevel() -> QuestionList:
    return build_question_list(
        ["1", "2", "3", "4(a)", "4(b)", "5(a)(i)", "5(a)(ii)", {"id": "10(b)", "target": True},
         "10(b)(1)", "10(b)(2)"],
        exam_id="ML", version="1",
    )
