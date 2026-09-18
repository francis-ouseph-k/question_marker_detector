"""Coordinate transforms (TD §9)."""

import cv2
import numpy as np
import pytest

from qmd import geometry
from qmd.models import BBox


def test_crop_translation_roundtrip():
    # Crop starting at (20, 500): canonical -> crop is translation(-20, -500).
    to_crop = geometry.translation(-20, -500)
    back = geometry.invert(to_crop)
    pt = geometry.point_to_canonical(back, 42, 118)
    assert (pt.x, pt.y) == (62, 618)


def test_compose_crop_after_rotation():
    rot = cv2.getRotationMatrix2D((500, 500), 2.0, 1.0)
    crop = geometry.translation(-100, -50)
    both = geometry.compose(crop, rot)
    p = np.array([[300.0, 700.0]])
    direct = geometry.apply_points(crop, geometry.apply_points(rot, p))
    assert np.allclose(geometry.apply_points(both, p), direct)
    # and inverse brings it back
    assert np.allclose(geometry.apply_points(geometry.invert(both), direct), p)


def test_rotated_box_recomputed_from_polygon():
    rot = cv2.getRotationMatrix2D((0, 0), 90.0, 1.0)
    poly = geometry.polygon_to_canonical(rot, geometry.box_polygon(10, 0, 20, 5))
    bb = geometry.bbox_of_polygon(poly)
    assert (bb.width, bb.height) == (5, 20)   # width/height swap under 90 degrees


def test_iou_and_union():
    a, b = BBox(x=0, y=0, width=10, height=10), BBox(x=5, y=0, width=10, height=10)
    assert geometry.iou(a, b) == pytest.approx(50 / 150)
    u = geometry.union_bbox([a, b])
    assert (u.x, u.width) == (0, 15)


def test_clamp():
    b = geometry.clamp_bbox(BBox(x=-5, y=-5, width=20, height=20), 10, 10)
    assert (b.x, b.y, b.x2, b.y2) == (0, 0, 10, 10)
