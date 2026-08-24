"""Tests for src.pipelines.pipes._shared.inpainting.region_utils"""

import numpy as np
import pytest

from src.pipelines.pipes._shared.inpainting.region_utils import (
    compute_initial_abcd,
    solve_abcd,
    get_image_shape_ceil,
    set_image_shape_ceil,
    resample_image,
)


class TestComputeInitialAbcd:
    def test_returns_full_bounds_for_empty_mask(self):
        mask = np.zeros((100, 200), dtype=np.uint8)
        a, b, c, d = compute_initial_abcd(mask)
        assert (a, b, c, d) == (0, 100, 0, 200)

    def test_computes_bounding_box_around_masked_region(self):
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[80:120, 90:110] = 255
        a, b, c, d = compute_initial_abcd(mask)
        # Should contain the masked region
        assert a <= 80
        assert b >= 120
        assert c <= 90
        assert d >= 110

    def test_expands_by_15_percent(self):
        mask = np.zeros((500, 500), dtype=np.uint8)
        mask[200:300, 200:300] = 255
        a, b, c, d = compute_initial_abcd(mask)
        # The bounding box should be larger than the raw mask extent
        raw_size = 100  # 300 - 200
        result_h = b - a
        result_w = d - c
        assert result_h > raw_size
        assert result_w > raw_size

    def test_clamps_to_image_bounds(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[0:10, 0:10] = 255  # Corner mask
        a, b, c, d = compute_initial_abcd(mask)
        assert a >= 0
        assert b <= 100
        assert c >= 0
        assert d <= 100

    def test_returns_integers(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[30:70, 40:60] = 255
        a, b, c, d = compute_initial_abcd(mask)
        assert isinstance(a, int)
        assert isinstance(b, int)
        assert isinstance(c, int)
        assert isinstance(d, int)

    def test_single_pixel_mask(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[50, 50] = 255
        a, b, c, d = compute_initial_abcd(mask)
        assert a <= 50
        assert b >= 50
        assert c <= 50
        assert d >= 50


class TestSolveAbcd:
    def test_returns_full_image_when_k_is_1(self):
        mask = np.zeros((100, 200), dtype=np.uint8)
        a, b, c, d = solve_abcd(mask, 30, 70, 50, 150, k=1.0)
        assert (a, b, c, d) == (0, 100, 0, 200)

    def test_expands_to_cover_k_fraction(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        a, b, c, d = solve_abcd(mask, 40, 60, 40, 60, k=0.5)
        assert (b - a) >= 100 * 0.5
        assert (d - c) >= 100 * 0.5

    def test_clamps_to_bounds(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        a, b, c, d = solve_abcd(mask, 10, 90, 10, 90, k=0.99)
        assert a >= 0
        assert b <= 100
        assert c >= 0
        assert d <= 100

    def test_k_zero_returns_original(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        a, b, c, d = solve_abcd(mask, 30, 70, 30, 70, k=0.0)
        # k=0 means any box size is sufficient
        assert a == 30
        assert b == 70
        assert c == 30
        assert d == 70

    def test_raises_on_invalid_k(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        with pytest.raises(AssertionError):
            solve_abcd(mask, 30, 70, 30, 70, k=1.5)
        with pytest.raises(AssertionError):
            solve_abcd(mask, 30, 70, 30, 70, k=-0.1)

    def test_returns_integers(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        a, b, c, d = solve_abcd(mask, 30, 70, 30, 70, k=0.618)
        assert isinstance(a, int)
        assert isinstance(b, int)
        assert isinstance(c, int)
        assert isinstance(d, int)


class TestGetImageShapeCeil:
    def test_returns_max_dimension_for_landscape(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        assert get_image_shape_ceil(image) == 200

    def test_returns_max_dimension_for_portrait(self):
        image = np.zeros((300, 100, 3), dtype=np.uint8)
        assert get_image_shape_ceil(image) == 300

    def test_returns_dimension_for_square(self):
        image = np.zeros((128, 128, 3), dtype=np.uint8)
        assert get_image_shape_ceil(image) == 128

    def test_works_with_2d_array(self):
        image = np.zeros((64, 128), dtype=np.uint8)
        assert get_image_shape_ceil(image) == 128


class TestSetImageShapeCeil:
    def test_downscales_large_image(self):
        image = np.random.randint(0, 255, (2048, 1024, 3), dtype=np.uint8)
        result = set_image_shape_ceil(image, 1024)
        assert max(result.shape[:2]) <= 1024

    def test_preserves_small_image_within_max(self):
        image = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        result = set_image_shape_ceil(image, 1024)
        assert result.shape == image.shape

    def test_ensures_divisibility_by_8(self):
        image = np.random.randint(0, 255, (103, 205, 3), dtype=np.uint8)
        result = set_image_shape_ceil(image, 200)
        assert result.shape[0] % 8 == 0
        assert result.shape[1] % 8 == 0

    def test_already_divisible_no_change(self):
        image = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        result = set_image_shape_ceil(image, 128)
        # 64 is divisible by 8 and <= 128
        assert result.shape == (64, 64, 3)

    def test_preserves_channel_count(self):
        image = np.random.randint(0, 255, (200, 300, 3), dtype=np.uint8)
        result = set_image_shape_ceil(image, 100)
        assert result.shape[2] == 3


class TestResampleImage:
    def test_resizes_to_target_dimensions(self):
        image = np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8)
        result = resample_image(image, 50, 25)
        assert result.shape == (25, 50, 3)

    def test_upscales(self):
        image = np.random.randint(0, 255, (32, 32, 3), dtype=np.uint8)
        result = resample_image(image, 128, 128)
        assert result.shape == (128, 128, 3)

    def test_works_with_grayscale(self):
        image = np.random.randint(0, 255, (64, 64), dtype=np.uint8)
        result = resample_image(image, 32, 32)
        assert result.shape == (32, 32)

    def test_preserves_dtype(self):
        image = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        result = resample_image(image, 32, 32)
        assert result.dtype == np.uint8
