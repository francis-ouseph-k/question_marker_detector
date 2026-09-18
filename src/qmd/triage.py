"""Page content class (TD §17.2, Requirements FR-09).

Counts connected components of dark STUDENT ink after removing:
* printed rules (morphological line removal),
* the light watermark and faint show-through (dark-ink threshold),
* valuer ink (the caller passes a gray image with valuer ink already whitened).

The class is informational and may be used to skip expensive work, but it
never stops marker detection on its own (TD §17.2).
"""

from __future__ import annotations

import cv2
import numpy as np

from qmd.config import TriageConfig
from qmd.ink import dark_ink
from qmd.models import ContentClass


def remove_printed_lines(ink: np.ndarray, min_line_px: int) -> np.ndarray:
    """Remove long horizontal and vertical runs (printed rules) from a 0/1 ink mask."""
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (min_line_px, 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_line_px)))
    return ink & (1 - np.maximum(horiz, vert))


def classify_content(student_gray: np.ndarray, dpi: float, cfg: TriageConfig, dark_threshold: int,
                     top_y: int = 0) -> tuple[ContentClass, int]:
    """Return (content_class, component_count) for the area below ``top_y``."""
    area = student_gray[max(0, top_y):]
    small = cv2.resize(area, None, fx=cfg.scale, fy=cfg.scale, interpolation=cv2.INTER_AREA)
    eff_dpi = dpi * cfg.scale
    ink = dark_ink(small, dark_threshold)
    ink = remove_printed_lines(ink, max(15, int(eff_dpi * 0.6)))  # runs longer than ~15 mm
    # Scanner edge shadow: ignore a thin border.
    border = max(2, int(eff_dpi * 0.04))
    ink[:, :border] = 0
    ink[:, -border:] = 0
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    min_area = cfg.min_component_area_mm2 * (eff_dpi / 25.4) ** 2
    min_side = max(2, int(round(0.5 * eff_dpi / 25.4)))  # strokes are thicker than line-removal residue
    count = 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area and min(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= min_side:
            count += 1
    if count <= cfg.blank_max_components:
        return ContentClass.BLANK, count
    if count <= cfg.sparse_max_components:
        return ContentClass.SPARSE, count
    return ContentClass.NORMAL, count
