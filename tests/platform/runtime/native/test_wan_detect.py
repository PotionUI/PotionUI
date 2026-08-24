"""Tests for Wan 2.1 / 2.2 DiT detection + registry discrimination.

Synthetic state dicts carry only the keys/shapes detection reads (no real
weights), which is enough to exercise the t2v / i2v / ti2v-5B discrimination and
the vace / camera / s2v / humo / animate rejects.
"""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.errors import NativeEngineUnsupportedError


def wan_sd(dim, in_dim, out_dim, *, layers=2, text_dim=4096, ffn=64, i2v=False,
           flf=None, ref_conv=None, extra=None):
    patch = (1, 2, 2)
    sd = {
        "head.modulation": torch.zeros(1, 2, dim),
        "head.head.weight": torch.zeros(out_dim * math.prod(patch), dim),
        "patch_embedding.weight": torch.zeros(dim, in_dim, *patch),
        "text_embedding.0.weight": torch.zeros(dim, text_dim),
    }
    for i in range(layers):
        sd[f"blocks.{i}.ffn.0.weight"] = torch.zeros(ffn, dim)
    if i2v:
        sd["img_emb.proj.0.bias"] = torch.zeros(1280)
    if flf is not None:
        sd["img_emb.emb_pos"] = torch.zeros(1, flf, 1280)
    if ref_conv is not None:
        sd["ref_conv.weight"] = torch.zeros(dim, ref_conv, 2, 2)
    if extra:
        sd[extra] = torch.zeros(4)
    return sd


# -- structural detection --------------------------------------------------

def test_detect_t2v_14b():
    c = detect_unet_config(wan_sd(5120, 16, 16, layers=40))
    assert c["image_model"] == "wan2.1"
    assert c["model_type"] == "t2v"
    assert c["dim"] == 5120
    assert c["num_heads"] == 40          # dim // 128
    assert c["in_dim"] == 16
    assert c["out_dim"] == 16
    assert c["num_layers"] == 40
    assert c["patch_size"] == (1, 2, 2)


def test_detect_i2v_by_img_emb():
    c = detect_unet_config(wan_sd(5120, 36, 16, i2v=True))
    assert c["model_type"] == "i2v"
    assert c["in_dim"] == 36


def test_detect_5b_by_out_dim():
    c = detect_unet_config(wan_sd(3072, 48, 48, layers=30))
    assert c["model_type"] == "t2v"     # 5B has no img_emb (concat conditioning)
    assert c["out_dim"] == 48
    assert c["num_heads"] == 24          # 3072 // 128


def test_detect_flf_and_ref_conv_optional_fields():
    c = detect_unet_config(wan_sd(5120, 36, 16, i2v=True, flf=257, ref_conv=16))
    assert c["flf_pos_embed_token_number"] == 257
    assert c["in_dim_ref_conv"] == 16


# -- registry discrimination ----------------------------------------------

def test_registry_matches_t2v_14b():
    spec = match_model_spec(detect_unet_config(wan_sd(5120, 16, 16)))
    assert spec.variant == "wan_t2v_14b"
    assert spec.vae_target == "wan21"
    assert spec.sampling_settings["shift"] == 8.0
    assert spec.sampling_settings["guidance"] == "cfg"
    assert spec.sampling_settings["expert_boundary"] == 0.875
    assert spec.latent_format["latent_channels"] == 16


def test_registry_matches_classic_i2v_14b():
    # Classic Wan 2.1 i2v: img_emb (CLIP-vision) -> model_type i2v.
    spec = match_model_spec(detect_unet_config(wan_sd(5120, 36, 16, i2v=True)))
    assert spec.variant == "wan_i2v_14b"
    assert "clip_vision" in spec.clip_targets
    assert spec.sampling_settings["expert_boundary"] == 0.900


def test_registry_matches_concat_i2v_wan22():
    # Wan 2.2 i2v: reference frame channel-concatenated (in_dim 36), NO img_emb,
    # so detection reports t2v (t2v cross-attn arch) — the local Dasiwa/Enhanced
    # checkpoints. Distinguished from plain t2v 14B by in_dim (36 vs 16).
    c = detect_unet_config(wan_sd(5120, 36, 16))  # no img_emb
    assert c["model_type"] == "t2v"
    assert c["in_dim"] == 36
    spec = match_model_spec(c)
    assert spec.variant == "wan22_i2v_14b"
    assert spec.clip_targets == ["umt5"]  # concat, not CLIP-vision
    assert spec.vae_target == "wan21"


def test_registry_matches_ti2v_5b():
    spec = match_model_spec(detect_unet_config(wan_sd(3072, 48, 48)))
    assert spec.variant == "wan_ti2v_5b"
    assert spec.vae_target == "wan22"
    assert spec.latent_format["latent_channels"] == 48
    assert "expert_boundary" not in spec.sampling_settings  # dense 5B, no MoE


def test_umt5_is_the_wan_text_encoder_target():
    spec = match_model_spec(detect_unet_config(wan_sd(5120, 16, 16)))
    assert spec.clip_targets[0] == "umt5"


# -- rejects ---------------------------------------------------------------

@pytest.mark.parametrize("key,name", [
    ("vace_patch_embedding.weight", "vace"),
    ("control_adapter.conv.weight", "camera"),
    ("casual_audio_encoder.encoder.final_linear.weight", "s2v"),
    ("audio_proj.audio_proj_glob_1.layer.bias", "humo"),
    ("face_adapter.fuser_blocks.0.k_norm.weight", "animate"),
])
def test_rejects_extension_variants(key, name):
    with pytest.raises(NativeEngineUnsupportedError):
        detect_unet_config(wan_sd(5120, 16, 16, extra=key))


def test_funcontrol_in_dim_48_does_not_masquerade_as_14b():
    # A t2v-shaped checkpoint with a non-standard in_dim (e.g. FunControl 48)
    # must NOT match the 14B t2v spec (in_dim pinned 16) nor the 5B (out_dim 48).
    c = detect_unet_config(wan_sd(5120, 48, 16))
    with pytest.raises(NativeEngineUnsupportedError):
        match_model_spec(c)


# -- no cross-family collisions -------------------------------------------

def test_wan_signature_does_not_collide_with_other_families():
    # head.modulation is Wan-only; a Wan sd must not detect as flux/krea2/qwen.
    c = detect_unet_config(wan_sd(5120, 16, 16))
    assert c["image_model"] == "wan2.1"
