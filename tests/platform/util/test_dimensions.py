"""Tests for src.platform.util.dimensions"""

import pytest

from src.platform.util.dimensions import (
    align_dimensions,
    floor_to_multiple,
    round_to_multiple,
    validate_resolution,
)


# ---- round_to_multiple ----

class TestRoundToMultiple:
    def test_already_aligned(self):
        assert round_to_multiple(16, 8) == 16

    def test_rounds_up(self):
        assert round_to_multiple(17, 8) == 24

    def test_one_below(self):
        assert round_to_multiple(15, 8) == 16

    def test_zero(self):
        assert round_to_multiple(0, 8) == 0

    def test_custom_multiple(self):
        assert round_to_multiple(10, 16) == 16
        assert round_to_multiple(32, 16) == 32

    def test_matches_memory_manager_formula(self):
        # Verify our implementation matches ((value + 7) // 8) * 8
        for v in range(0, 200):
            expected = ((v + 7) // 8) * 8
            assert round_to_multiple(v, 8) == expected, f"Failed for {v}"


# ---- floor_to_multiple ----

class TestFloorToMultiple:
    def test_already_aligned(self):
        assert floor_to_multiple(16, 8) == 16

    def test_rounds_down(self):
        assert floor_to_multiple(17, 8) == 16

    def test_one_above(self):
        assert floor_to_multiple(9, 8) == 8

    def test_zero(self):
        assert floor_to_multiple(0, 8) == 0

    def test_below_multiple(self):
        assert floor_to_multiple(7, 8) == 0

    def test_custom_multiple(self):
        assert floor_to_multiple(20, 16) == 16

    def test_matches_inpaint_crop_formula(self):
        # Verify our implementation matches (value // 8) * 8
        for v in range(0, 200):
            expected = (v // 8) * 8
            assert floor_to_multiple(v, 8) == expected, f"Failed for {v}"


# ---- align_dimensions ----

class TestAlignDimensions:
    def test_floor_mode(self):
        w, h = align_dimensions(1025, 769, 8, "floor")
        assert w == 1024
        assert h == 768

    def test_ceil_mode(self):
        w, h = align_dimensions(1025, 769, 8, "ceil")
        assert w == 1032
        assert h == 776

    def test_already_aligned(self):
        w, h = align_dimensions(1024, 768, 8, "floor")
        assert w == 1024
        assert h == 768

    def test_custom_multiple(self):
        w, h = align_dimensions(100, 200, 64, "floor")
        assert w == 64
        assert h == 192


# ---- validate_resolution ----

class TestValidateResolution:
    def test_valid_resolution(self):
        validate_resolution(1024, 768)  # should not raise

    def test_invalid_width(self):
        with pytest.raises(ValueError, match="divisible by 8"):
            validate_resolution(1023, 768)

    def test_invalid_height(self):
        with pytest.raises(ValueError, match="divisible by 8"):
            validate_resolution(1024, 769)

    def test_both_invalid(self):
        with pytest.raises(ValueError):
            validate_resolution(1023, 769)

    def test_custom_multiple(self):
        validate_resolution(64, 128, multiple=64)  # should not raise
        with pytest.raises(ValueError):
            validate_resolution(48, 128, multiple=64)
