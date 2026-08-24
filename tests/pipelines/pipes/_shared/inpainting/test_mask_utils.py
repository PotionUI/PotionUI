"""Tests for src.pipelines.pipes._shared.inpainting.mask_utils"""

import numpy as np

from src.pipelines.pipes._shared.inpainting.mask_utils import color_correction


class TestColorCorrection:
    def test_full_mask_returns_foreground(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 200
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 50
        mask = np.ones((10, 10), dtype=np.uint8) * 255
        result = color_correction(fg, bg, mask)
        np.testing.assert_array_equal(result, fg)

    def test_zero_mask_returns_background(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 200
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 50
        mask = np.zeros((10, 10), dtype=np.uint8)
        result = color_correction(fg, bg, mask)
        np.testing.assert_array_equal(result, bg)

    def test_half_mask_blends_evenly(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 200
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 100
        mask = np.ones((10, 10), dtype=np.uint8) * 128  # ~50%
        result = color_correction(fg, bg, mask)
        # ~128/255 * 200 + ~127/255 * 100 = ~150
        expected_approx = 150
        assert abs(result.mean() - expected_approx) < 5

    def test_output_clipped_to_valid_range(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 255
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 255
        mask = np.ones((10, 10), dtype=np.uint8) * 255
        result = color_correction(fg, bg, mask)
        assert result.max() <= 255
        assert result.min() >= 0

    def test_output_dtype_is_uint8(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 100
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 50
        mask = np.ones((10, 10), dtype=np.uint8) * 128
        result = color_correction(fg, bg, mask)
        assert result.dtype == np.uint8

    def test_handles_3d_mask(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 200
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 50
        mask = np.ones((10, 10, 1), dtype=np.uint8) * 255
        result = color_correction(fg, bg, mask)
        np.testing.assert_array_equal(result, fg)

    def test_preserves_shape(self):
        fg = np.ones((64, 128, 3), dtype=np.uint8) * 100
        bg = np.ones((64, 128, 3), dtype=np.uint8) * 50
        mask = np.ones((64, 128), dtype=np.uint8) * 128
        result = color_correction(fg, bg, mask)
        assert result.shape == (64, 128, 3)

    def test_spatially_varying_mask(self):
        fg = np.ones((10, 10, 3), dtype=np.uint8) * 200
        bg = np.ones((10, 10, 3), dtype=np.uint8) * 0
        mask = np.zeros((10, 10), dtype=np.uint8)
        mask[:5, :] = 255  # Top half is foreground
        result = color_correction(fg, bg, mask)
        # Top half should be foreground
        np.testing.assert_array_equal(result[:5, :], fg[:5, :])
        # Bottom half should be background
        np.testing.assert_array_equal(result[5:, :], bg[5:, :])
