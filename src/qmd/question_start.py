"""Question start (TD §14-15, Requirements FR-11).

Definition
----------
* x = the margin rule (left edge of the answer area) + ``start_padding_mm``
* y = top ruled line of the first writing line, at or below the marker's line,
      that contains answer ink.

Phase-1 method ``LANDMARK_LINE``::

    L0 = line band containing the marker's vertical centre
    for L in L0 .. L0 + max_lines_below:
        stop if L reaches the band of the next marker on this page
        if band L has answer ink right of the margin rule -> start = (rule_x, line_y[L])
    no ink found -> start = (rule_x, line_y[L0]) with low confidence (START_UNCERTAIN)

``FALLBACK_OFFSET`` (landmarks missing): x = right edge of marker + offset,
y = marker top - 1 mm. Always sent to review by the gate.

The question start is never "the right edge of the OCR crop" (TD §15).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from qmd.config import QuestionStartConfig, mm_to_px
from qmd.models import BBox, QuestionStartMethod
from qmd.page_layout import WorkLayout

CONF_SAME_LINE = 0.95
CONF_NEXT_LINE = 0.85
CONF_FURTHER = 0.70
CONF_NO_INK = 0.30
CONF_FALLBACK = 0.20


@dataclass
class QuestionStart:
    x: int                      # WORK coordinates
    y: int
    method: QuestionStartMethod
    confidence: float
    lines_below_marker: Optional[int] = None


class QuestionStartFinder:
    def __init__(self, cfg: QuestionStartConfig) -> None:
        self.cfg = cfg

    def find(self, answer_ink: np.ndarray, layout: WorkLayout, marker: BBox, next_marker_band: Optional[int],
             dpi: float) -> QuestionStart:
        """``answer_ink``: 0/1 mask of student ink for the whole WORK page, printed lines removed."""
        if not layout.found or layout.margin_rule_x is None:
            return QuestionStart(
                x=marker.x2 + mm_to_px(self.cfg.fallback_offset_mm, dpi),
                y=max(0, marker.y - mm_to_px(1.0, dpi)),
                method=QuestionStartMethod.FALLBACK_OFFSET,
                confidence=CONF_FALLBACK,
            )

        ys = layout.ruled_line_ys
        pitch = layout.line_pitch_px or float(np.median(np.diff(ys)))
        l0 = max(0, layout.band_index(marker.center_y) or 0)
        start_x = layout.margin_rule_x + mm_to_px(self.cfg.start_padding_mm, dpi)
        x0 = layout.margin_rule_x + max(3, mm_to_px(0.8, dpi))
        x1 = answer_ink.shape[1] - mm_to_px(self.cfg.right_margin_mm, dpi)
        inset = max(2, int(pitch * 0.08))  # stay clear of the printed line itself

        for offset in range(0, self.cfg.max_lines_below + 1):
            band = l0 + offset
            if band >= len(ys):
                break
            if offset > 0 and next_marker_band is not None and band >= next_marker_band:
                break
            top = ys[band]
            bottom = ys[band + 1] if band + 1 < len(ys) else int(top + pitch)
            area = answer_ink[top + inset: bottom - inset, x0:x1]
            if area.size and float(area.mean()) >= self.cfg.min_ink_ratio:
                conf = CONF_SAME_LINE if offset == 0 else CONF_NEXT_LINE if offset <= 2 else CONF_FURTHER
                return QuestionStart(start_x, top, QuestionStartMethod.LANDMARK_LINE, conf, offset)

        return QuestionStart(start_x, ys[l0], QuestionStartMethod.LANDMARK_LINE, CONF_NO_INK, None)


def answer_ink_mask(student_gray_work: np.ndarray, dark_threshold: int, dpi: float) -> np.ndarray:
    """0/1 mask of student ink with printed horizontal/vertical lines removed."""
    ink = (student_gray_work < dark_threshold).astype(np.uint8)
    run = max(10, mm_to_px(8.0, dpi))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (run, 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, run * 3)))
    return ink & (1 - np.maximum(horiz, vert))
