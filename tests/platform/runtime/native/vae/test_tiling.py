"""Tests for tiled encode/decode blending and the tile-size heuristic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
import torch

from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.vae import ae_2d
from src.platform.runtime.native.vae.ae_2d import AutoEncoder2D
from src.platform.runtime.native.vae import causal_3d
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D
from src.platform.runtime.native.vae.tiling import (
    VAE_SPATIAL_DOWNSCALE,
    auto_tile_size,
    causal3d_decode_overlap,
    causal3d_decode_tile,
    smaller_decode_tile,
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
    with mock.patch.object(ae_2d, "_CH", 32):
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
    with mock.patch.object(causal_3d, "_DIM", 8):
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
    latent = torch.randn(1, 16, 3, 8, 8)  # 3 latent frames

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode_causal3d(vae, latent, tile_size=4, overlap=1)

    assert whole.shape[2] == tiled.shape[2]  # same temporal extent
    assert tiled.shape == whole.shape
    assert tiled.shape[-2:] == (8 * VAE_SPATIAL_DOWNSCALE, 8 * VAE_SPATIAL_DOWNSCALE)
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


class _Upscale16RGBAVAE:
    def decode(self, latent):
        b, c, t, h, w = latent.shape
        rgb = latent[:, :4].reshape(b * t, 4, h, w) if c >= 4 else latent.reshape(b * t, c, h, w)
        up = torch.nn.functional.interpolate(rgb, scale_factor=16, mode="bilinear", align_corners=False)
        return up.reshape(b, 4, t, h * 16, w * 16)


def test_tiled_decode_causal3d_honours_a_16x_rgba_vae():
    torch.manual_seed(0)
    vae = _Upscale16RGBAVAE()
    latent = torch.randn(1, 64, 1, 40, 40)

    with torch.no_grad():
        whole = vae.decode(latent)
        tiled = tiled_decode_causal3d(vae, latent, tile_size=32, overlap=8, scale=16)

    assert tiled.shape == whole.shape == (1, 4, 1, 640, 640)
    assert torch.isfinite(tiled).all()
    interior = (slice(None), slice(None), slice(None), slice(64, -64), slice(64, -64))
    assert torch.allclose(tiled[interior], whole[interior], atol=5e-2)


def test_a_latent_that_fits_one_tile_is_still_moved_to_the_accumulation_device():
    moved = []
    pixels = mock.Mock()
    pixels.to = lambda device: moved.append(device) or "on the host"
    vae = SimpleNamespace(decode=lambda latent: pixels)

    out = tiled_decode_causal3d(vae, torch.zeros(1, 16, 2, 4, 4), tile_size=8, overlap=2, accumulate_device="cpu")

    assert out == "on the host"
    assert moved == ["cpu"]


def test_the_engine_sizes_its_first_causal3d_tile_with_its_own_constants():
    from src.platform.runtime.native import engine as engine_mod

    seen = []
    gen = engine_mod.NativeGenerator.__new__(engine_mod.NativeGenerator)
    gen.vae = SimpleNamespace(module=None, compute_dtype=torch.float32, move_to=lambda device: None, offload=lambda: None)
    gen.spec = SimpleNamespace(latent_format={})
    gen._is_self_normalizing_vae = lambda: False
    gen._spatial_downscale = lambda: 8
    gen._resident = lambda component: True
    gen._to_uint8 = lambda pixels: pixels

    sizer = mock.Mock(side_effect=lambda hw, free, **kw: seen.append((hw, free, kw)) or 16)
    with mock.patch.object(engine_mod, "free_vram_gb", return_value=6.0), \
            mock.patch.object(engine_mod, "causal3d_decode_tile", sizer), \
            mock.patch.object(engine_mod, "tiled_decode_causal3d", return_value="pixels"):
        assert gen._decode_causal3d_tiled(torch.zeros(1, 16, 1, 8, 12), "cpu") == "pixels"

    assert seen == [((8, 12), 6.0, {
        "decode_mb_per_latent_px": engine_mod._CAUSAL3D_DECODE_MB_PER_LATENT_PX,
        "vram_fraction": engine_mod._DECODE_TILE_VRAM_FRACTION,
        "min_tile": engine_mod._MIN_DECODE_TILE_LATENT,
    })]


@pytest.mark.parametrize(
    ("latent_hw", "free", "expected"),
    [
        ((78, 186), None, 88),
        ((78, 186), 64.0, 88),
        ((78, 186), 4.0, 40),
        ((78, 186), 0.5, 16),
        ((10, 12), None, 16),
    ],
)
def test_the_decode_tile_is_half_the_long_axis_capped_by_free_vram(latent_hw, free, expected):
    assert causal3d_decode_tile(latent_hw, free) == expected


@pytest.mark.parametrize(("tile", "overlap", "smaller"), [(16, 8, 16), (40, 8, 16), (64, 8, 32), (128, 16, 64)])
def test_the_decode_tile_overlap_and_shrink(tile, overlap, smaller):
    assert causal3d_decode_overlap(tile) == overlap
    assert smaller_decode_tile(tile) == smaller


@pytest.mark.parametrize(("tile_size", "overlap"), [(8, 4), (64, 8)])
def test_tiled_decode_causal3d_can_accumulate_on_another_device(tile_size, overlap):
    torch.manual_seed(0)
    vae = _tiny_causal3d_vae()
    latent = torch.randn(1, 16, 2, 4, 12)

    with torch.no_grad():
        here = tiled_decode_causal3d(vae, latent, tile_size=tile_size, overlap=overlap)
        there = tiled_decode_causal3d(vae, latent, tile_size=tile_size, overlap=overlap, accumulate_device="cpu")

    assert there.device.type == "cpu"
    assert torch.equal(here, there)
