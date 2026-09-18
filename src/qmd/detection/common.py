"""Shared helpers for candidate detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from qmd.geometry import union_bbox
from qmd.models import BBox
from qmd.region import MarginRegion


@dataclass
class RegionCandidate:
    """A candidate marker in REGION coordinates.

    ``polygon`` is the text detector's own (possibly rotated) outline, kept so it
    can be reported as-is (TD §8). It is ``None`` for the component detector and
    for candidates merged from several fragments (a union has no single outline);
    the pipeline then reports the box as a 4-point polygon.
    """

    box: BBox
    detection_confidence: float
    line_index: Optional[int] = None
    polygon: Optional[list[list[float]]] = None


def _vertical_overlap(a: BBox, b: BBox) -> float:
    inter = min(a.y2, b.y2) - max(a.y, b.y)
    return inter / max(1, min(a.height, b.height))


def merge_boxes_by_line(candidates: list[RegionCandidate], region: MarginRegion, gap_px: int) -> list[RegionCandidate]:
    """Merge fragments on the same writing line that are horizontally close (TD §7.3).

    "Same line" = same ruled-line band (when ruled lines are known), otherwise
    at least 30% vertical overlap. Repeats until nothing changes.
    """
    items = [RegionCandidate(c.box, c.detection_confidence, region.band_index(c.box.center_y), c.polygon)
             for c in candidates]
    changed = True
    while changed:
        changed = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                same_line = (
                    a.line_index == b.line_index if a.line_index is not None and b.line_index is not None
                    else _vertical_overlap(a.box, b.box) >= 0.3
                )
                h_gap = max(a.box.x, b.box.x) - min(a.box.x2, b.box.x2)
                if same_line and h_gap <= gap_px:
                    merged_box = union_bbox([a.box, b.box])
                    items[i] = RegionCandidate(
                        merged_box,
                        min(a.detection_confidence, b.detection_confidence),
                        region.band_index(merged_box.center_y),
                        None,  # merged fragments: no single detector outline
                    )
                    del items[j]
                    changed = True
                    break
            if changed:
                break
    items.sort(key=lambda c: (c.box.y, c.box.x))
    return items
