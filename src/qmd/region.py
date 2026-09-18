"""Question-marker region (TD §6.2, §6.3; Requirements FR-02).

The region is defined RELATIVE TO PAGE LANDMARKS, in millimetres:

    left   = page edge
    right  = margin_rule_x + spill_mm
    top    = header_y + top_offset_mm
    bottom = page bottom

In the OCR copy of the region:
* valuer (red/green) ink is painted white,
* the margin rule itself is painted white, so it is not read as '1' or '|' and
  does not glue the marker to the answer text.

If the landmarks are missing, a fixed fallback width is used and the page's
markers are flagged ``LANDMARKS_NOT_FOUND`` by the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from qmd import geometry
from qmd.config import InkConfig, RegionConfig, mm_to_px
from qmd.ink import dark_ink
from qmd.models import BBox
from qmd.page_layout import WorkLayout


@dataclass
class MarginRegion:
    """The prepared margin region. All coordinates are REGION pixels unless noted."""

    gray: np.ndarray                 # student ink only; rule and valuer ink whitened
    ink: np.ndarray                  # 0/1 mask of dark student ink, printed lines removed
    offset_x: int                    # region origin in WORK coordinates
    offset_y: int
    dpi: float
    rule_x: Optional[int]            # margin rule x in region coords (None if not found)
    line_ys: list[int] = field(default_factory=list)  # ruled lines in region coords
    line_pitch_px: Optional[float] = None
    landmarks_found: bool = False
    valuer_boxes_work: list[BBox] = field(default_factory=list)

    @property
    def region_to_work(self) -> geometry.Affine:
        return geometry.translation(self.offset_x, self.offset_y)

    def band_index(self, y: float) -> Optional[int]:
        """Line band containing region-y (see WorkLayout.band_index)."""
        if not self.line_ys:
            return None
        return WorkLayout(ruled_line_ys=self.line_ys).band_index(y)


def build_margin_region(
    student_gray_work: np.ndarray,
    valuer_mask_work: np.ndarray,
    layout: WorkLayout,
    dpi: float,
    region_cfg: RegionConfig,
    ink_cfg: InkConfig,
) -> MarginRegion:
    height, width = student_gray_work.shape
    found = layout.found
    if layout.margin_rule_x is not None:
        right = layout.margin_rule_x + mm_to_px(region_cfg.spill_mm, dpi)
    else:
        right = mm_to_px(region_cfg.fallback_region_width_mm, dpi)
    right = int(min(max(right, 10), width))
    top = 0
    if layout.header_y is not None:
        top = layout.header_y + mm_to_px(region_cfg.top_offset_mm, dpi)
    elif layout.ruled_line_ys:
        top = max(0, layout.ruled_line_ys[0] - int(layout.line_pitch_px or 0))
    top = int(min(max(top, 0), height - 10))

    gray = student_gray_work[top:height, 0:right].copy()
    rule_x = None
    if layout.margin_rule_x is not None:
        rule_x = layout.margin_rule_x
        half = max(1, mm_to_px(region_cfg.rule_mask_half_width_mm, dpi))
        gray[:, max(0, rule_x - half): rule_x + half + 1] = 255

    ink = dark_ink(gray, ink_cfg.dark_ink_threshold)
    # Remove the horizontal ruled lines inside the region (runs longer than ~3 mm).
    min_run = max(8, mm_to_px(3.0, dpi))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (min_run, 1)))
    ink = ink & (1 - lines)
    # Also whiten those line pixels in the OCR copy; recognizers read them as '_' or '-'.
    gray[lines > 0] = 255

    valuer_boxes = _valuer_boxes(valuer_mask_work[top:height, 0:right], dpi, top)
    return MarginRegion(
        gray=gray,
        ink=ink,
        offset_x=0,
        offset_y=top,
        dpi=dpi,
        rule_x=rule_x,
        line_ys=[y - top for y in layout.ruled_line_ys if y >= top - (layout.line_pitch_px or 0)],
        line_pitch_px=layout.line_pitch_px,
        landmarks_found=found,
        valuer_boxes_work=valuer_boxes,
    )


def _valuer_boxes(mask: np.ndarray, dpi: float, offset_y: int) -> list[BBox]:
    """Boxes of suppressed valuer-ink marks (kept for audit, TD §6.3)."""
    if not mask.any():
        return []
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    min_area = (dpi / 25.4) ** 2 * 2.0  # ~2 mm^2
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_area:
            boxes.append(BBox(x=int(x), y=int(y + offset_y), width=int(w), height=int(h)))
    return boxes
