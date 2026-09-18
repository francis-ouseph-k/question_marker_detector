"""Margin connected-component detector (TD §3.2 option B, "MarginComponentDetector").

Idea: after valuer ink, printed lines and the margin rule are removed, every
remaining dark blob in the margin is student writing. Question markers are the
blobs that START left of the margin rule; blobs that start inside the answer
area (numbered points such as "(2)" or a circled "②") are ignored.
Neighbouring blobs on the same writing line are merged into one candidate box.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from qmd.config import DetectionConfig, mm_to_px
from qmd.detection.common import RegionCandidate, merge_boxes_by_line
from qmd.models import BBox
from qmd.region import MarginRegion

log = logging.getLogger(__name__)


class MarginComponentDetector:
    name = "margin_components"

    def __init__(self, cfg: DetectionConfig) -> None:
        self.cfg = cfg

    def detect(self, region: MarginRegion) -> list[RegionCandidate]:
        dpi = region.dpi
        px_per_mm2 = (dpi / 25.4) ** 2
        min_area = self.cfg.min_component_area_mm2 * px_per_mm2
        max_h = mm_to_px(self.cfg.max_component_height_mm, dpi)
        thin = max(2, mm_to_px(0.4, dpi))
        tall = mm_to_px(3.0, dpi)

        n, _, stats, _ = cv2.connectedComponentsWithStats(region.ink.astype(np.uint8), connectivity=8)
        blobs: list[RegionCandidate] = []
        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area < min_area or h > max_h:
                continue
            if w <= thin and h >= tall:
                continue  # scanner edge shadow or remnant of a printed vertical line
            if region.rule_x is not None and x >= region.rule_x:
                continue  # starts inside the answer area -> not a margin marker
            blobs.append(RegionCandidate(BBox(x=x, y=y, width=w, height=h), detection_confidence=1.0))

        merged = merge_boxes_by_line(blobs, region, mm_to_px(self.cfg.merge_gap_mm, dpi))
        min_h = mm_to_px(self.cfg.min_candidate_height_mm, dpi)
        edge_w = mm_to_px(1.5, dpi)
        result = [
            c for c in merged
            if c.box.height >= min_h and c.box.width >= 2
            # narrow blob glued to the page edge = scanner edge/corner shadow
            and not (c.box.x <= 1 and c.box.width <= edge_w)
        ]
        log.debug("components: %d blobs -> %d candidates", len(blobs), len(result))
        return result
