"""Tests for the Qwen-Image-2.1-shaped causal 3D VAE (64ch, RGBA, no patchify --
same ``AutoEncoderCausal3D_2_2`` module class as Wan 2.2, different config)."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.detect.vae_detect import (
    detect_causal3d_v2_vae_config,
    detect_qwen_image21_vae_config,
)
from src.platform.runtime.native.vae.causal_3d_v2 import AutoEncoderCausal3D_2_2
from src.platform.runtime.native.vae.loader import _VaeSpec, load_qwen_image21_vae

_QWEN_IMAGE21_VAE_PATH = Path("models/vae/qwen_image_2.1_vae_bf16.safetensors")

# The real Comfy-Org ddconfig (verified against the header dump), not a
# shrunk stand-in -- detection derives dec_dim from decoder.conv1's shape
# divided by the FIXED (only-ever-shipped) dim_mult[-1]=8, so a state dict
# built with a different dim_mult topology would make dec_dim detection
# wrong. Matches test_causal_3d_v2.py's own precedent (its "_build_tiny"
# uses Wan 2.2's real dims too, just a small test image).
_QWEN_IMAGE21_CONFIG = {
    "vae_type": "qwen_image21",
    "latent_channels": 64,
    "in_channels": 4,
    "out_channels": 4,
    "dim": 96,
    "dec_dim": 144,
    "dim_mult": (1, 2, 4, 8, 8),
    "num_res_blocks": 2,
    "temporal_downsample": (False, True, True, True),
    "patch_size": 1,
    "temporal_kernel": 1,
}


def _randomize_weights(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _build_qwen21() -> AutoEncoderCausal3D_2_2:
    module = AutoEncoderCausal3D_2_2.from_config(_QWEN_IMAGE21_CONFIG, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


def _build_wan22() -> AutoEncoderCausal3D_2_2:
    module = AutoEncoderCausal3D_2_2.from_config({}, disable_weight_init)  # Wan 2.2 defaults
    module.eval()
    _randomize_weights(module)
    return module


def test_from_config_builds_rgba_no_patchify_module():
    module = _build_qwen21()
    assert module.patch_size == 1
    assert module.image_channels == 4
    assert module.pad_channel_value == 1.0
    assert module.encoder.conv1.weight.shape == (96, 4, 1, 3, 3)
    assert module.decoder.head[-1].weight.shape == (4, 144, 1, 3, 3)
    assert module.conv2.weight.shape == (64, 64, 1, 1, 1)


def test_post_load_is_safe_noop():
    module = _build_qwen21()
    module.post_load()


def test_self_consistent_state_dict_passes_load_integrity():
    module = _build_qwen21()
    sd = module.state_dict()
    spec = _VaeSpec(family="vae", variant="qwen_image21")
    load_into_module(module, sd, spec)  # must not raise


def test_detects_qwen_image21_from_synthetic_state_dict():
    module = _build_qwen21()
    sd = module.state_dict()

    config = detect_qwen_image21_vae_config(sd)
    assert config is not None
    assert config["vae_type"] == "qwen_image21"
    assert config["latent_channels"] == 64
    assert config["in_channels"] == 4
    assert config["out_channels"] == 4
    assert config["dim"] == 96
    assert config["dec_dim"] == 144
    assert config["temporal_kernel"] == 1
    assert config["patch_size"] == 1

    # And it must NOT also match as Wan 2.2 -- the two detectors are
    # mutually exclusive on the same nested-upsamples signature.
    assert detect_causal3d_v2_vae_config(sd) is None


def test_wan22_detection_still_wins_for_a_wan_shaped_dict():
    module = _build_wan22()
    sd = module.state_dict()

    assert detect_qwen_image21_vae_config(sd) is None
    config = detect_causal3d_v2_vae_config(sd)
    assert config is not None
    assert config["vae_type"] == "wan2.2"
    assert config["latent_channels"] == 48


def test_non_wan22_shaped_dict_returns_none():
    assert detect_qwen_image21_vae_config({"foo": torch.zeros(1)}) is None


def test_encode_image_decode_image_roundtrip_shape():
    module = _build_qwen21()
    pixels = torch.rand(1, 4, 32, 32) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode_image(pixels)
        recon = module.decode_image(latent)

    assert latent.shape == (1, 64, 2, 2)
    assert recon.shape == (1, 4, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


def test_encode_pads_rgb_input_with_opaque_alpha():
    module = _build_qwen21()
    rgb = torch.rand(1, 3, 32, 32) * 2.0 - 1.0
    rgba = torch.cat([rgb, torch.ones(1, 1, 32, 32)], dim=1)

    with torch.no_grad():
        latent_from_rgb = module.encode_image(rgb)
        latent_from_rgba = module.encode_image(rgba)

    # padding the missing alpha channel with 1.0 must be exactly what
    # encode_image(rgba) already does -- not merely "close".
    assert torch.equal(latent_from_rgb, latent_from_rgba)


def test_decode_rgb_only_drops_alpha_channel():
    module = _build_qwen21()
    pixels = torch.rand(1, 4, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode_image(pixels)
        rgba = module.decode_image(latent)
        rgb = module.decode_image(latent, rgb_only=True)

    assert rgba.shape == (1, 4, 16, 16)
    assert rgb.shape == (1, 3, 16, 16)
    assert torch.equal(rgb, rgba[:, :3])


def test_encode_decode_video_shaped_api_single_frame_matches_image_api():
    module = _build_qwen21()
    pixels = torch.rand(1, 4, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent_video = module.encode(pixels.unsqueeze(2))
        latent_image = module.encode_image(pixels)
        recon_video = module.decode(latent_video)
        recon_image = module.decode_image(latent_image)

    assert torch.equal(latent_video.squeeze(2), latent_image)
    assert torch.equal(recon_video.squeeze(2), recon_image)


@pytest.mark.requires_models
@pytest.mark.skipif(not _QWEN_IMAGE21_VAE_PATH.exists(), reason="models/vae/qwen_image_2.1_vae_bf16.safetensors not present")
def test_real_qwen_image21_vae_load_and_image_roundtrip():
    vae = load_qwen_image21_vae(_QWEN_IMAGE21_VAE_PATH, disable_weight_init, device="cpu")
    vae.eval()

    weight_dtype = next(vae.parameters()).dtype
    pixels = (torch.rand(1, 4, 32, 32) * 2.0 - 1.0).to(weight_dtype)
    with torch.no_grad():
        latent = vae.encode_image(pixels)
        recon = vae.decode_image(latent)

    assert latent.shape == (1, 64, 2, 2)
    assert recon.shape == (1, 4, 32, 32)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()
