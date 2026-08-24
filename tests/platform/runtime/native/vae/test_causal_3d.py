"""Tests for the causal 3D (Wan-shaped, Qwen-Image/Krea-2) VAE."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.causal_3d import (
    LATENT_CHANNELS,
    LATENTS_MEAN,
    LATENTS_STD,
    AutoEncoderCausal3D,
    _conv3d_forward,
    _count_causal_conv3d,
)
from src.platform.runtime.native.vae.loader import _VaeSpec, load_causal3d_vae

_QWEN_IMAGE_VAE_PATH = Path("models/vae/qwen_image_vae.safetensors")
_WAN21_VAE_PATH = Path("models/vae/wan_2.1_vae.safetensors")


def _randomize_weights(module: torch.nn.Module) -> None:
    # disable_weight_init skips reset_parameters (real loading always
    # overwrites weights via load_state_dict) -- fill with finite values so
    # a self-consistency roundtrip isn't propagating uninitialized memory.
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _build_tiny() -> AutoEncoderCausal3D:
    # Architecture size is fixed (not config-parameterized, unlike AutoEncoder2D
    # -- there's exactly one known checkpoint shape), so "tiny" here means a
    # small *input*, not a small network.
    module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


def test_latent_constants_shape():
    assert LATENT_CHANNELS == 16
    assert len(LATENTS_MEAN) == LATENT_CHANNELS
    assert len(LATENTS_STD) == LATENT_CHANNELS


def test_self_consistent_state_dict_passes_load_integrity():
    module = _build_tiny()
    sd = module.state_dict()
    spec = _VaeSpec(family="vae", variant="qwen_image")
    load_into_module(module, sd, spec)  # must not raise


def test_post_load_is_safe_noop():
    module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.post_load()  # must not raise; documented no-op (no computed buffers)


def test_encode_image_decode_image_roundtrip_shape():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 32, 32) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode_image(pixels)
        recon = module.decode_image(latent)

    # 3 downsample stages (2 temporal, matching temperal_downsample) -> 8x.
    assert latent.shape == (1, 16, 4, 4)
    assert recon.shape == (1, 3, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


def test_encode_decode_video_shaped_api_single_frame_matches_image_api():
    """A single-frame (B,C,1,H,W) call through encode/decode must agree with
    the encode_image/decode_image convenience wrappers (same underlying call,
    just squeezed/unsqueezed)."""
    module = _build_tiny()
    pixels = torch.rand(1, 3, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent_video = module.encode(pixels.unsqueeze(2))
        latent_image = module.encode_image(pixels)
        recon_video = module.decode(latent_video)
        recon_image = module.decode_image(latent_image)

    assert torch.equal(latent_video.squeeze(2), latent_image)
    assert torch.equal(recon_video.squeeze(2), recon_image)


def test_multiframe_decode_has_no_uncached_equivalent_by_design():
    """Document (as a test, not just a docstring) a real architectural quirk
    found while verifying this port against ComfyUI: ``Resample.forward``'s
    ``upsample3d``/``downsample3d`` branches only perform *temporal*
    resampling when a ``feat_cache`` is supplied -- with ``feat_cache=None``
    a multi-frame call only spatially resamples, silently skipping temporal
    upsampling entirely (verified against ``comfy/ldm/wan/vae.py`` -- this is
    ComfyUI's own behavior, not a porting bug). So "chunked decode == whole
    (uncached) decode" is not a valid equivalence to test for T>1: there is no
    uncached multi-frame reference to compare against. ``feat_cache=None`` is
    only a code path for the T=1 image case (see
    ``test_encode_decode_video_shaped_api_single_frame_matches_image_api``,
    which IS a valid image-mode equivalence)."""
    module = _build_tiny()
    z = torch.randn(1, 16, 9, 4, 4)  # 9 latent frames

    with torch.no_grad():
        x = _conv3d_forward(module.conv2, z)
        uncached = module.decoder(x, feat_cache=None, feat_idx=[0])
        cached = module.decode(z)

    # Same starting T (temporal resampling skipped without a cache)...
    assert uncached.shape[2] == 9
    # ...vs the causally-correct upsampled length (ComfyUI's a*4-3 ratio for
    # two 2x temporal upsample stages), which only the cached path produces.
    assert cached.shape[2] == 9 * 4 - 3


def test_encode_chunking_requires_comfyui_exact_grouping():
    """A chunk size of 1 throughout is NOT a valid alternative to encode()'s
    (1, then 4s) grouping -- it's not an implementation choice, it's a hard
    requirement of the downsample3d layers: their time_conv is kernel=3
    stride=2 with *zero* built-in causal padding (relies entirely on a
    1-frame cache), so a chunk of only 1 new frame plus the 1-frame cache
    gives 2 total time steps, one short of the kernel-3 minimum. Documented
    here as a test (found while probing whether feat_cache is chunk-boundary
    invariant -- it is not, for this specific layer shape) so a future editor
    doesn't "simplify" encode()'s chunking scheme."""
    module = _build_tiny()
    pixels = torch.rand(1, 3, 9, 16, 16) * 2.0 - 1.0

    with torch.no_grad(), pytest.raises(RuntimeError):
        feat_cache = [None] * _count_causal_conv3d(module.decoder)
        out = None
        for i in range(pixels.shape[2]):
            idx = [0]
            chunk = module.encoder(pixels[:, :, i:i + 1], feat_cache=feat_cache, feat_idx=idx)
            out = chunk if out is None else torch.cat([out, chunk], dim=2)


def test_encode_multiframe_matches_comfyui_causal_ratio():
    """encode()'s only valid chunking (ComfyUI's own: first frame alone, then
    chunks of 4) must produce the causally-correct downsampled frame count --
    ComfyUI's downscale_ratio for this VAE: ``floor((a + 3) / 4)``."""
    module = _build_tiny()
    pixels = torch.rand(1, 3, 9, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode(pixels)
        recon = module.decode(latent)

    assert latent.shape == (1, 16, (9 + 3) // 4, 2, 2)  # 3 latent frames, 8x spatial downscale
    assert recon.shape == (1, 3, 9, 16, 16)  # decode's a*4-3 upscale ratio round-trips exactly
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _QWEN_IMAGE_VAE_PATH.exists(), reason="models/vae/qwen_image_vae.safetensors not present")
def test_real_qwen_image_vae_load_and_roundtrip():
    vae = load_causal3d_vae(_QWEN_IMAGE_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()

    # disable_weight_init keeps the checkpoint's native storage dtype (this
    # file is bf16) -- match it, the way a real caller would.
    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 3, 64, 64) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        latent = vae.encode_image(pixels)
        recon = vae.decode_image(latent)

    assert latent.shape == (1, 16, 8, 8)
    assert recon.shape == (1, 3, 64, 64)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _QWEN_IMAGE_VAE_PATH.exists(), reason="models/vae/qwen_image_vae.safetensors not present")
