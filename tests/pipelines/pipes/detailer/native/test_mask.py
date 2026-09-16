import math

import numpy as np
import pytest
from PIL import Image

from src.pipelines.pipes.detailer.native.detection import FaceDetection
from src.pipelines.pipes.detailer.native.mask import (
    build_face_mask,
    default_dilate,
    oval_polygon,
    oval_ring,
)


def _ellipse_polygon(cx, cy, rx, ry, count=32):
    return [
        (cx + rx * math.cos(2 * math.pi * i / count), cy + ry * math.sin(2 * math.pi * i / count))
        for i in range(count)
    ]


def test_oval_ring_walks_unordered_connections_into_a_cycle():
    connections = {(3, 0), (1, 2), (0, 1), (2, 3)}
    ring = oval_ring(connections)
    assert sorted(ring) == [0, 1, 2, 3]
    assert len(ring) == 4
    edges = {frozenset((ring[i], ring[(i + 1) % 4])) for i in range(4)}
    assert edges == {frozenset(e) for e in connections}


def test_oval_ring_rejects_a_broken_contour():
    with pytest.raises(ValueError):
        oval_ring({(0, 1), (1, 2)})
    with pytest.raises(ValueError):
        oval_ring({(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)})


def test_oval_polygon_declines_a_ring_that_overruns_the_landmarks():
    assert oval_polygon([(0.0, 0.0), (1.0, 1.0)], [0, 5]) is None
    assert oval_polygon([], [0]) is None


def test_oval_mask_fills_the_face_and_leaves_the_box_corners_empty():
    polygon = _ellipse_polygon(100, 100, 45, 60)
    mask = np.asarray(build_face_mask((200, 200), polygon=polygon, dilate=8, feather=10))

    assert mask[100, 100] == 255
    for y, x in ((0, 0), (0, 199), (199, 0), (199, 199)):
        assert mask[y, x] == 0


def test_oval_mask_feather_is_monotone_outward():
    polygon = _ellipse_polygon(100, 100, 40, 40)
    mask = np.asarray(build_face_mask((200, 200), polygon=polygon, dilate=10, feather=12)).astype(int)

    ray = mask[100, 100:]
    assert ray[0] == 255
    assert ray[-1] == 0
    assert all(later <= earlier for earlier, later in zip(ray, ray[1:]))

    band = [value for value in ray if 0 < value < 255]
    assert len(band) >= 12
    assert min(band) < 40 and max(band) > 215


def test_dilate_grows_the_oval_outward():
    polygon = _ellipse_polygon(100, 100, 30, 30)
    plain = np.asarray(build_face_mask((200, 200), polygon=polygon, dilate=0, feather=0)).astype(int)
    grown = np.asarray(build_face_mask((200, 200), polygon=polygon, dilate=12, feather=0)).astype(int)

    assert grown.sum() > plain.sum()
    assert plain[100, 138] == 0 and grown[100, 138] == 255


def test_box_mode_is_a_rounded_rectangle():
    mask = np.asarray(build_face_mask((200, 200), polygon=None, box=(40, 40, 160, 160),
                                      dilate=0, feather=0)).astype(int)

    assert mask[100, 41] == 255
    assert mask[41, 41] == 0
    assert mask[100, 100] == 255
    assert mask[10, 10] == 0


def test_box_mode_is_used_when_no_landmarks_are_available():
    without_polygon = np.asarray(build_face_mask((120, 120), polygon=None, box=(20, 20, 100, 100),
                                                 dilate=4, feather=6))
    degenerate = np.asarray(build_face_mask((120, 120), polygon=[(10.0, 10.0)], box=(20, 20, 100, 100),
                                            dilate=4, feather=6))
    assert np.array_equal(without_polygon, degenerate)


def test_default_dilate_is_a_fraction_of_the_short_side():
    assert default_dilate((0, 0, 200, 50), 0.12) == 6
    assert default_dilate((0, 0, 50, 200), 0.12) == 6
    assert default_dilate((0, 0, 50, 50), 0.0) == 0


def test_yolo_detection_carries_no_landmarks():
    detection = FaceDetection(box=(0.0, 0.0, 10.0, 10.0))
    assert detection.landmarks is None
    assert detection.area == 100.0
