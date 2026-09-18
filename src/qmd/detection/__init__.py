"""Candidate detection in the margin region (TD §7.1).

Two interchangeable methods (config ``detection.method``):

* ``margin_components`` - group connected ink blobs that start in the margin
  (TD §3.2 option B). Default; see README "Assumptions" A-4.
* ``ocr_model``         - the OCR engine's text detector run on overlapping tiles.
"""

from __future__ import annotations

from qmd.config import DetectionConfig
from qmd.detection.common import RegionCandidate, merge_boxes_by_line
from qmd.detection.components import MarginComponentDetector
from qmd.detection.ocr_model import OcrModelDetector
from qmd.ocr.base import TextDetectionModel

__all__ = ["RegionCandidate", "merge_boxes_by_line", "MarginComponentDetector", "OcrModelDetector", "create_detector"]


def create_detector(cfg: DetectionConfig, text_detector: TextDetectionModel | None):
    if cfg.method == "ocr_model":
        if text_detector is None:
            raise ValueError("detection.method=ocr_model needs an OCR text detector")
        return OcrModelDetector(text_detector, cfg)
    return MarginComponentDetector(cfg)
