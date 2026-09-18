"""Writing results and diagnostic artefacts to the script's output folder.

Layout of ``<output_root>/<script_id>/``::

    script_result.json        all page + marker results, script flags (the main output)
    run_manifest.json         versions, configuration snapshot, timings
    processing.log            full log for this script
    pages/page_003.jpg        canonical page image (original JPEG bytes when possible)
    debug/page_003_overlay.jpg  page with margin rule, ruled lines, markers and starts drawn
    debug/page_003_margin.png   the cleaned margin region given to the detector
    crops/page_003_c01.png      the exact crop given to the recognizer

JSON files are written atomically (temp file + rename) so that an interrupted
run never leaves a half-written result (FR-15, FR-16).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from qmd.models import MarkerResult, MarkerStatus, PageResult

STATUS_COLOURS = {  # BGR
    MarkerStatus.ACCEPTED: (0, 160, 0),
    MarkerStatus.REVIEW_REQUIRED: (0, 140, 255),
    MarkerStatus.UNRESOLVED: (0, 0, 255),
    MarkerStatus.DISCARDED: (160, 160, 160),
    MarkerStatus.REJECTED: (120, 120, 120),
}


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def write_image(path: Path, image: np.ndarray, jpeg_quality: int = 85) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality] if path.suffix.lower() in (".jpg", ".jpeg") else []
    if not cv2.imwrite(str(path), image, params):
        raise OSError(f"Could not write image {path}")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def draw_overlay(canonical: np.ndarray, page: PageResult, scale: float = 0.5) -> np.ndarray:
    """Diagnostic picture: landmarks, marker boxes (colour = status) and question starts."""
    img = canonical.copy()
    layout = page.layout
    if layout is not None:
        if layout.margin_rule_x is not None:
            cv2.line(img, (layout.margin_rule_x, 0), (layout.margin_rule_x, img.shape[0]), (255, 0, 255), 2)
        for y in layout.ruled_line_ys:
            cv2.line(img, (0, y), (img.shape[1], y), (255, 200, 0), 1)
    for box in page.suppressed_valuer_ink:
        cv2.rectangle(img, (box.x, box.y), (box.x2, box.y2), (200, 0, 200), 1)
    for m in page.markers:
        _draw_marker(img, m)
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def _draw_marker(img: np.ndarray, m: MarkerResult) -> None:
    colour = STATUS_COLOURS.get(m.status, (0, 0, 0))
    thickness = 1 if m.status == MarkerStatus.DISCARDED else 3
    cv2.rectangle(img, (m.marker_x, m.marker_y), (m.marker_x + m.marker_width, m.marker_y + m.marker_height),
                  colour, thickness)
    if m.status == MarkerStatus.DISCARDED:
        return
    label = f"{m.canonical_question_id or '?'} [{m.status.value[:3]}]"
    cv2.putText(img, label, (m.marker_x, max(20, m.marker_y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, colour, 2)
    if m.question_start_x is not None and m.question_start_y is not None:
        cv2.circle(img, (m.question_start_x, m.question_start_y), 12, colour, 3)
        cv2.line(img, (m.marker_x + m.marker_width, m.marker_y + m.marker_height // 2),
                 (m.question_start_x, m.question_start_y), colour, 2)
