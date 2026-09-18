"""PDF ingestion: produce the CANONICAL page image (TD §5.1, Requirements FR-10).

Rules
-----
* If a page contains exactly one embedded image that covers (almost) the whole
  page and is placed without rotation/shear, the canonical image is that
  embedded scan decoded at its NATIVE resolution (no resampling). This keeps
  the scanner's pixels, so coordinates match the original scan exactly.
* Otherwise the page is rendered at ``pdf.canonical_dpi``.
* The PDF page's ``/Rotate`` is applied so the canonical image is upright as it
  would be displayed.

The source PDF is opened read-only and never modified (Requirements §2.3).

Library: ``pypdfium2`` (Apache-2.0/BSD) was chosen over PyMuPDF (AGPL) to avoid
licence obligations in a production deployment.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from qmd.errors import PdfReadError

log = logging.getLogger(__name__)


@dataclass
class CanonicalPage:
    """The canonical image of one page, plus what we need to interpret it."""

    page_index: int            # 1-based
    image: np.ndarray          # BGR, uint8, shape (H, W, 3)
    dpi: float
    source: str                # "embedded" | "rendered"
    original_bytes: Optional[bytes] = None  # original JPEG bytes (for lossless saving)
    original_ext: Optional[str] = None      # ".jpg" when original_bytes is set

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_pages(pdf_path: Path) -> int:
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except pdfium.PdfiumError as exc:
        raise PdfReadError(f"Cannot open PDF {pdf_path}: {exc}") from exc
    try:
        return len(doc)
    finally:
        doc.close()


def load_page(pdf_path: Path, page_index: int, render_dpi: int, min_coverage: float = 0.9) -> CanonicalPage:
    """Load page ``page_index`` (1-based) as a canonical image."""
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except pdfium.PdfiumError as exc:
        raise PdfReadError(f"Cannot open PDF {pdf_path}: {exc}") from exc
    try:
        if not 1 <= page_index <= len(doc):
            raise PdfReadError(f"Page {page_index} out of range (1..{len(doc)}) in {pdf_path}")
        page = doc[page_index - 1]
        try:
            embedded = _try_embedded(page, page_index, min_coverage)
            if embedded is not None:
                return embedded
            return _render(page, page_index, render_dpi)
        finally:
            page.close()
    except PdfReadError:
        raise
    except Exception as exc:  # pdfium raises a variety of errors on damaged pages
        raise PdfReadError(f"Cannot read page {page_index} of {pdf_path}: {exc}") from exc
    finally:
        doc.close()


def _try_embedded(page: pdfium.PdfPage, page_index: int, min_coverage: float) -> Optional[CanonicalPage]:
    images = list(page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=2))
    if len(images) != 1:
        log.debug("page %d: %d embedded images -> render", page_index, len(images))
        return None
    img_obj = images[0]

    # Only accept an unrotated, unsheared placement (a, d > 0; b, c == 0).
    matrix = img_obj.get_matrix()
    a, b, c, d = matrix.a, matrix.b, matrix.c, matrix.d
    if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:
        log.debug("page %d: image placed with rotation/shear -> render", page_index)
        return None

    page_w, page_h = page.get_width(), page.get_height()   # un-rotated page box, points
    left, bottom, right, top = img_obj.get_bounds()
    coverage = ((right - left) * (top - bottom)) / max(page_w * page_h, 1e-6)
    if coverage < min_coverage:
        log.debug("page %d: image covers %.2f of page -> render", page_index, coverage)
        return None

    px_w, px_h = img_obj.get_px_size()
    original_bytes: Optional[bytes] = None
    if img_obj.get_filters() == ["DCTDecode"]:
        buf = io.BytesIO()
        img_obj.extract(buf)
        original_bytes = buf.getvalue()
        arr = np.frombuffer(original_bytes, dtype=np.uint8)
        # IGNORE_ORIENTATION: the PDF placement, not EXIF, defines orientation.
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            original_bytes = None
    else:
        image = None
    if image is None:
        bitmap = img_obj.get_bitmap(render=False)
        image = _bitmap_to_bgr(bitmap.to_numpy())

    if image.shape[1] != px_w or image.shape[0] != px_h:
        log.debug("page %d: decoded size %s differs from declared %sx%s", page_index, image.shape, px_w, px_h)

    dpi = image.shape[1] / ((right - left) / 72.0)
    rotation = page.get_rotation()
    if rotation:
        image = _rotate_clockwise(image, rotation)
        original_bytes = None  # stored bytes would no longer match the canonical image
    return CanonicalPage(
        page_index=page_index,
        image=np.ascontiguousarray(image),
        dpi=round(float(dpi), 2),
        source="embedded",
        original_bytes=original_bytes,
        original_ext=".jpg" if original_bytes else None,
    )


def _render(page: pdfium.PdfPage, page_index: int, dpi: int) -> CanonicalPage:
    # pdfium applies the page's /Rotate when rendering.
    bitmap = page.render(scale=dpi / 72.0)
    arr = bitmap.to_numpy()
    image = _bitmap_to_bgr(arr)
    return CanonicalPage(page_index=page_index, image=np.ascontiguousarray(image), dpi=float(dpi), source="rendered")


def _bitmap_to_bgr(arr: np.ndarray) -> np.ndarray:
    """pdfium bitmaps are BGR/BGRA (or single channel). Normalise to BGR uint8."""
    if arr.ndim == 2:
        return cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    if arr.shape[2] == 1:
        return cv2.cvtColor(arr[:, :, 0], cv2.COLOR_GRAY2BGR)
    return arr.astype(np.uint8)


def _rotate_clockwise(image: np.ndarray, degrees: int) -> np.ndarray:
    return {
        90: cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
        180: cv2.rotate(image, cv2.ROTATE_180),
        270: cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }.get(degrees % 360, image)
