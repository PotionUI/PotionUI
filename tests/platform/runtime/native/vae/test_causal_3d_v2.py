"""Tests for the Wan-2.2-shaped causal 3D VAE (48ch, patchified)."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.causal_3d_v2 import (
    LATENT_CHANNELS,
    LATENT_SCALE_FACTOR,
    AutoEncoderCausal3D_2_2,
    _patchify,
    _unpatchify,
)
from src.platform.runtime.native.vae.loader import _VaeSpec, load_causal3d_v2_vae

_WAN22_VAE_PATH = Path("models/vae/wan2.2_vae.safetensors")


def _randomize_weights(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _build_tiny() -> AutoEncoderCausal3D_2_2:
    module = AutoEncoderCausal3D_2_2.from_config({}, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


def test_latent_constants():
    assert LATENT_CHANNELS == 48
    assert LATENT_SCALE_FACTOR == 1.0


def test_patchify_unpatchify_roundtrip():
    x = torch.randn(1, 3, 2, 8, 8)
    p = _patchify(x, patch_size=2)
    assert p.shape == (1, 12, 2, 4, 4)
    back = _unpatchify(p, patch_size=2)
    assert torch.equal(back, x)


def test_patchify_matches_manual_space_to_depth():
    # sanity: patchify groups 2x2 spatial blocks into channels, not an
    # arbitrary reshape -- verify against a hand-built 2x2 block example.
    x = torch.arange(1 * 1 * 1 * 4 * 4).float().view(1, 1, 1, 4, 4)
    p = _patchify(x, patch_size=2)
    assert p.shape == (1, 4, 1, 2, 2)
    # top-left 2x2 block of x should map to the 4 channel values at (0,0).
    block = x[0, 0, 0, 0:2, 0:2].flatten()
    assert torch.equal(p[0, :, 0, 0, 0].sort().values, block.sort().values)


def test_self_consistent_state_dict_passes_load_integrity():
    module = _build_tiny()
    sd = module.state_dict()
    spec = _VaeSpec(family="vae", variant="wan2.2")
    load_into_module(module, sd, spec)  # must not raise


def test_post_load_is_safe_noop():
    module = AutoEncoderCausal3D_2_2.from_config({}, disable_weight_init)
    module.post_load()


def test_encode_image_decode_image_roundtrip_shape():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 32, 32) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode_image(pixels)
        recon = module.decode_image(latent)

    # patchify(2x2) + 3 downsample stages (2 temporal + spatial via avg
    # shortcut) -> 16x spatial downscale overall (ComfyUI's spacial_downscale_ratio=16).
    assert latent.shape == (1, 48, 2, 2)
    assert recon.shape == (1, 3, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


def test_encode_decode_video_shaped_api_single_frame_matches_image_api():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent_video = module.encode(pixels.unsqueeze(2))
        latent_image = module.encode_image(pixels)
        recon_video = module.decode(latent_video)
        recon_image = module.decode_image(latent_image)

    assert torch.equal(latent_video.squeeze(2), latent_image)
    assert torch.equal(recon_video.squeeze(2), recon_image)


@pytest.mark.requires_models
@pytest.mark.skipif(not _WAN22_VAE_PATH.exists(), reason="models/vae/wan2.2_vae.safetensors not present")
def test_real_wan22_vae_load_and_image_roundtrip():
    vae = load_causal3d_v2_vae(_WAN22_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()

    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 3, 32, 32) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        latent = vae.encode_image(pixels)
        recon = vae.decode_image(latent)

    assert latent.shape == (1, 48, 2, 2)
    assert recon.shape == (1, 3, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _WAN22_VAE_PATH.exists(), reason="models/vae/wan2.2_vae.safetensors not present")
def test_real_wan22_vae_multiframe_video_roundtrip():
    """T>1 roundtrip on REAL weights: same causal frame-count formula as
    Wan 2.1 (floor((9+3)/4) = 3 latent frames -> 3*4-3 = 9 output frames),
    16x spatial downscale (ComfyUI's Wan22.spacial_downscale_ratio)."""
    vae = load_causal3d_v2_vae(_WAN22_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()

    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 3, 9, 32, 32) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        latent = vae.encode(pixels)
        recon = vae.decode(latent)

    assert latent.shape == (1, 48, 3, 2, 2)
    assert recon.shape == (1, 3, 9, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()
