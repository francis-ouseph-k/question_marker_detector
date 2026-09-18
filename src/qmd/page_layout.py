"""Page geometry: margin rule, header rule, ruled lines, skew (TD §6.1).

All detection here is plain image processing (morphology + projections); no ML.

Terminology
-----------
* margin rule  - the printed vertical line separating the left margin from the
                 answer area. Its x position differs between odd and even pages
                 and drifts from page to page, so it is found on EVERY page.
* header rule  - the printed horizontal line under the page header/logo.
* ruled lines  - the horizontal writing lines (~9 mm apart). Consecutive lines
                 define a "line band"; question start y snaps to these.

The detector works on the "work image": the canonical image, optionally
deskewed. ``WorkLayout`` holds coordinates in the work image;
``to_canonical_layout`` converts them for reporting.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from qmd import geometry
from qmd.config import LayoutConfig, mm_to_px
from qmd.models import PageLayout

log = logging.getLogger(__name__)


@dataclass
class WorkLayout:
    """Landmarks in WORK-image coordinates (used internally by the pipeline)."""

    margin_rule_x: Optional[int] = None
    header_y: Optional[int] = None
    ruled_line_ys: list[int] = field(default_factory=list)
    line_pitch_px: Optional[float] = None
    skew_deg: float = 0.0

    @property
    def found(self) -> bool:
        return self.margin_rule_x is not None and len(self.ruled_line_ys) >= 2

    def band_index(self, y: float) -> Optional[int]:
        """Index L of the line band [ruled_line_ys[L], ruled_line_ys[L+1]) containing ``y``.

        Returns -1 above the first line, len-1 below the last line, None if no lines.
        """
        ys = self.ruled_line_ys
        if not ys:
            return None
        if y < ys[0]:
            return -1
        for i in range(len(ys) - 1):
            if ys[i] <= y < ys[i + 1]:
                return i
        return len(ys) - 1


@dataclass
class DeskewResult:
    image: np.ndarray                 # work image (BGR)
    canonical_to_work: geometry.Affine
    skew_deg: float
    deskewed: bool


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def prepare_work_image(image: np.ndarray, dpi: float, cfg: LayoutConfig) -> DeskewResult:
    """Measure skew from the margin rule and deskew the page if needed."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = _binarize(gray)
    rule_x = _find_margin_rule_x(binary, dpi, cfg)
    skew = _measure_skew(binary, rule_x) if rule_x is not None else 0.0
    if abs(skew) < cfg.deskew_threshold_deg or abs(skew) > cfg.max_skew_deg:
        if abs(skew) > cfg.max_skew_deg:
            log.warning("measured skew %.2f deg exceeds max_skew_deg; not deskewing", skew)
        return DeskewResult(image=image, canonical_to_work=geometry.identity(), skew_deg=skew, deskewed=False)

    h, w = gray.shape
    # Rotating by -skew makes the (x = a*y + b) rule vertical. See tests/test_page_layout.py.
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -skew, 1.0)
    rotated = cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255)
    )
    return DeskewResult(image=rotated, canonical_to_work=matrix.astype(np.float64), skew_deg=skew, deskewed=True)


def detect_layout(work_image: np.ndarray, dpi: float, cfg: LayoutConfig) -> WorkLayout:
    """Find margin rule, header rule and ruled lines on the (deskewed) work image."""
    gray = cv2.cvtColor(work_image, cv2.COLOR_BGR2GRAY)
    binary = _binarize(gray)
    layout = WorkLayout()
    layout.margin_rule_x = _find_margin_rule_x(binary, dpi, cfg)
    layout.header_y = _find_header_y(binary, cfg)
    if layout.margin_rule_x is None:
        log.debug("margin rule not found")
        return layout
    candidates = _find_ruled_line_candidates(binary, layout.margin_rule_x)
    pitch = _estimate_pitch(candidates, dpi, cfg)
    if pitch is None:
        log.debug("ruled-line pitch not found (%d candidate rows)", len(candidates))
        return layout
    layout.line_pitch_px = pitch
    grid = _complete_grid(candidates, pitch, layout.header_y, gray.shape[0], cfg.line_snap_tolerance_fraction)
    if len(grid) >= cfg.min_ruled_lines:
        layout.ruled_line_ys = grid
    return layout


