"""Tests for DiT (Flux1/Flux2) detection."""

from __future__ import annotations

import torch

from src.platform.runtime.native.detect.unet_detect import detect_unet_config

from .conftest import flux1_sd, flux2_sd, seedvr2_dit_sd


def test_detect_flux2():
    c = detect_unet_config(flux2_sd(hidden=256, depth=8, single=24))
    assert c["image_model"] == "flux2"
    assert c["hidden_size"] == 256
    assert c["num_heads"] == 256 // 128       # sum(axes_dim) == 128
    assert c["depth"] == 8
    assert c["depth_single_blocks"] == 24
    assert c["in_channels"] == 128            # 128 // patch_size(1)**2
    assert c["context_in_dim"] == 768
    assert c["axes_dim"] == [32, 32, 32, 32]
    assert c["theta"] == 2000
    assert c["patch_size"] == 1
    assert c["qkv_bias"] is False
    assert c["guidance_embed"] is False


def test_detect_flux1_with_guidance():
    c = detect_unet_config(flux1_sd(hidden=384, depth=19, single=38, guidance=True))
    assert c["image_model"] == "flux"
    assert c["num_heads"] == 384 // 128
    assert c["depth"] == 19
    assert c["depth_single_blocks"] == 38
    assert c["in_channels"] == 16             # 64 // patch_size(2)**2
    assert c["axes_dim"] == [16, 56, 56]
    assert c["theta"] == 10000
    assert c["patch_size"] == 2
    assert c["qkv_bias"] is True
    assert c["guidance_embed"] is True


def test_detect_flux1_without_guidance():
    c = detect_unet_config(flux1_sd(guidance=False))
    assert c["image_model"] == "flux"
    assert c["guidance_embed"] is False


def test_flux2_takes_priority_over_flux1_signature():
    # flux2 has the flux1 signature key too; the modulation key must win.
    sd = flux2_sd()
    assert "double_blocks.0.img_attn.norm.key_norm.scale" in sd
    assert detect_unet_config(sd)["image_model"] == "flux2"


def test_non_flux_returns_none():
    assert detect_unet_config({"random.key": torch.zeros(1)}) is None


def test_chroma_like_without_img_in_returns_none():
    # double-block signature but no img_in -> not our flux (guards Chroma).
    sd = {"double_blocks.0.img_attn.norm.key_norm.scale": torch.zeros(16)}
    assert detect_unet_config(sd) is None


def test_detect_seedvr2():
    c = detect_unet_config(seedvr2_dit_sd(
        vid_dim=64, heads=4, head_dim=16, num_layers=4, mm_layers=2,
        vid_in_channels=33, vid_out_channels=16, txt_in_dim=5120, mlp_hidden=96,
    ))
    assert c["image_model"] == "seedvr2"
    assert c["vid_dim"] == 64
    assert c["heads"] == 4                     # (4*16*3) // (3*16)
    assert c["head_dim"] == 16
    assert c["num_layers"] == 4
    assert c["mm_layers"] == 2                 # only the .vid blocks
    assert c["vid_in_channels"] == 33          # 132 // 4
    assert c["vid_out_channels"] == 16         # 64 // 4
    assert c["txt_in_dim"] == 5120
    assert c["emb_dim"] == 6 * 64
    assert c["mlp_hidden"] == 96


def test_detect_seedvr2_real_3b_shapes():
    # Real 3B checkpoint's shapes (verified against the safetensors header).
    c = detect_unet_config(seedvr2_dit_sd(
        vid_dim=2560, heads=20, head_dim=128, num_layers=32, mm_layers=10,
        vid_in_channels=33, vid_out_channels=16, txt_in_dim=5120,
        emb_dim=15360, mlp_hidden=6912,
    ))
    assert (c["vid_dim"], c["heads"], c["head_dim"]) == (2560, 20, 128)
    assert (c["num_layers"], c["mm_layers"]) == (32, 10)
    assert (c["emb_dim"], c["mlp_hidden"]) == (15360, 6912)


def test_seedvr2_needs_both_signature_keys():
    # vid_in.proj alone (no ada.vid) must NOT match — the compound guards a
    # future family that might reuse a vid_in embed.
    sd = seedvr2_dit_sd()
    del sd["blocks.0.ada.vid.attn_shift"]
    assert detect_unet_config(sd) is None
