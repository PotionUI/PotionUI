"""Tests for the shared colour helpers (`_shared.imaging.color`): hex
parsing, border-ring auto colour, chrominance conversion, and despill."""

import numpy as np
import pytest

from src.pipelines.pipes._shared.imaging.color import (
    border_ring_color,
    despill,
    parse_hex_color,
    rgb_to_cbcr,
)


def test_parse_hex_color():
    assert parse_hex_color("#00ff00") == (0, 255, 0)
    assert parse_hex_color("#112233") == (0x11, 0x22, 0x33)


def test_parse_hex_color_rejects_garbage():
    with pytest.raises(ValueError):
        parse_hex_color("not-a-color")


def test_border_ring_color_picks_the_modal_edge_color():
    arr = np.full((40, 40, 3), 255, dtype=np.uint8)  # white border
    arr[10:30, 10:30] = (10, 200, 10)  # green subject, away from every edge
    assert border_ring_color(arr) == (255, 255, 255)


def test_border_ring_color_ignores_a_small_off_color_intrusion():
    arr = np.full((40, 40, 3), (0, 255, 0), dtype=np.uint8)
    arr[0, 0] = (1, 2, 3)  # one odd corner pixel shouldn't flip the mode
    assert border_ring_color(arr) == (0, 255, 0)


# -- rgb_to_cbcr: the brightness-invariance property the pipe depends on ----

def test_neutral_gray_is_always_the_chroma_origin():
    """Any (v, v, v) must land exactly on (128, 128) regardless of v - this
    is the property that makes chrominance distance brightness-invariant."""
    grays = np.array([[[v, v, v] for v in (0, 1, 60, 85, 128, 200, 254, 255)]], dtype=np.float64)
    cbcr = rgb_to_cbcr(grays)
    assert np.allclose(cbcr, 128.0, atol=1e-9)


def test_pure_green_chroma_is_off_center():
    cbcr = rgb_to_cbcr(np.array([[[0.0, 255.0, 0.0]]]))
    cb, cr = cbcr[0, 0]
    assert (cb, cr) != (128.0, 128.0)
    assert abs(cb - 128.0) > 50 and abs(cr - 128.0) > 50


def test_scaled_pure_hue_moves_linearly_toward_neutral():
    """A pure-hue colour scaled by a brightness factor k moves LINEARLY from
    its own chroma point toward (128, 128) as k -> 0 - the mechanism the
    color_key docstring/report leans on. This is the bite-check for
    `rgb_to_cbcr` being a genuinely linear transform (a buggy non-linear
    stand-in would fail the exact proportionality asserted here)."""
    base = np.array([[[0.0, 255.0, 0.0]]])
    full = rgb_to_cbcr(base)[0, 0]
    neutral = np.array([128.0, 128.0])

    for k in (0.75, 0.5, 0.25, 0.0):
        scaled = rgb_to_cbcr(base * k)[0, 0]
        expected = neutral + k * (full - neutral)
        assert np.allclose(scaled, expected, atol=1e-9)


# -- despill -----------------------------------------------------------------

def test_despill_clamps_dominant_channel_to_average_of_others():
    key = (0, 255, 0)  # green is dominant
    rgb = np.array([[[10, 250, 10]]], dtype=np.uint8)  # green fringe on a near-gray pixel
    out = despill(rgb, key)
    r, g, b = out[0, 0]
    assert g <= (int(r) + int(b)) // 2 + 1  # clamped toward the average of R and B


def test_despill_is_a_no_op_when_dominant_channel_is_already_low():
    key = (0, 255, 0)
    rgb = np.array([[[200, 50, 200]]], dtype=np.uint8)  # G already below avg(R, B)
    out = despill(rgb, key)
    assert tuple(out[0, 0]) == (200, 50, 200)


def test_despill_bite_check_wrong_channel_would_fail():
    """despill must act on the KEY colour's dominant channel, not a fixed
    one - pin that against a blue key."""
    key = (0, 0, 255)  # blue dominant
    rgb = np.array([[[10, 10, 250]]], dtype=np.uint8)
    out = despill(rgb, key)
    r, g, b = out[0, 0]
    assert b <= (int(r) + int(g)) // 2 + 1
