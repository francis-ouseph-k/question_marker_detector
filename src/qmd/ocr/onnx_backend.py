"""ONNX Runtime backend for PP-OCR family models (via RapidOCR).

Install with ``pip install -e ".[onnx]"``.

Why this backend exists
-----------------------
TD §4 requires the OCR engine to be replaceable. This backend runs PP-OCR
models exported to ONNX on ``onnxruntime`` (small, CPU-friendly, no Paddle
runtime). It is also the only backend that works fully offline out of the box:

* ``ocr.onnx_det_model_path`` / ``ocr.onnx_rec_model_path`` (+ ``onnx_rec_keys_path``
  if the model has no embedded character dictionary) select explicit model files,
  e.g. PP-OCRv5 mobile exported to ONNX.
* If they are empty, the models BUNDLED WITH THE rapidocr WHEEL are used. For
  rapidocr 3.9.2 these are PP-OCR "v6 small" models - NOT the PP-OCRv5 Mobile
  baseline of TD §3.1. The model identity is recorded in every result, so
  results from different models are never confused (NFR-06).
"""

from __future__ import annotations

import hashlib
import logging
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

import numpy as np

from qmd.config import OcrConfig
from qmd.errors import OcrEngineError
from qmd.ocr.base import EngineInfo, OcrEngine, TextRegion, TextResult

log = logging.getLogger(__name__)


class OnnxRecognizer:
    def __init__(self, engine: Any) -> None:
        from rapidocr.ch_ppocr_rec import TextRecInput

        self._engine = engine
        self._input_cls = TextRecInput

    def recognize(self, images: list[np.ndarray]) -> list[TextResult]:
        if not images:
            return []
        out = self._engine.text_rec(self._input_cls(img=list(images)))
        return [
            TextResult(text=str(text), recognition_confidence=float(score))
            for text, score in zip(out.txts, out.scores)
        ]


class OnnxDetector:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def detect(self, image: np.ndarray) -> list[TextRegion]:
        out = self._engine.text_det(image)
        if out.boxes is None:
            return []
        return [
            TextRegion(polygon=np.asarray(box, dtype=float).tolist(), detection_confidence=float(score))
            for box, score in zip(out.boxes, out.scores)
        ]


def _file_id(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    return f"{Path(path).name}@sha256:{digest}"


def _bundled_model_id(engine: Any, task: str) -> str:
    """Identify a model bundled inside the rapidocr wheel by file name AND hash (NFR-06).

    The version string alone is not enough: a rapidocr upgrade can ship a
    different model under the same configuration. rapidocr 3.x stores bundled
    models as ``<ocr_version>_<task>_<model_type>.onnx`` in its models folder.
    """
    cfg = engine.text_rec.cfg
    version, model_type = cfg["ocr_version"].value, cfg["model_type"].value
    root = Path(cfg["model_root_dir"])
    candidate = root / f"{version}_{task}_{model_type}.onnx"
    if not candidate.exists():
        matches = sorted(root.glob(f"*_{task}_*.onnx"))
        if len(matches) != 1:
            log.warning("Could not identify the bundled %s model file in %s; recording name only", task, root)
            return f"bundled:{version}_{task}_{model_type}"
        candidate = matches[0]
    return f"bundled:{_file_id(candidate)}"


def create_onnx_engine(cfg: OcrConfig, need_detector: bool) -> OcrEngine:
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise OcrEngineError(
            "rapidocr/onnxruntime not installed. Install with: pip install -e \".[onnx]\""
        ) from exc

    threads = cfg.threads_per_worker
    params: dict[str, Any] = {
        "Global.log_level": "error",
        "Global.use_cls": False,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Det.limit_type": "max",       # never upscale small tiles (TD §7.1)
        "Det.limit_side_len": 960,
        "Det.box_thresh": cfg.det_box_thresh,
        "Det.thresh": cfg.det_thresh,
        "Det.unclip_ratio": cfg.det_unclip_ratio,
        "Rec.rec_batch_num": cfg.rec_batch_size,
    }
    for key, value in (
        ("Det.model_path", cfg.onnx_det_model_path),
        ("Rec.model_path", cfg.onnx_rec_model_path),
        ("Rec.rec_keys_path", cfg.onnx_rec_keys_path),
    ):
        if value:
            if not Path(value).exists():
                raise OcrEngineError(f"{key} does not exist: {value}")
            params[key] = str(value)
    try:
        engine = RapidOCR(params=params)
    except Exception as exc:
        raise OcrEngineError(f"Could not create ONNX OCR engine: {exc}") from exc

    version = metadata.version("rapidocr")
    ort_version = metadata.version("onnxruntime")
    rec_id = _file_id(cfg.onnx_rec_model_path)
    det_id = _file_id(cfg.onnx_det_model_path)
    if rec_id is None:
        rec_id = _bundled_model_id(engine, "rec")
    if det_id is None and need_detector:
        det_id = _bundled_model_id(engine, "det")
    model = f"{det_id}+{rec_id}" if need_detector else rec_id
    info = EngineInfo(
        ocr_engine=f"rapidocr-{version}/onnxruntime-{ort_version}",
        ocr_model=model,
        ocr_model_version=f"rapidocr=={version}",
    )
    if "bundled:" in model:
        log.warning(
            "ONNX backend is using the models bundled with rapidocr (%s), not PP-OCRv5 Mobile. "
            "Set ocr.onnx_*_model_path to use a specific model.", model,
        )
    return OcrEngine(
        recognizer=OnnxRecognizer(engine),
        detector=OnnxDetector(engine) if need_detector else None,
        info=info,
    )