def test_real_qwen_image_vae_latent_normalization_roundtrips():
    """The per-channel Wan21 latent format normalization (encode: (x-mean)/std,
    decode: x*std+mean) must be its own inverse -- a smoke check that the
    LATENTS_MEAN/LATENTS_STD constants are usable the way a future generator
    pipe will use them."""
    mean = torch.tensor(LATENTS_MEAN).view(1, -1, 1, 1)
    std = torch.tensor(LATENTS_STD).view(1, -1, 1, 1)

    vae = load_causal3d_vae(_QWEN_IMAGE_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()
    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 3, 32, 32) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        raw_latent = vae.encode_image(pixels).to(torch.float32)
        normalized = (raw_latent - mean) / std
        denormalized = normalized * std + mean

    assert torch.allclose(denormalized, raw_latent, atol=1e-4)


@pytest.mark.requires_models
@pytest.mark.skipif(not _WAN21_VAE_PATH.exists(), reason="models/vae/wan_2.1_vae.safetensors not present")
def test_real_wan_2_1_vae_is_same_shape_as_qwen_image_vae():
    """models/vae/wan_2.1_vae.safetensors must load through the exact same
    AutoEncoderCausal3D class as qwen_image_vae.safetensors -- confirms
    Qwen-Image's VAE really is the Wan 2.1 VAE verbatim (module docstring),
    not just a coincidentally-similar shape, by loading the *actual* Wan
    checkpoint through it with zero missing/unexpected keys."""
    vae = load_causal3d_vae(_WAN21_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()
    assert isinstance(vae, AutoEncoderCausal3D)

    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 3, 64, 64) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        latent = vae.encode_image(pixels)
        recon = vae.decode_image(latent)

    assert latent.shape == (1, 16, 8, 8)
    assert recon.shape == (1, 3, 64, 64)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _WAN21_VAE_PATH.exists(), reason="models/vae/wan_2.1_vae.safetensors not present")
def test_real_wan_2_1_vae_multiframe_video_roundtrip():
    """T>1 roundtrip on REAL weights (not random): a 9-frame 64x64 synthetic
    clip must produce the causally-correct latent frame count
    (floor((9+3)/4) = 3, matching the tiny-config test's formula) and decode
    back to exactly 9 frames, finite throughout."""
    vae = load_causal3d_vae(_WAN21_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()

    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 3, 9, 64, 64) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        latent = vae.encode(pixels)
        recon = vae.decode(latent)

    assert latent.shape == (1, 16, 3, 8, 8)  # (9+3)//4 == 3 latent frames
    assert recon.shape == (1, 3, 9, 64, 64)  # 3*4-3 == 9, exact round-trip
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()
