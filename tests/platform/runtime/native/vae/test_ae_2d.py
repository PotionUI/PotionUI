"""Tiny-config smoke tests + real-checkpoint load/roundtrip for AutoEncoder2D."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.ae_2d import AutoEncoder2D
from src.platform.runtime.native.vae.loader import _VaeSpec, load_vae

_FLUX_AE_PATH = Path("models/vae/ae.sft")
_FLUX2_VAE_PATH = Path("models/vae/flux2-vae.safetensors")


def _randomize_weights(module: torch.nn.Module) -> None:
    # `disable_weight_init` deliberately skips `reset_parameters` (real
    # loading always overwrites weights via `load_state_dict`), so a freshly
    # constructed module's tensors are raw `torch.empty` memory -- not a fair
    # stand-in for "real" weights in a test. Fill them with finite values.
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
        for name, b in module.named_buffers():
            if b is not None and b.is_floating_point() and "running_var" in name:
                b.fill_(1.0)
            elif b is not None and b.is_floating_point():
                b.zero_()


def _tiny_config(*, vae_type: str, latent: int, has_quant_conv: bool, has_batchnorm: bool) -> dict:
    return {
        "vae_type": vae_type,
        "latent_channels": latent,
        "in_channels": 3,
        "out_channels": 3,
        "key_layout": "diffusers" if has_quant_conv else "ldm",
        "has_quant_conv": has_quant_conv,
        "has_batchnorm": has_batchnorm,
    }


@pytest.mark.parametrize(
    "vae_type,latent,has_quant_conv,has_batchnorm",
    [
        ("flux_ae", 16, False, False),
        ("flux2_ae", 32, True, True),
    ],
)
def test_tiny_config_encode_decode_roundtrip_shape(vae_type, latent, has_quant_conv, has_batchnorm):
    config = _tiny_config(vae_type=vae_type, latent=latent, has_quant_conv=has_quant_conv, has_batchnorm=has_batchnorm)
    module = AutoEncoder2D.from_config(config, disable_weight_init)
    module.eval()
    _randomize_weights(module)

    # self-consistent state dict (random init) must pass the same load-integrity
    # gate the real loader uses -- proves the module's own keys are internally
    # consistent (no accidental duplicate/missing submodule wiring).
    sd = module.state_dict()
    spec = _VaeSpec(family="vae", variant=vae_type)
    load_into_module(module, sd, spec)

    pixels = torch.randn(1, 3, 32, 32)
    with torch.no_grad():
        z = module.encode(pixels)
        out = module.decode(z)

    downscale = 8 * (2 if has_batchnorm else 1)
    expected_latent_channels = latent * (4 if has_batchnorm else 1)
    assert z.shape == (1, expected_latent_channels, 32 // downscale, 32 // downscale)
    assert out.shape == (1, 3, 32, 32)
    assert torch.isfinite(out).all()


def test_post_load_is_safe_noop():
    config = _tiny_config(vae_type="flux_ae", latent=16, has_quant_conv=False, has_batchnorm=False)
    module = AutoEncoder2D.from_config(config, disable_weight_init)
    module.post_load()  # must not raise; documented no-op for this arch


@pytest.mark.requires_models
@pytest.mark.skipif(not _FLUX_AE_PATH.exists(), reason="models/vae/ae.sft not present")
def test_real_flux_ae_load_and_roundtrip():
    vae = load_vae(_FLUX_AE_PATH, disable_weight_init, device="cpu")
    vae.eval()
    assert vae.vae_type == "flux_ae"
    assert vae.z_channels == 16

    pixels = torch.rand(1, 3, 64, 64) * 2.0 - 1.0
    with torch.no_grad():
        latent = vae.encode(pixels)
        recon = vae.decode(latent)

    assert latent.shape == (1, 16, 8, 8)
    assert recon.shape == (1, 3, 64, 64)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _FLUX2_VAE_PATH.exists(), reason="models/vae/flux2-vae.safetensors not present")
def test_real_flux2_vae_load_and_roundtrip():
    vae = load_vae(_FLUX2_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()
    assert vae.vae_type == "flux2_ae"
    assert vae.z_channels == 32

    pixels = torch.rand(1, 3, 64, 64) * 2.0 - 1.0
    with torch.no_grad():
        latent = vae.encode(pixels)
        recon = vae.decode(latent)

    # 8x conv downscale * 2x batchnorm pixel-unshuffle = 16x; 32*4=128 packed channels.
    assert latent.shape == (1, 128, 4, 4)
    assert recon.shape == (1, 3, 64, 64)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()
