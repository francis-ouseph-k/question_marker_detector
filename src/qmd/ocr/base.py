"""OCR engine abstraction (TD §4, Requirements NFR-08).

The rest of the system only talks to these two protocols, so the OCR library
can be replaced without touching grammar, resolution or reporting code.

* ``TextDetectionModel`` - finds text regions in an image (used only by the
  ``ocr_model`` candidate detector).
* ``Recognizer``          - reads the text in an image crop.

Both return plain dataclasses, never library-specific objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

import numpy as np


@dataclass
class TextRegion:
    """A detected text region. ``polygon`` is in the coordinates of the image passed in."""

    polygon: list[list[float]]
    detection_confidence: float


@dataclass
class TextResult:
    text: str
    recognition_confidence: float
    # Optional richer outputs (TD §4). Not provided by the current backends;
    # reserved for the CTC-probability scoring path (TD §12.3).
    top_k: list[tuple[str, float]] = field(default_factory=list)
    char_probabilities: Optional[np.ndarray] = None
    charset_id: Optional[str] = None


@dataclass(frozen=True)
class EngineInfo:
    """Provenance recorded with every result (TD §27)."""

    ocr_engine: str          # e.g. "paddleocr-3.3.3"
    ocr_model: str           # e.g. "PP-OCRv5_mobile_det+PP-OCRv5_mobile_rec"
    ocr_model_version: str   # package version and/or model file hash


@runtime_checkable
class TextDetectionModel(Protocol):
    def detect(self, image: np.ndarray) -> list[TextRegion]:
        """Detect text regions in a BGR image."""
        ...


@runtime_checkable
class Recognizer(Protocol):
    def recognize(self, images: list[np.ndarray]) -> list[TextResult]:
        """Recognise text in each BGR crop (batched). Output order = input order."""
        ...


@dataclass
class OcrEngine:
    """Bundle of the models created by a backend."""

    recognizer: Recognizer
    detector: Optional[TextDetectionModel]
    info: EngineInfo
