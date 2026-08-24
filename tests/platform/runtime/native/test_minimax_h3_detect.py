"""Tests for MiniMax-H3 DiT detection + registry discrimination.

Synthetic state dicts are built at the EXACT dimensions in ``ai/minimax_h3/
pruned_fp8_header.json`` / ``full_bf16_header.json`` (hidden 5376, heads 56 x
head_dim 128 -> qkv 21504, ffn fc1 28672, adaln_proj [96768, {2688|8}], curve
table [1025, 8]) so the shape-derivation arithmetic is exercised against real
numbers, with a small (not 50) block count for test speed.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config

HIDDEN = 5376
HEADS = 56
HEAD_DIM = 128
INNER = HEADS * HEAD_DIM         # 7168
FFN = 14336
IN_CHANNELS = 24
VIDEO_PATCH_DIM = IN_CHANNELS * 4  # patch (1,2,2) -> 96
AUDIO_IN_CHANNELS = 32
TEXT_DIM = 5120
ROPE_FREQ_DIM = 16
FULL_TIME_EMBED_DIM = 2688
FREQ_DIM = 256
TIME_EMBED_HIDDEN = 5376
PRUNED_ADALN_GRID = 1025
PRUNED_ADALN_WIDTH = 8


def _block_sd(prefix: str, *, fp8: bool = False) -> dict[str, torch.Tensor]:
    qkv_dtype = torch.float8_e4m3fn if fp8 else torch.bfloat16
    return {
        f"{prefix}attn.qkv_proj.weight": torch.zeros(3 * INNER, HIDDEN, dtype=qkv_dtype),
        f"{prefix}attn.q_norm.weight": torch.zeros(HEAD_DIM, dtype=torch.bfloat16),
        f"{prefix}attn.k_norm.weight": torch.zeros(HEAD_DIM, dtype=torch.bfloat16),
        f"{prefix}attn.out_proj.weight": torch.zeros(HIDDEN, INNER, dtype=qkv_dtype),
        f"{prefix}mlp.fc1.weight": torch.zeros(2 * FFN, HIDDEN, dtype=qkv_dtype),
        f"{prefix}mlp.fc2.weight": torch.zeros(HIDDEN, FFN, dtype=qkv_dtype),
        f"{prefix}norm1.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        f"{prefix}norm2.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
    }


def _minimax_h3_sd(*, pruned: bool, num_layers: int = 2, num_refiner_layers: int = 1,
                    fp8: bool = False) -> dict[str, torch.Tensor]:
    time_embed_dim = PRUNED_ADALN_WIDTH if pruned else FULL_TIME_EMBED_DIM
    adaln_dtype = torch.float16 if pruned else torch.bfloat16

    sd: dict[str, torch.Tensor] = {
        "video_patch_proj.weight": torch.zeros(HIDDEN, VIDEO_PATCH_DIM, dtype=torch.float32),
        "video_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float32),
        "audio_patch_proj.weight": torch.zeros(HIDDEN, AUDIO_IN_CHANNELS, dtype=torch.float32),
        "audio_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float32),
        "condition_proj.weight": torch.zeros(HIDDEN, TEXT_DIM, dtype=torch.bfloat16),
        "condition_proj.bias": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "rope.inv_freq": torch.zeros(ROPE_FREQ_DIM, dtype=torch.float32),
        "final_layer.norm.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "final_layer.adaln_proj.linear.weight": torch.zeros(2 * HIDDEN, time_embed_dim, dtype=adaln_dtype),
        "final_layer.adaln_proj.linear.bias": torch.zeros(2 * HIDDEN, dtype=adaln_dtype),
        "final_layer.video_out.weight": torch.zeros(VIDEO_PATCH_DIM, HIDDEN, dtype=torch.float32),
        "final_layer.video_out.bias": torch.zeros(VIDEO_PATCH_DIM, dtype=torch.float32),
        "final_layer.audio_out.weight": torch.zeros(AUDIO_IN_CHANNELS, HIDDEN, dtype=torch.float32),
        "final_layer.audio_out.bias": torch.zeros(AUDIO_IN_CHANNELS, dtype=torch.float32),
        "token_refiner.final_norm.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
    }
    for i in range(num_layers):
        sd.update(_block_sd(f"blocks.{i}.", fp8=fp8))
        sd[f"blocks.{i}.adaln_proj.linear.weight"] = torch.zeros(6 * HIDDEN * 3, time_embed_dim, dtype=adaln_dtype)
        sd[f"blocks.{i}.adaln_proj.linear.bias"] = torch.zeros(6 * HIDDEN * 3, dtype=adaln_dtype)
    for i in range(num_refiner_layers):
        p = f"token_refiner.blocks.{i}."
        sd[f"{p}attn.qkv_proj.weight"] = torch.zeros(3 * INNER, HIDDEN, dtype=torch.bfloat16)
        sd[f"{p}attn.q_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
        sd[f"{p}attn.k_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
        sd[f"{p}attn.out_proj.weight"] = torch.zeros(HIDDEN, INNER, dtype=torch.bfloat16)
        sd[f"{p}mlp.fc1.weight"] = torch.zeros(2 * FFN, HIDDEN, dtype=torch.bfloat16)
        sd[f"{p}mlp.fc2.weight"] = torch.zeros(HIDDEN, FFN, dtype=torch.bfloat16)
        sd[f"{p}norm1.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
        sd[f"{p}norm2.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)

    if pruned:
        sd["adaln_t_table"] = torch.zeros(PRUNED_ADALN_GRID, PRUNED_ADALN_WIDTH, dtype=torch.float32)
    else:
        sd["time_embedder.proj_in.weight"] = torch.zeros(TIME_EMBED_HIDDEN, FREQ_DIM, dtype=torch.float32)
        sd["time_embedder.proj_in.bias"] = torch.zeros(TIME_EMBED_HIDDEN, dtype=torch.float32)
        sd["time_embedder.proj_out.weight"] = torch.zeros(FULL_TIME_EMBED_DIM, TIME_EMBED_HIDDEN, dtype=torch.float32)
        sd["time_embedder.proj_out.bias"] = torch.zeros(FULL_TIME_EMBED_DIM, dtype=torch.float32)
    return sd


# -- structural detection ---------------------------------------------------

def test_detect_full_checkpoint():
    c = detect_unet_config(_minimax_h3_sd(pruned=False, num_layers=3))
    assert c["image_model"] == "minimax_h3"
    assert c["hidden_size"] == HIDDEN
    assert c["num_layers"] == 3
    assert c["num_refiner_layers"] == 1
    assert c["num_attention_heads"] == HEADS
    assert c["attention_head_dim"] == HEAD_DIM
    assert c["ffn_dim"] == FFN
    assert c["in_channels"] == IN_CHANNELS
    assert c["audio_in_channels"] == AUDIO_IN_CHANNELS
    assert c["text_dim"] == TEXT_DIM
    assert c["rope_freq_dim"] == ROPE_FREQ_DIM
    assert c["patch_size"] == (1, 2, 2)
    assert c["pruned"] is False
    assert c["time_embed_dim"] == FULL_TIME_EMBED_DIM
    assert c["freq_dim"] == FREQ_DIM
    assert c["time_embed_hidden_dim"] == TIME_EMBED_HIDDEN
    assert "adaln_curve_grid" not in c


def test_detect_pruned_checkpoint():
    c = detect_unet_config(_minimax_h3_sd(pruned=True, num_layers=2, fp8=True))
    assert c["image_model"] == "minimax_h3"
    assert c["pruned"] is True
    assert c["adaln_curve_grid"] == PRUNED_ADALN_GRID
    assert c["time_embed_dim"] == PRUNED_ADALN_WIDTH
    # everything else is identical shape-derivation regardless of the AdaLN branch.
    assert c["hidden_size"] == HIDDEN
    assert c["num_attention_heads"] == HEADS
    assert c["attention_head_dim"] == HEAD_DIM
    assert c["ffn_dim"] == FFN
    assert "freq_dim" not in c
    assert "time_embed_hidden_dim" not in c


def test_pruned_fp8_qkv_shape_still_reads_correctly():
    # F8_E4M3 is never nvfp4-packed (only nvfp4 halves in-features), so the
    # plain-shape read of a fp8 qkv_proj must recover the true dims.
    c = detect_unet_config(_minimax_h3_sd(pruned=True, num_layers=1, fp8=True))
    assert c["num_attention_heads"] == HEADS
    assert c["attention_head_dim"] == HEAD_DIM


def test_non_minimax_h3_returns_none_or_other_family():
    assert detect_unet_config({"random.key": torch.zeros(1)}) is None


def test_minimax_h3_signature_does_not_collide_with_other_families():
    # video_patch_proj + audio_patch_proj is H3-only; a real Wan sd (no such
    # keys) must never be misdetected as minimax_h3.
    wan_like = {
        "head.modulation": torch.zeros(1, 2, 32),
        "head.head.weight": torch.zeros(16 * 4, 32),
        "patch_embedding.weight": torch.zeros(32, 16, 1, 2, 2),
        "text_embedding.0.weight": torch.zeros(32, 64),
        "blocks.0.ffn.0.weight": torch.zeros(8, 32),
    }
    assert detect_unet_config(wan_like)["image_model"] != "minimax_h3"


# -- registry discrimination -------------------------------------------------

def test_registry_matches_full_and_pruned_to_the_same_single_spec():
    # fl2va/ref2va (and full/pruned) are byte-identical/structurally
    # indistinguishable at the ModelSpec level -- one variant covers both.
    full_spec = match_model_spec(detect_unet_config(_minimax_h3_sd(pruned=False, num_layers=1)))
    pruned_spec = match_model_spec(detect_unet_config(_minimax_h3_sd(pruned=True, num_layers=1)))
    assert full_spec is pruned_spec
    assert full_spec.family == "minimax_h3"
    assert full_spec.variant == "h3"


def test_registry_sampling_settings():
    spec = match_model_spec(detect_unet_config(_minimax_h3_sd(pruned=True, num_layers=1)))
    assert spec.sampling_settings["shift"] == 12.0
    assert spec.sampling_settings["audio_shift"] == 3.0
    assert spec.sampling_settings["guidance"] == "none"
    assert spec.latent_format["latent_channels"] == 24
    assert spec.latent_format["format"] == "minimax_h3"


def test_registry_model_class_is_lazy_dotted_string():
    # Registering must never import the (heavy) arch module -- boot-import
    # guard territory; assert the spec stores an unresolved dotted string.
    spec = match_model_spec(detect_unet_config(_minimax_h3_sd(pruned=True, num_layers=1)))
    assert spec.model_class == "src.platform.runtime.native.arch.minimax_h3.model:MiniMaxH3Model"
    assert spec.resolve_model_class().__name__ == "MiniMaxH3Model"
