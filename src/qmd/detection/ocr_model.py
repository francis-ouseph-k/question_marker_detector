"""OCR-model text detector on overlapping tiles (TD §7.1).

The margin region is tall and narrow (about 1:13). Feeding it whole to a text
detector either shrinks the markers or (with a 'min side' resize policy)
enlarges the strip enormously. We therefore cut it into tiles of a few writing
lines, add white padding on the right so the tile is not extremely narrow, run
the detector, and remove duplicates found in the overlaps.
"""

from __future__ import annotations

import logging

import cv2

from qmd.config import DetectionConfig, mm_to_px
from qmd.detection.common import RegionCandidate, merge_boxes_by_line
from qmd.geometry import bbox_of_polygon, iou
from qmd.ocr.base import TextDetectionModel
from qmd.region import MarginRegion

log = logging.getLogger(__name__)


class OcrModelDetector:
    name = "ocr_model"

    def __init__(self, model: TextDetectionModel, cfg: DetectionConfig) -> None:
        self.model = model
        self.cfg = cfg

    def detect(self, region: MarginRegion) -> list[RegionCandidate]:
        height, width = region.gray.shape
        pitch = region.line_pitch_px or mm_to_px(9.4, region.dpi)
        tile_h = int(pitch * self.cfg.tile_height_lines)
        step = max(1, tile_h - int(pitch * self.cfg.tile_overlap_lines))
        pad_right = max(0, width)  # makes the tile roughly square-ish for the detector

        found: list[RegionCandidate] = []
        for y0 in range(0, height, step):
            tile = region.gray[y0: y0 + tile_h]
            if tile.shape[0] < 10:
                break
            tile_bgr = cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)
            tile_bgr = cv2.copyMakeBorder(tile_bgr, 0, 0, 0, pad_right, cv2.BORDER_CONSTANT, value=(255, 255, 255))
            for text_region in self.model.detect(tile_bgr):
                polygon = [[float(x), float(y + y0)] for x, y in text_region.polygon]
                box = bbox_of_polygon(polygon)
                if box.x >= width:
                    continue  # entirely in the padding
                found.append(RegionCandidate(box, text_region.detection_confidence, polygon=polygon))
            if y0 + tile_h >= height:
                break

        # Non-maximum suppression across tile overlaps: keep the higher score.
        found.sort(key=lambda c: c.detection_confidence, reverse=True)
        kept: list[RegionCandidate] = []
        for cand in found:
            if all(iou(cand.box, k.box) < self.cfg.nms_iou for k in kept):
                kept.append(cand)
        # Keep only boxes that START in the margin. The text detector "unclips" (grows)
        # its boxes, so a line of answer text can appear to start ~1 mm left of the
        # rule; require a small clearance. Very tall boxes are vertical artefacts
        # along the region's cut edge, not markers.
        rule_x = region.rule_x
        clearance = mm_to_px(1.0, region.dpi)
        max_h = int(pitch * 1.8)
        margin = [
            c for c in kept
            if (rule_x is None or c.box.x < rule_x - clearance) and c.box.height <= max_h
        ]
        merged = merge_boxes_by_line(margin, region, mm_to_px(self.cfg.merge_gap_mm, region.dpi))
        log.debug("ocr_model detector: %d raw -> %d kept -> %d candidates", len(found), len(margin), len(merged))
        return merged
