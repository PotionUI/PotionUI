"""Tests for the shared alpha helpers (`_shared.imaging.alpha`): bbox
extraction, matte-strength smoothstep, and feathering."""

import numpy as np
import pytest
from PIL import Image

from src.pipelines.pipes._shared.imaging.alpha import (
    alpha_bbox,
    apply_matte_strength,
    feather_alpha,
)


def _rgba(arr):
    return Image.fromarray(arr.astype(np.uint8), mode="RGBA")


# -- alpha_bbox ----------------------------------------------------------

def test_alpha_bbox_finds_opaque_rect():
    arr = np.zeros((20, 30, 4), dtype=np.uint8)
    arr[5:15, 10:20, 3] = 255  # opaque rect: y in [5,15), x in [10,20)
    bbox = alpha_bbox(_rgba(arr), threshold=16)
    assert bbox == (10, 5, 10, 10)


def test_alpha_bbox_empty_image_returns_none():
    arr = np.zeros((20, 30, 4), dtype=np.uint8)  # alpha all zero
    assert alpha_bbox(_rgba(arr), threshold=16) is None


def test_alpha_bbox_bite_check_threshold_matters():
    """Break the threshold comparison direction; the test must go red."""
    arr = np.zeros((10, 10, 4), dtype=np.uint8)
    arr[..., 3] = 10  # below the default threshold of 16
    assert alpha_bbox(_rgba(arr), threshold=16) is None

    # A broken implementation using `>=` on a threshold of 255 would treat
    # nothing as opaque even for a fully-opaque image; confirm the real
    # boundary (strictly greater than) behaves as documented.
    arr[..., 3] = 17
    assert alpha_bbox(_rgba(arr), threshold=16) == (0, 0, 10, 10)
    assert alpha_bbox(_rgba(arr), threshold=17) is None


# -- apply_matte_strength -------------------------------------------------

def test_matte_strength_zero_is_identity():
    alpha = np.array([0, 50, 128, 200, 255], dtype=np.uint8)
    assert np.array_equal(apply_matte_strength(alpha, 0), alpha)


def test_matte_strength_hundred_is_hard_threshold_at_128():
    alpha = np.array([0, 100, 127, 128, 129, 200, 255], dtype=np.uint8)
    result = apply_matte_strength(alpha, 100)
    assert list(result) == [0, 0, 0, 255, 255, 255, 255]


def test_matte_strength_commits_mid_grey_pixels():
    """The failure this exists to fix: a matting model that 'removed
    nothing' leaves the whole background at a mid-grey like 140. At
    strength=0 that pixel stays background-colored garbage (140); a
    sufficiently high strength must commit it toward fully opaque since
    140 > 128."""
    mostly_opaque_background = np.full((4, 4), 140, dtype=np.uint8)
    identity = apply_matte_strength(mostly_opaque_background, 0)
    assert np.all(identity == 140)

    committed = apply_matte_strength(mostly_opaque_background, 100)
    assert np.all(committed == 255)


def test_matte_strength_monotonic_in_strength_for_a_fixed_input():
    alpha = np.array([140], dtype=np.uint8)
    values = [int(apply_matte_strength(alpha, s)[0]) for s in (0, 25, 50, 75, 100)]
    assert values == sorted(values)


# -- feather_alpha ---------------------------------------------------------

def test_feather_zero_is_identity():
    alpha = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    assert np.array_equal(feather_alpha(alpha, 0.0), alpha)


def test_feather_softens_a_hard_edge():
    alpha = np.zeros((20, 20), dtype=np.uint8)
    alpha[:, 10:] = 255
    blurred = feather_alpha(alpha, 4.0)

    # A hard edge has exactly one transition column; a feathered one must
    # have intermediate (neither 0 nor 255) values near the boundary - this
    # is the property a no-op feather implementation would fail.
    edge_strip = blurred[10, 6:14]
    assert any(0 < v < 255 for v in edge_strip)
    # Far from the edge, values are unaffected.
    assert blurred[10, 0] == 0
    assert blurred[10, 19] == 255
