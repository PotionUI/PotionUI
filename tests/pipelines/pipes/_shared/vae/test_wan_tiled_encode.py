"""Tests for the shared Wan i2v-concat VAE-encode OOM ladder
(``make_wan_vae_encode``): untiled pass-through, and shrink-on-OOM tiled
fallback -- moved here from generator/img2vid_wan22 when the encode was
hoisted so generator/chain_video_wan22 could share it."""

from __future__ import annotations

from types import SimpleNamespace

import torch

from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_wan_vae_encode


def test_vae_encode_falls_back_to_tiled_on_oom():
    """make_wan_vae_encode returns the untiled result normally, and on a CUDA
    OOM retries via the spatial-tiling path (temporal chunking untouched)."""
    from vendor.gpl.comfyui.ops import disable_weight_init
    from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D

    vae = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    vae.eval()
    with torch.no_grad():
        for p in vae.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)

    real_encode = vae.encode
    calls = {"n": 0}

    def flaky_encode(px):
        calls["n"] += 1
        if calls["n"] == 1:  # the untiled whole-frame attempt OOMs once
            raise torch.cuda.OutOfMemoryError("mock OOM")
        return real_encode(px)

    vae.encode = flaky_encode
    encode = make_wan_vae_encode(vae, 64, 64)
    pixels = torch.rand(1, 3, 1, 64, 64) * 2.0 - 1.0

    with torch.no_grad():
        out = encode(pixels)

    assert calls["n"] == 2                 # one OOM, one successful retry
    assert out.shape == (1, 16, 1, 8, 8)   # correct latent shape from the fallback
    assert torch.isfinite(out).all()


def test_vae_encode_passes_through_when_no_oom():
    """No OOM -> the wrapper is a transparent pass-through (single encode call)."""
    calls = {"n": 0}

    def ok_encode(px):
        calls["n"] += 1
        return torch.zeros(1, 16, 1, px.shape[3] // 8, px.shape[4] // 8)

    vae = SimpleNamespace(encode=ok_encode)
    encode = make_wan_vae_encode(vae, 64, 64)
    out = encode(torch.rand(1, 3, 1, 64, 64))

    assert calls["n"] == 1
    assert out.shape == (1, 16, 1, 8, 8)
