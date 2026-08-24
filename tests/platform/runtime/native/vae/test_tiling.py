"""Tests for tiled encode/decode blending and the tile-size heuristic."""

from __future__ import annotations

import torch

from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.ae_2d import AutoEncoder2D
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D
from src.platform.runtime.native.vae.tiling import (
    VAE_SPATIAL_DOWNSCALE,
    auto_tile_size,
    tiled_decode,
    tiled_decode_causal3d,
    tiled_encode,
    tiled_encode_causal3d,
)


def _tiny_flux_vae() -> AutoEncoder2D:
    config = {
        "vae_type": "flux_ae",
        "latent_channels": 16,
        "in_channels": 3,
        "out_channels": 3,
        "key_layout": "ldm",
        "has_quant_conv": False,
        "has_batchnorm": False,
    }
    module = AutoEncoder2D.from_config(config, disable_weight_init)
    module.eval()
    # `disable_weight_init` skips reset_parameters -- fill with finite values
    # so forward passes don't just propagate uninitialized memory.
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


def test_tiled_decode_matches_whole_decode_within_tolerance():
    vae = _tiny_flux_vae()
    latent = torch.randn(1, 16, 16, 16)  # -> 128x128 pixels whole

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode(vae, latent, tile_size=8, overlap=2)

    assert tiled.shape == whole.shape
    # GroupNorm/attention are computed over each tile's own receptive field,
    # not the whole image, so tiled output isn't bit-identical -- but the
    # linear seam blend should keep it close.
    assert torch.allclose(tiled, whole, atol=0.5)


def test_tiled_decode_single_tile_is_exact_when_no_split_needed():
    vae = _tiny_flux_vae()
    latent = torch.randn(1, 16, 8, 8)

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode(vae, latent, tile_size=64, overlap=8)

    assert torch.equal(tiled, whole)


