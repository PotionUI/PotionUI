"""Tests for the ``Embeddings1DConnector`` head-count guard.

A bad ``inner``/``dim_head`` pairing (e.g. one connector's ``inner`` combined
with the OTHER stream's ``dim_head`` -- video and audio connectors are
configured independently, see ``LTXAVConfig``) used to construct without
complaint and only blow up later, deep inside ``split_freqs_cis`` (rope.py),
as a bare "128 vs 64" tensor-shape broadcast error. ``Embeddings1DConnector``
now asserts the head split is exact and even-per-head at construction time,
so the failure names the declared inputs instead.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.ltx.model import Embeddings1DConnector
from vendor.gpl.comfyui.ops import pick_operations


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def test_valid_dims_construct_without_error():
    # 3840 / 128 = 30 heads, dim_head even -> matches the real 19b shared
    # connector dims; must not raise.
    connector = Embeddings1DConnector(
        inner=3840, dim_head=128, num_layers=1, num_learnable_registers=4,
        operations=_fp32_ops(),
    )
    assert connector.num_attention_heads == 30


def test_valid_audio_dims_construct_without_error():
    # 2.3's per-stream audio connector: 2048 / 64 = 32 heads.
    connector = Embeddings1DConnector(
        inner=2048, dim_head=64, num_layers=1, num_learnable_registers=4,
        operations=_fp32_ops(),
    )
    assert connector.num_attention_heads == 32


def test_heads_mismatch_raises_with_declared_and_derived_numbers():
    # inner not an exact multiple of dim_head -- would otherwise silently
    # truncate the derived head count (130 // 64 = 2, dropping a remainder of
    # 2) instead of raising, corrupting the RoPE head split downstream.
    with pytest.raises(ValueError, match=r"heads mismatch") as exc_info:
        Embeddings1DConnector(
            inner=130, dim_head=64, num_layers=1, num_learnable_registers=4,
            operations=_fp32_ops(),
        )
    message = str(exc_info.value)
    assert "inner=130" in message
    assert "dim_head=64" in message


def test_odd_dim_head_raises_even_when_divisible():
    # inner % dim_head == 0 (90 / 9 = 10 heads) but dim_head is ODD -- the
    # RoPE half-dim per head would not divide evenly either; also rejected.
    with pytest.raises(ValueError, match=r"heads mismatch"):
        Embeddings1DConnector(
            inner=90, dim_head=9, num_layers=1, num_learnable_registers=4,
            operations=_fp32_ops(),
        )


def test_zero_dim_head_raises_clean_error_not_zero_division():
    # A bad zero dim_head must surface as the same clean ValueError, not an
    # opaque ZeroDivisionError from BasicTransformerBlock1D's own unguarded
    # `dim // dim_head` -- the guard must run before that submodule is built.
    with pytest.raises(ValueError, match=r"heads mismatch"):
        Embeddings1DConnector(
            inner=128, dim_head=0, num_layers=1, num_learnable_registers=4,
            operations=_fp32_ops(),
        )
