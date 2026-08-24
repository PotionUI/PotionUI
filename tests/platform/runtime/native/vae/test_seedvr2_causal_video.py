"""Tests for the SeedVR2 causal video VAE port.

Tiny-input smoke tests (fixed architecture, small *input*) plus a
real-checkpoint strict-load + round-trip PSNR test guarded on the file's
presence -- mirroring test_causal_3d.py's structure.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.base import load_into_module
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.loader import _VaeSpec
from src.platform.runtime.native.vae.seedvr2_causal_video import (
    LATENT_CHANNELS,
    SCALING_FACTOR,
    SeedVR2CausalVideoVAE,
)

_SEEDVR2_VAE_PATH = Path("models/vae/ema_vae_fp16.safetensors")


def _randomize_weights(module: torch.nn.Module) -> None:
    # disable_weight_init skips reset_parameters (real loading always overwrites
    # via load_state_dict) -- fill with finite values so a self-consistency
    # roundtrip isn't propagating uninitialized memory.
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _build_tiny() -> SeedVR2CausalVideoVAE:
    # Fixed architecture (one known checkpoint shape) -- "tiny" is a small input.
    module = SeedVR2CausalVideoVAE.from_config({}, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


def test_latent_constants():
    assert LATENT_CHANNELS == 16
    assert SCALING_FACTOR == pytest.approx(0.9152)


def test_post_load_is_safe_noop():
    module = SeedVR2CausalVideoVAE.from_config({}, disable_weight_init)
    module.post_load()  # must not raise; documented no-op (no computed buffers)


def test_self_consistent_state_dict_passes_load_integrity():
    module = _build_tiny()
    sd = module.state_dict()
    spec = _VaeSpec(family="vae", variant="seedvr2_causal_video")
    load_into_module(module, sd, spec)  # must not raise


def test_encode_image_decode_image_roundtrip_shape():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 32, 32) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode_image(pixels)
        recon = module.decode_image(latent)

    # 8x spatial downsample (32 -> 4), 16 latent channels.
    assert latent.shape == (1, 16, 4, 4)
    assert recon.shape == (1, 3, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


def test_video_and_image_apis_agree_single_frame():
    """A single-frame (B,C,1,H,W) call must agree with the 4D image path
    (same underlying call, just squeezed/unsqueezed)."""
    module = _build_tiny()
    pixels = torch.rand(1, 3, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent_video = module.encode(pixels.unsqueeze(2))
        latent_image = module.encode(pixels)
        recon_video = module.decode(latent_video)
        recon_image = module.decode(latent_image)

    assert latent_video.shape == (1, 16, 1, 2, 2)
    assert torch.equal(latent_video.squeeze(2), latent_image)
    assert torch.equal(recon_video.squeeze(2), recon_image)


def _psnr(a: torch.Tensor, b: torch.Tensor) -> float:
    a01 = (a.clamp(-1, 1) + 1) / 2
    b01 = (b.clamp(-1, 1) + 1) / 2
    mse = F.mse_loss(a01, b01).item()
    return 10 * math.log10(1.0 / mse) if mse > 0 else float("inf")


@pytest.mark.requires_models
@pytest.mark.skipif(not _SEEDVR2_VAE_PATH.exists(), reason="models/vae/ema_vae_fp16.safetensors not present")
def test_real_seedvr2_vae_strict_load_and_roundtrip():
    from safetensors.torch import load_file

    module = SeedVR2CausalVideoVAE.from_config({}, disable_weight_init)
    module.eval()

    sd = {k: v.float() for k, v in load_file(str(_SEEDVR2_VAE_PATH)).items()}
    result = module.load_state_dict(sd, strict=False)
    # Exact key parity: the real checkpoint must load with zero missing/unexpected.
    assert list(result.missing_keys) == []
    assert list(result.unexpected_keys) == []

    module = module.float()
    x = torch.rand(1, 3, 128, 128) * 2.0 - 1.0
    with torch.no_grad():
        latent = module.encode(x)
        recon = module.decode(latent)

    assert latent.shape == (1, 16, 16, 16)
    assert recon.shape == (1, 3, 128, 128)
    # On real weights (not random) a genuine VAE round-trips well above noise.
    # (30.7 dB on a real 256px photo; a random-content 128px tensor is harder
    #  but still comfortably positive/finite -- guard against structural bugs.)
    assert torch.isfinite(recon).all()
    assert _psnr(recon, x) > 10.0
