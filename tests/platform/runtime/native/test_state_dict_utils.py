"""Tests for state-dict introspection helpers."""

from __future__ import annotations

import torch

from src.platform.runtime.native.io.state_dict_utils import (
    count_blocks,
    detect_prefix,
    is_nvfp4_packed,
    key_shapes,
    linear_in_features,
    strip_prefix,
    weight_dtype,
)


def test_strip_prefix():
    sd = {"model.a": torch.zeros(1), "model.b": torch.zeros(1), "other.c": torch.zeros(1)}
    out = strip_prefix(sd, "model.")
    assert set(out) == {"a", "b"}


def test_detect_prefix_count_based():
    sd = {f"diffusion_model.x{i}": torch.zeros(1) for i in range(5)}
    sd["model.y"] = torch.zeros(1)
    assert detect_prefix(sd, ["model.", "diffusion_model."]) == "diffusion_model."


def test_detect_prefix_none():
    sd = {"a": torch.zeros(1)}
    assert detect_prefix(sd, ["x.", "y."]) is None


def test_count_blocks_contiguous_and_gap():
    sd = {}
    for i in (0, 1, 2, 3):
        sd[f"blocks.{i}.w"] = torch.zeros(1)
    sd["blocks.9.w"] = torch.zeros(1)   # gap -> not counted
    assert count_blocks(sd, "blocks.{}.") == 4


def test_count_blocks_empty():
    assert count_blocks({}, "blocks.{}.") == 0


def test_key_shapes():
    sd = {"a": torch.zeros(2, 3), "b": torch.zeros(4)}
    assert key_shapes(sd) == {"a": (2, 3), "b": (4,)}


def test_weight_dtype_majority_float():
    sd = {
        "a": torch.zeros(3, dtype=torch.bfloat16),
        "b": torch.zeros(3, dtype=torch.bfloat16),
        "c": torch.zeros(3, dtype=torch.float32),
        "idx": torch.zeros(3, dtype=torch.int64),  # ignored
    }
    assert weight_dtype(sd) == torch.bfloat16


def test_weight_dtype_all_int_returns_none():
    sd = {"idx": torch.zeros(3, dtype=torch.int64)}
    assert weight_dtype(sd) is None


def test_is_nvfp4_packed_true_with_scale2_sibling():
    sd = {"lin.weight": torch.zeros(8, 4, dtype=torch.uint8), "lin.weight_scale_2": torch.tensor(0.0)}
    assert is_nvfp4_packed(sd, "lin.weight") is True


def test_is_nvfp4_packed_false_without_sibling():
    sd = {"lin.weight": torch.zeros(8, 4)}
    assert is_nvfp4_packed(sd, "lin.weight") is False


def test_is_nvfp4_packed_false_for_non_weight_key():
    sd = {"lin.bias": torch.zeros(8), "lin.weight_scale_2": torch.tensor(0.0)}
    assert is_nvfp4_packed(sd, "lin.bias") is False


def test_linear_in_features_unpacked_reads_shape_directly():
    sd = {"lin.weight": torch.zeros(8, 16)}
    assert linear_in_features(sd, "lin.weight") == 16


def test_linear_in_features_packed_doubles_stored_width():
    # nvfp4 stores [out, in // 2]; the true in-features (16) must be recovered.
    sd = {
        "lin.weight": torch.zeros(8, 8, dtype=torch.uint8),
        "lin.weight_scale_2": torch.tensor(0.0),
    }
    assert linear_in_features(sd, "lin.weight") == 16
