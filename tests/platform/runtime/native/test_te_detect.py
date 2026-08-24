"""Tests for text-encoder detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.detect.te_detect import detect_te_config

from .conftest import clip_l_sd, qwen3_sd, t5xxl_sd

# Real Comfy-Org trimmed MiniMax-H3 TE safetensors header (fetched 2026-08-10 —
# see PORT_PLAN.md S4 / the port report); `ai/` is gitignored scratch, so a
# fresh checkout skips these rather than failing.
_H3_HEADER = Path(__file__).resolve().parents[4] / "ai" / "minimax_h3" / "te_bf16_header.json"
# Locally-downloaded nvfp4_awq repack's header (read from the on-disk file the
# maintainer already fetched, not range-requested — see the port report addendum).
_H3_NVFP4_HEADER = Path(__file__).resolve().parents[4] / "ai" / "minimax_h3" / "te_nvfp4_awq_header.json"


def test_detect_clip_l():
    c = detect_te_config(clip_l_sd(hidden=64, layers=12))
    assert c["te_type"] == "clip_l"
    assert c["variant"] == "clip_l"
    assert c["hidden_size"] == 64
    assert c["num_layers"] == 12
    assert c["vocab_size"] == 49408
    assert c["scaled_fp8"] is False


def test_detect_t5xxl():
    c = detect_te_config(t5xxl_sd(hidden=64, blocks=24))
    assert c["te_type"] == "t5xxl"
    assert c["variant"] == "t5xxl"
    assert c["num_layers"] == 24
    assert c["vocab_size"] == 32128


def test_detect_t5xxl_scaled_fp8_marker():
    c = detect_te_config(t5xxl_sd(scaled_fp8=True))
    assert c["scaled_fp8"] is True


def test_detect_qwen3_4b_by_hidden():
    c = detect_te_config(qwen3_sd(hidden=2560))
    assert c["te_type"] == "qwen3"
    assert c["variant"] == "qwen3_4b"
    assert c["hidden_size"] == 2560
    assert c["num_layers"] == 36


def test_detect_qwen3_8b_by_hidden():
    c = detect_te_config(qwen3_sd(hidden=4096))
    assert c["variant"] == "qwen3_8b"
    assert c["hidden_size"] == 4096


def test_qwen_needs_qk_norm_signature():
    # a generic model.* dict without q_norm is not Qwen3.
    sd = {"model.embed_tokens.weight": torch.zeros(100, 64)}
    assert detect_te_config(sd) is None


def test_unknown_returns_none():
    assert detect_te_config({"foo.bar": torch.zeros(1)}) is None


# --- Qwen3-VL width branch: Krea-2's 4B vs MiniMax-H3's 32B -----------------


def test_detect_qwen3vl_4b_nested_vision_by_hidden():
    sd = {
        "model.embed_tokens.weight": torch.zeros(260, 2560),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(8),
        "model.visual.blocks.0.attn.qkv.weight": torch.zeros(4, 4),
    }
    c = detect_te_config(sd)
    assert c["te_type"] == "qwen3vl"
    assert c["variant"] == "qwen3vl_4b"
    assert c["vision_top_level"] is False


def test_detect_qwen3vl_32b_toplevel_vision_by_hidden():
    # MiniMax-H3's Qwen3-VL-32B TE: hidden 5120, vision tower TOP-LEVEL
    # `visual.*` (not nested `model.visual.*` like the 4B) — see
    # te_detect.py's width-branch comment / the port report.
    sd = {
        "model.embed_tokens.weight": torch.zeros(260, 5120),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(8),
        "visual.blocks.0.attn.qkv.weight": torch.zeros(4, 4),
    }
    c = detect_te_config(sd)
    assert c["te_type"] == "qwen3vl"
    assert c["variant"] == "qwen3vl_32b"
    assert c["vision_top_level"] is True


def test_detect_qwen3vl_32b_from_real_h3_header_shapes():
    """Width-branch ground truth: build the synthetic dict's KEY SET and
    WIDTHS from the real Comfy-Org trimmed checkpoint's safetensors header
    (fetched from HF, not guessed) — tensors are shrunk (small torch.zeros)
    since only the shapes detection actually reads matter, but the widths
    themselves come from the header.
    """
    if not _H3_HEADER.is_file():
        pytest.skip(f"real TE header not present: {_H3_HEADER}")
    with open(_H3_HEADER) as f:
        header = json.load(f)

    hidden = header["model.embed_tokens.weight"]["shape"][1]
    assert hidden == 5120
    q_norm_dim = header["model.layers.0.self_attn.q_norm.weight"]["shape"][0]
    vision_hidden = header["visual.blocks.0.norm1.weight"]["shape"][0]

    sd = {
        "model.embed_tokens.weight": torch.zeros(64, hidden),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(q_norm_dim),
        "visual.blocks.0.norm1.weight": torch.zeros(vision_hidden),
    }
    c = detect_te_config(sd)
    assert c["te_type"] == "qwen3vl"
    assert c["variant"] == "qwen3vl_32b"
    assert c["hidden_size"] == 5120
    assert c["vision_top_level"] is True


def test_detect_qwen3vl_32b_from_real_nvfp4_awq_header_not_confused_by_quant_sidecars():
    """The nvfp4_awq repack adds `comfy_quant`/`weight_scale`/`weight_scale_2`
    (nvfp4 linears) and `pre_quant_scale` (AWQ activation-smoothing scale on
    `down_proj`/`o_proj`) sidecar tensors, plus an int8-quantized
    `embed_tokens` (`weight` I8 + per-row `weight_scale`, no nibble packing).
    Detection must read the same te_type/variant/hidden/layers/vision_top_level
    as the plain bf16 checkpoint — none of its key/shape checks look at these
    sidecars, but this is the ground-truth proof, not just that reasoning.
    """
    if not _H3_NVFP4_HEADER.is_file():
        pytest.skip(f"real nvfp4_awq TE header not present: {_H3_NVFP4_HEADER}")
    with open(_H3_NVFP4_HEADER) as f:
        header = json.load(f)

    dtype_map = {"BF16": torch.bfloat16, "F32": torch.float32, "F8_E4M3": torch.float32,
                 "U8": torch.uint8, "I8": torch.int8}
    sd = {
        k: torch.zeros(v["shape"] or [1], dtype=dtype_map.get(v["dtype"], torch.float32))
        for k, v in header.items() if k != "__metadata__"
    }

    c = detect_te_config(sd)
    assert c["te_type"] == "qwen3vl"
    assert c["variant"] == "qwen3vl_32b"
    assert c["hidden_size"] == 5120
    assert c["num_layers"] == 50
    assert c["vision_top_level"] is True