def test_tiled_encode_shape():
    vae = _tiny_flux_vae()
    pixels = torch.rand(1, 3, 128, 128) * 2.0 - 1.0

    with torch.no_grad():
        latent = tiled_encode(vae, pixels, tile_size=32, overlap=8)

    assert latent.shape == (1, 16, 128 // VAE_SPATIAL_DOWNSCALE, 128 // VAE_SPATIAL_DOWNSCALE)
    assert torch.isfinite(latent).all()


def _tiny_causal3d_vae() -> AutoEncoderCausal3D:
    # The causal-3D arch size is fixed (one known checkpoint shape); "tiny"
    # means a small *input*, not a small network (mirrors test_causal_3d.py).
    module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.eval()
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


def test_tiled_encode_causal3d_matches_whole_encode_within_tolerance():
    torch.manual_seed(0)
    vae = _tiny_causal3d_vae()
    pixels = torch.rand(1, 3, 1, 64, 64) * 2.0 - 1.0  # single frame, 8x8 latent whole

    with torch.no_grad():
        whole = vae.encode(pixels)
        tiled = tiled_encode_causal3d(vae, pixels, tile_size=32, overlap=8)

    assert tiled.shape == whole.shape
    # The middle-block spatial attention is computed over each tile's own
    # receptive field, so tiled output isn't bit-identical -- but the linear
    # seam blend keeps it close (observed max diff ~1e-3 against a ~5e-2 value
    # magnitude). Interior (away from the outer edge) is at least as tight.
    assert torch.isfinite(tiled).all()
    interior = (slice(None), slice(None), slice(None), slice(1, -1), slice(1, -1))
    assert torch.allclose(tiled[interior], whole[interior], atol=5e-3)
    assert torch.allclose(tiled, whole, atol=1e-2)


def test_tiled_encode_causal3d_single_tile_is_exact_when_no_split_needed():
    vae = _tiny_causal3d_vae()
    pixels = torch.rand(1, 3, 1, 32, 32) * 2.0 - 1.0

    with torch.no_grad():
        whole = vae.encode(pixels)
        tiled = tiled_encode_causal3d(vae, pixels, tile_size=64, overlap=8)

    assert torch.equal(tiled, whole)


def test_tiled_encode_causal3d_multiframe_shape_and_temporal_axis_whole():
    """A T>1 clip tiles spatially only: the latent frame count matches the
    untiled encode's causal downsample ratio ((T+3)//4), proving the temporal
    chunking is passed through each tile whole."""
    torch.manual_seed(0)
    vae = _tiny_causal3d_vae()
    pixels = torch.rand(1, 3, 9, 64, 64) * 2.0 - 1.0  # 9 frames -> (9+3)//4 = 3 latent frames

    with torch.no_grad():
        whole = vae.encode(pixels)
        tiled = tiled_encode_causal3d(vae, pixels, tile_size=32, overlap=8)

    assert whole.shape == (1, 16, 3, 8, 8)
    assert tiled.shape == whole.shape
    assert torch.isfinite(tiled).all()
    assert torch.allclose(tiled, whole, atol=1e-2)


def test_tiled_decode_causal3d_matches_whole_decode_within_tolerance():
    torch.manual_seed(0)
    vae = _tiny_causal3d_vae()
    latent = torch.randn(1, 16, 1, 16, 16)  # single frame, 128x128 pixels whole

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode_causal3d(vae, latent, tile_size=8, overlap=2)

    assert tiled.shape == whole.shape
    # The middle-block spatial attention is computed over each tile's own
    # receptive field, so tiled output isn't bit-identical -- but the linear
    # seam blend keeps it close (mirrors the encode twin). Interior is tighter.
    assert torch.isfinite(tiled).all()
    interior = (slice(None), slice(None), slice(None), slice(8, -8), slice(8, -8))
    assert torch.allclose(tiled[interior], whole[interior], atol=5e-3)
    assert torch.allclose(tiled, whole, atol=5e-2)


def test_tiled_decode_causal3d_single_tile_is_exact_when_no_split_needed():
    vae = _tiny_causal3d_vae()
    latent = torch.randn(1, 16, 1, 8, 8)

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode_causal3d(vae, latent, tile_size=64, overlap=8)

    assert torch.equal(tiled, whole)


def test_tiled_decode_causal3d_multiframe_shape_and_temporal_axis_whole():
    """A T>1 latent tiles spatially only: the decoded pixel frame count matches
    the untiled decode (one output frame per latent frame), proving the temporal
    chunking is passed through each tile whole (feat_cache untouched)."""
    torch.manual_seed(0)
    vae = _tiny_causal3d_vae()
    latent = torch.randn(1, 16, 3, 16, 16)  # 3 latent frames

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode_causal3d(vae, latent, tile_size=8, overlap=2)

    assert whole.shape[2] == tiled.shape[2]  # same temporal extent
    assert tiled.shape == whole.shape
    assert tiled.shape[-2:] == (16 * VAE_SPATIAL_DOWNSCALE, 16 * VAE_SPATIAL_DOWNSCALE)
    assert torch.isfinite(tiled).all()


def test_auto_tile_size_none_when_plenty_of_vram():
    assert auto_tile_size(vram_free_gb=64.0, latent_hw=(64, 64)) is None


def test_auto_tile_size_none_when_unknown_vram():
    assert auto_tile_size(vram_free_gb=None, latent_hw=(64, 64)) is None


def test_auto_tile_size_returns_tile_when_vram_constrained():
    tile = auto_tile_size(vram_free_gb=1.0, latent_hw=(256, 256))
    assert tile is not None
    assert 8 <= tile <= 256
    assert tile % 8 == 0


def test_auto_tile_size_grows_with_more_vram():
    small = auto_tile_size(vram_free_gb=0.5, latent_hw=(512, 512))
    large = auto_tile_size(vram_free_gb=4.0, latent_hw=(512, 512))
    assert small is not None and large is not None
    assert large > small
