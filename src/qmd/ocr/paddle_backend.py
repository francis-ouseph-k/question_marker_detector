"""PaddleOCR 3.x backend: PP-OCRv5 Mobile by default (TD §3.1).

Install with ``pip install -e ".[paddle]"``.

Models
------
* By default PaddleOCR downloads ``PP-OCRv5_mobile_det`` / ``PP-OCRv5_mobile_rec``
  on first use (needs access to HuggingFace, BOS or ModelScope).
* For offline or controlled deployments, download the model folders once and set
  ``ocr.det_model_dir`` / ``ocr.rec_model_dir``; the model bundle is then fully
  versioned by you (TD §28).
"""

from __future__ import annotations

import logging
import os
from importlib import metadata

import numpy as np

from qmd.config import OcrConfig
from qmd.errors import OcrEngineError
from qmd.ocr.base import EngineInfo, OcrEngine, TextRegion, TextResult

log = logging.getLogger(__name__)


class PaddleRecognizer:
    def __init__(self, cfg: OcrConfig) -> None:
        from paddleocr import TextRecognition  # imported lazily: optional dependency

        kwargs = _common_kwargs(cfg)
        if cfg.rec_model_dir:
            kwargs["model_dir"] = str(cfg.rec_model_dir)
        self._model = TextRecognition(model_name=cfg.rec_model_name, **kwargs)
        self._batch = cfg.rec_batch_size

    def recognize(self, images: list[np.ndarray]) -> list[TextResult]:
        if not images:
            return []
        results: list[TextResult] = []
        for res in self._model.predict(input=images, batch_size=self._batch):
            results.append(TextResult(text=str(res["rec_text"]), recognition_confidence=float(res["rec_score"])))
        return results


class PaddleDetector:
    def __init__(self, cfg: OcrConfig) -> None:
        from paddleocr import TextDetection

        kwargs = _common_kwargs(cfg)
        if cfg.det_model_dir:
            kwargs["model_dir"] = str(cfg.det_model_dir)
        self._model = TextDetection(
            model_name=cfg.det_model_name,
            thresh=cfg.det_thresh,
            box_thresh=cfg.det_box_thresh,
            unclip_ratio=cfg.det_unclip_ratio,
            # Tiles are already small; never upscale them (TD §7.1).
            limit_type="max",
            limit_side_len=960,
            **kwargs,
        )

    def detect(self, image: np.ndarray) -> list[TextRegion]:
        regions: list[TextRegion] = []
        for res in self._model.predict(input=image):
            for poly, score in zip(res["dt_polys"], res["dt_scores"]):
                regions.append(TextRegion(polygon=np.asarray(poly, dtype=float).tolist(), detection_confidence=float(score)))
        return regions


def _common_kwargs(cfg: OcrConfig) -> dict:
    return {"device": "cpu", "enable_mkldnn": cfg.enable_mkldnn, "cpu_threads": cfg.threads_per_worker}


def create_paddle_engine(cfg: OcrConfig, need_detector: bool) -> OcrEngine:
    if cfg.det_model_dir or cfg.rec_model_dir:
        # Local models: skip PaddleOCR's slow connectivity check to model hosts.
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        recognizer = PaddleRecognizer(cfg)
        detector = PaddleDetector(cfg) if need_detector else None
    except ImportError as exc:
        raise OcrEngineError(
            "PaddleOCR is not installed. Install with: pip install -e \".[paddle]\"  "
            "(or set QMD_OCR__BACKEND=onnx)"
        ) from exc
    except Exception as exc:
        raise OcrEngineError(
            f"Could not create PaddleOCR models ({cfg.det_model_name}, {cfg.rec_model_name}): {exc}. "
            "If model download is blocked, download the models and set ocr.det_model_dir / ocr.rec_model_dir."
        ) from exc
    try:
        version = metadata.version("paddleocr")
    except metadata.PackageNotFoundError:  # pragma: no cover
        version = "unknown"
    model = f"{cfg.det_model_name}+{cfg.rec_model_name}" if need_detector else cfg.rec_model_name
    model_version = f"paddleocr=={version}"
    if cfg.rec_model_dir:
        model_version += f";rec_dir={cfg.rec_model_dir}"
    info = EngineInfo(ocr_engine=f"paddleocr-{version}", ocr_model=model, ocr_model_version=model_version)
    log.info("OCR engine ready: %s / %s", info.ocr_engine, info.ocr_model)
    return OcrEngine(recognizer=recognizer, detector=detector, info=info)
