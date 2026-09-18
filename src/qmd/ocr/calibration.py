"""Confidence calibration (TD §19.2, Requirements FR-12).

Raw recognizer scores are not probabilities. After a labelled benchmark run,
fit a monotone mapping raw -> P(correct) (e.g. isotonic regression) and save it
as JSON:

    {"engine_model": "PP-OCRv5_mobile_rec", "points": [[0.0, 0.0], [0.8, 0.55], [0.95, 0.9], [1.0, 0.98]]}

The mapping is applied by piecewise-linear interpolation. Without a file the
raw score is used unchanged and results are marked as uncalibrated in the run
manifest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

from qmd.errors import ConfigurationError


class Calibrator:
    def __init__(self, points: Optional[list[tuple[float, float]]] = None, source: str = "identity") -> None:
        self.source = source
        self._x: Optional[np.ndarray] = None
        self._y: Optional[np.ndarray] = None
        if points:
            pts = sorted(points)
            self._x = np.array([p[0] for p in pts], dtype=float)
            self._y = np.array([p[1] for p in pts], dtype=float)
            if np.any(np.diff(self._y) < 0):
                raise ConfigurationError("Calibration points must be monotonically non-decreasing")

    @property
    def is_identity(self) -> bool:
        return self._x is None

    def __call__(self, raw: float) -> float:
        if self._x is None or self._y is None:
            return float(raw)
        return float(np.interp(raw, self._x, self._y))

    @classmethod
    def from_file(cls, path: Optional[Path]) -> Calibrator:
        if path is None:
            return cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            points = [(float(a), float(b)) for a, b in data["points"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigurationError(f"Invalid calibration file {path}: {exc}") from exc
        if len(points) < 2:
            # An empty/one-point file would silently mean "uncalibrated"; almost
            # certainly a mistake, so fail loudly at start-up instead.
            raise ConfigurationError(f"Calibration file {path} needs at least 2 points, got {len(points)}")
        return cls(points, source=str(path))
