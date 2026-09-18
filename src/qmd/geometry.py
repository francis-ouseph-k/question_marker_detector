"""Coordinate transforms (TD §5.2, §8, §9).

Every derived image (deskewed page, cropped margin region, ...) carries a 2x3
affine matrix ``T`` that maps CANONICAL page coordinates to the derived image:

    [x_d, y_d]^T = T @ [x_c, y_c, 1]^T

To bring a point found in a derived image back to the canonical page we apply
the inverse. Chained stages multiply their matrices (``compose``).

Why not just "add the crop offset"? Because a deskew rotation may also be
involved; the affine form handles crop, scale and rotation uniformly.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from qmd.models import BBox, Point

Affine = np.ndarray  # shape (2, 3), float64


def identity() -> Affine:
    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def translation(dx: float, dy: float) -> Affine:
    """Matrix for 'derived = canonical + (dx, dy)'. A crop starting at (x0, y0) is translation(-x0, -y0)."""
    return np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]])


def _to3(m: Affine) -> np.ndarray:
    return np.vstack([m, [0.0, 0.0, 1.0]])


def compose(second: Affine, first: Affine) -> Affine:
    """Return the matrix that applies ``first`` and then ``second``."""
    return (_to3(second) @ _to3(first))[:2]


def invert(m: Affine) -> Affine:
    return np.linalg.inv(_to3(m))[:2]


def apply_points(m: Affine, points: Sequence[Sequence[float]]) -> np.ndarray:
    """Apply ``m`` to an (N, 2) array of points."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    ones = np.ones((pts.shape[0], 1))
    return (np.hstack([pts, ones]) @ m.T).reshape(-1, 2)


def polygon_to_canonical(derived_to_canonical: Affine, polygon: Sequence[Sequence[float]]) -> list[list[int]]:
    pts = apply_points(derived_to_canonical, polygon)
    return [[int(round(x)), int(round(y))] for x, y in pts]


def bbox_of_polygon(polygon: Sequence[Sequence[float]]) -> BBox:
    """Axis-aligned bounding box of a polygon (TD §8: recompute after transforming)."""
    pts = np.asarray(polygon, dtype=np.float64).reshape(-1, 2)
    x0, y0 = np.floor(pts.min(axis=0))
    x1, y1 = np.ceil(pts.max(axis=0))
    return BBox(x=int(x0), y=int(y0), width=max(1, int(x1 - x0)), height=max(1, int(y1 - y0)))


def box_polygon(x: float, y: float, w: float, h: float) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def point_to_canonical(derived_to_canonical: Affine, x: float, y: float) -> Point:
    px, py = apply_points(derived_to_canonical, [[x, y]])[0]
    return Point(x=int(round(px)), y=int(round(py)))


def clamp_bbox(box: BBox, width: int, height: int) -> BBox:
    x0 = min(max(box.x, 0), width - 1)
    y0 = min(max(box.y, 0), height - 1)
    x1 = min(max(box.x2, x0 + 1), width)
    y1 = min(max(box.y2, y0 + 1), height)
    return BBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)


def iou(a: BBox, b: BBox) -> float:
    ix = max(0, min(a.x2, b.x2) - max(a.x, b.x))
    iy = max(0, min(a.y2, b.y2) - max(a.y, b.y))
    inter = ix * iy
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


def union_bbox(boxes: Sequence[BBox]) -> BBox:
    x0 = min(b.x for b in boxes)
    y0 = min(b.y for b in boxes)
    x1 = max(b.x2 for b in boxes)
    y1 = max(b.y2 for b in boxes)
    return BBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