def to_canonical_layout(
    work: WorkLayout, work_to_canonical: geometry.Affine, parity: str, skew_deg: float, deskewed: bool
) -> PageLayout:
    """Express landmarks in canonical coordinates for reporting."""
    rule_x = None
    lines: list[int] = []
    header = None
    ref_x = work.margin_rule_x if work.margin_rule_x is not None else 0
    if work.margin_rule_x is not None:
        mid_y = work.ruled_line_ys[len(work.ruled_line_ys) // 2] if work.ruled_line_ys else 0
        rule_x = geometry.point_to_canonical(work_to_canonical, work.margin_rule_x, mid_y).x
    for y in work.ruled_line_ys:
        lines.append(geometry.point_to_canonical(work_to_canonical, ref_x, y).y)
    if work.header_y is not None:
        header = geometry.point_to_canonical(work_to_canonical, ref_x, work.header_y).y
    return PageLayout(
        found=work.found,
        margin_rule_x=rule_x,
        header_y=header,
        ruled_line_ys=lines,
        line_pitch_px=round(work.line_pitch_px, 2) if work.line_pitch_px else None,
        parity=parity,
        skew_deg=round(skew_deg, 3),
        deskewed=deskewed,
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Adaptive threshold: printed rules are faint grey, so a global threshold misses them."""
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)


def _find_margin_rule_x(binary: np.ndarray, dpi: float, cfg: LayoutConfig) -> Optional[int]:
    h, w = binary.shape
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 10))))
    x_max = int(w * cfg.margin_search_max_fraction)
    edge = max(1, mm_to_px(cfg.edge_ignore_mm, dpi))
    cols = vertical[:, :x_max].sum(axis=0).astype(np.float64) / 255.0
    cols[:edge] = 0  # the scanner's dark page edge is not the margin rule
    if cols.size == 0:
        return None
    # A slightly skewed rule spreads over several columns; sum a small window so its
    # full length is counted. The window is narrower than the rule's thickness x 3.
    width = max(3, mm_to_px(1.0, dpi))
    smoothed = np.convolve(cols, np.ones(width), mode="same")
    best = int(np.argmax(smoothed))
    if smoothed[best] < cfg.margin_rule_min_length_fraction * h:
        return None
    # Weighted centre of the line within the window.
    lo, hi = max(0, best - width), min(x_max, best + width + 1)
    window = cols[lo:hi]
    if window.sum() <= 0:
        return best
    return int(round(float((np.arange(lo, hi) * window).sum() / window.sum())))


def _measure_skew(binary: np.ndarray, rule_x: int) -> float:
    """Angle (degrees) of the margin rule from vertical, from a line fit x = a*y + b."""
    h, _ = binary.shape
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 20))))
    band = vertical[:, max(0, rule_x - 25): rule_x + 26]
    ys, xs = np.nonzero(band)
    if len(ys) < h * 0.2:
        return 0.0
    slope, _ = np.polyfit(ys.astype(np.float64), xs.astype(np.float64), 1)
    return float(math.degrees(math.atan(slope)))


def _find_header_y(binary: np.ndarray, cfg: LayoutConfig) -> Optional[int]:
    h, w = binary.shape
    top = binary[: int(h * cfg.header_search_max_fraction)]
    horizontal = cv2.morphologyEx(top, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 3), 1)))
    rows = horizontal.sum(axis=1) / 255.0
    ys = np.nonzero(rows >= 0.5 * w)[0]
    ys = ys[ys > 3]  # ignore the scanner's top edge
    if ys.size == 0:
        return None
    return int(_cluster_rows(ys)[0])


def _find_ruled_line_candidates(binary: np.ndarray, rule_x: int) -> list[int]:
    h, w = binary.shape
    x0, x1 = min(w - 1, rule_x + 10), max(rule_x + 11, w - 10)
    area = binary[:, x0:x1]
    width = area.shape[1]
    if width < 50:
        return []
    # A short kernel (15% of width) survives handwriting crossing the line.
    horizontal = cv2.morphologyEx(area, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(width * 0.15)), 1)))
    rows = horizontal.sum(axis=1) / 255.0
    ys = np.nonzero(rows >= 0.25 * width)[0]
    ys = ys[(ys > 3) & (ys < h - 3)]
    return _cluster_rows(ys)


def _cluster_rows(ys: np.ndarray, gap: int = 3) -> list[int]:
    """Group consecutive row indices and return each group's centre."""
    groups: list[list[int]] = []
    for y in ys.tolist():
        if groups and y - groups[-1][-1] <= gap:
            groups[-1].append(y)
        else:
            groups.append([y])
    return [int(round(sum(g) / len(g))) for g in groups]


def _estimate_pitch(candidates: list[int], dpi: float, cfg: LayoutConfig) -> Optional[float]:
    lo, hi = mm_to_px(cfg.line_pitch_min_mm, dpi), mm_to_px(cfg.line_pitch_max_mm, dpi)
    diffs = [b - a for a, b in zip(candidates, candidates[1:]) if lo <= b - a <= hi]
    if len(diffs) < 3:
        return None
    return float(np.median(diffs))


def _complete_grid(candidates: list[int], pitch: float, header_y: Optional[int], height: int,
                   snap_fraction: float = 0.25) -> list[int]:
    """Build the full, regular list of ruled lines.

    Detected candidates are incomplete where handwriting interrupts a line. We
    walk down the page one pitch at a time and snap each predicted line to the
    nearest detected candidate (within a quarter pitch), so small drifts are
    followed but missing lines are still filled in.
    """
    tolerance = pitch * snap_fraction
    start_candidates = [c for c in candidates if header_y is None or c >= header_y - tolerance]
    if header_y is not None:
        y = float(header_y)
    elif start_candidates:
        y = float(start_candidates[0])
    else:
        return []
    # Stop at the last printed line we actually saw (or the page bottom).
    last_line = min(float(max(candidates)) + tolerance, height - pitch * 0.3) if candidates else height - pitch * 0.3
    grid = [int(round(y))]
    while y + pitch <= last_line:
        predicted = y + pitch
        nearest = min(candidates, key=lambda c: abs(c - predicted)) if candidates else None
        y = float(nearest) if nearest is not None and abs(nearest - predicted) <= tolerance else predicted
        grid.append(int(round(y)))
    return grid
