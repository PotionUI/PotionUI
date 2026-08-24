"""Tests for the resolution/frame snapping helpers.

The DiT patchify has no internal pad in every family (Krea-2 crashes on a
non-multiple axis), so width/height must land on ``spatial_downscale*patch`` and
frames on the causal VAE's ``1 + k*temporal_downscale`` lattice. These cover the
nearest-with-ties-down rounding, exact values, and the floors.
"""

from __future__ import annotations

import pytest

from src.platform.runtime.native.resolution import snap_frame_count, snap_resolution, snap_to_multiple


# -- snap_to_multiple ------------------------------------------------------

def test_exact_multiple_unchanged():
    assert snap_to_multiple(1024, 16) == 1024
    assert snap_to_multiple(512, 8) == 512


def test_tie_rounds_down():
    # 1080/16 == 67.5 -> 67 (not 68); the reported crash resolution.
    assert snap_to_multiple(1080, 16) == 1072


def test_rounds_to_nearest():
    assert snap_to_multiple(1085, 16) == 1088   # closer to 68*16
    assert snap_to_multiple(1070, 16) == 1072   # closer to 67*16
    assert snap_to_multiple(129, 16) == 128     # 129/16 == 8.06 -> 8


def test_minimum_one_multiple():
    assert snap_to_multiple(3, 16) == 16
    assert snap_to_multiple(0, 16) == 16
    assert snap_to_multiple(1, 32) == 32


def test_granularity_one_is_passthrough():
    assert snap_to_multiple(135, 1) == 135
    assert snap_to_multiple(0, 1) == 1


# -- snap_resolution -------------------------------------------------------

def test_resolution_snaps_both_axes():
    # Flux1 / Krea-2 / Qwen: spatial_downscale 8, patch 2 -> 16px grid.
    assert snap_resolution(1920, 1080, 8, 2) == (1920, 1072)
    assert snap_resolution(1080, 1920, 8, 2) == (1072, 1920)


def test_resolution_flux2_32px_grid():
    # Flux2: spatial_downscale 16, patch 2 -> 32px grid.
    assert snap_resolution(1080, 1920, 16, 2) == (1088, 1920)


def test_resolution_wan22_5b_32px_grid():
    assert snap_resolution(1920, 1080, 16, 2) == (1920, 1088)


def test_resolution_exact_multiples_unchanged():
    assert snap_resolution(1024, 1024, 8, 2) == (1024, 1024)
    assert snap_resolution(832, 480, 8, 2) == (832, 480)


# -- snap_frame_count ------------------------------------------------------

def test_frames_valid_lattice_unchanged():
    assert snap_frame_count(81, 4) == 81     # 1 + 4*20 (Wan default)
    assert snap_frame_count(1, 4) == 1       # single frame (k=0)
    assert snap_frame_count(5, 4) == 5


def test_frames_snap_to_nearest():
    assert snap_frame_count(100, 4) == 101   # nearest 1+4k
    assert snap_frame_count(80, 4) == 81
    assert snap_frame_count(3, 4) == 1        # tie -> down (1 vs 5)


def test_frames_temporal_one_is_passthrough():
    assert snap_frame_count(50, 1) == 50


def test_frames_floor_is_one():
    assert snap_frame_count(0, 4) == 1
    assert snap_frame_count(-3, 4) == 1


@pytest.mark.parametrize("frames", [1, 5, 9, 13, 81, 257])
def test_valid_frame_counts_are_fixed_points(frames):
    assert snap_frame_count(frames, 4) == frames
