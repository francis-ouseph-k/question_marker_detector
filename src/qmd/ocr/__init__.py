"""OCR backends behind a common interface (TD §4)."""

from __future__ import annotations

from qmd.config import OcrConfig
from qmd.ocr.base import EngineInfo, OcrEngine, Recognizer, TextDetectionModel, TextRegion, TextResult

__all__ = [
    "EngineInfo",
    "OcrEngine",
    "Recognizer",
    "TextDetectionModel",
    "TextRegion",
    "TextResult",
    "create_engine",
]


def create_engine(cfg: OcrConfig, need_detector: bool) -> OcrEngine:
    """Create the configured OCR backend.

    ``need_detector`` is True only for the ``ocr_model`` detection method; the
    default ``margin_components`` method needs the recognizer only.
    """
    if cfg.backend == "paddle":
        from qmd.ocr.paddle_backend import create_paddle_engine

        return create_paddle_engine(cfg, need_detector)
    from qmd.ocr.onnx_backend import create_onnx_engine

    return create_onnx_engine(cfg, need_detector)
