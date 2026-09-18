"""Ink handling: valuer-ink separation and ink masks (TD §6.3, Requirements FR-18).

Students write in blue/black (Problem Statement A5). Valuers write scores and
ticks in red or green. Those valuer marks sit in the same margin as the
question markers and are often valid question numbers ("7", "3"), so they
must be removed before recognition.
"""

from __future__ import annotations

import cv2
import numpy as np

from qmd.config import InkConfig


def is_colour_scan(image_bgr: np.ndarray, cfg: InkConfig) -> bool:
    """True if the scan carries real colour information.

    A grayscale scan stored as RGB has identical channels everywhere. A colour
    scan of white paper already shows small channel differences (paper tint),
    even on a page without any coloured ink. We therefore look at the channel
    spread of ordinary pixels, not at "is there coloured ink on this page".
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        return False
    small = cv2.resize(image_bgr, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA).astype(np.int16)
    spread = small.max(axis=2) - small.min(axis=2)
    return float(np.percentile(spread, 90)) >= cfg.grayscale_channel_spread_min


def valuer_ink_mask(image_bgr: np.ndarray, cfg: InkConfig) -> np.ndarray:
    """Binary mask (uint8 0/255) of red and green ink pixels, slightly dilated."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    coloured = (sat >= cfg.min_saturation) & (val >= cfg.min_value)
    red = np.zeros(hue.shape, dtype=bool)
    for lo, hi in cfg.red_hue_ranges:
        red |= (hue >= lo) & (hue <= hi)
    g_lo, g_hi = cfg.green_hue_range
    green = (hue >= g_lo) & (hue <= g_hi)
    mask = ((red | green) & coloured).astype(np.uint8) * 255
    # Dilate so anti-aliased edges of the strokes are removed too.
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)


def to_student_gray(image_bgr: np.ndarray, cfg: InkConfig, colour_scan: bool) -> tuple[np.ndarray, np.ndarray]:
    """Grayscale image with valuer ink painted white.

    Returns ``(gray, valuer_mask)``. ``valuer_mask`` is all zeros when colour
    separation is disabled or unavailable.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    if cfg.enable_colour_separation and colour_scan:
        mask = valuer_ink_mask(image_bgr, cfg)
        gray = gray.copy()
        gray[mask > 0] = 255
    else:
        mask = np.zeros(gray.shape, dtype=np.uint8)
    return gray, mask


def dark_ink(gray: np.ndarray, threshold: int) -> np.ndarray:
    """Binary (0/1 uint8) mask of dark pixels. Faint show-through and the
    light watermark are above the threshold and therefore excluded."""
    return (gray < threshold).astype(np.uint8)
