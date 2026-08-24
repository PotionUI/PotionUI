"""Tests for the LTX-2/2.3 causal video VAE."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.vae_detect import detect_ltx_video_vae_config
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.loader import _VaeSpec, load_ltx_video_vae
from src.platform.runtime.native.vae.ltx_causal_video import LTXCausalVideoVAE

_LTX2_VAE_PATH = Path("models/vae/LTX2_video_vae_bf16.safetensors")
_LTX23_VAE_PATH = Path("models/vae/LTX23_video_vae_bf16.safetensors")

# A genuinely small config (this architecture is fully parameterized by its
# block list, unlike the Wan causal VAEs' fixed constants -- so "tiny" here
# really does mean a small network, not just a small input).
_TINY_CONFIG = {
    "_class_name": "CausalVideoAutoencoder",
    "dims": 3,
    "in_channels": 3,
    "out_channels": 3,
    "latent_channels": 8,
    "encoder_blocks": [
        ["res_x", {"num_layers": 1}],
        ["compress_all_res", {"multiplier": 2}],
        ["res_x", {"num_layers": 1}],
    ],
    "decoder_blocks": [
        ["res_x", {"num_layers": 1, "inject_noise": False}],
        ["compress_all", {"residual": True, "multiplier": 2}],
        ["res_x", {"num_layers": 1, "inject_noise": False}],
    ],
    "scaling_factor": 1.0,
    "norm_layer": "pixel_norm",
    "patch_size": 1,
    "latent_log_var": "uniform",
    "use_quant_conv": False,
    "causal_decoder": False,
    "timestep_conditioning": False,
    "encoder_base_channels": 16,
    "decoder_base_channels": 16,
}


def _randomize_weights(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
        for name, b in module.named_buffers():
            if b is None or not b.is_floating_point():
                continue
            if "std-of-means" in name:
                b.fill_(1.0)
            else:
                b.zero_()


def _build_tiny() -> LTXCausalVideoVAE:
    module = LTXCausalVideoVAE.from_config(_TINY_CONFIG, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


class TestDetectLtxVideoVaeConfig:
    def test_detects_embedded_config(self):
        metadata = {"config": json.dumps({"vae": _TINY_CONFIG})}
        c = detect_ltx_video_vae_config(metadata)
        assert c is not None
        assert c["_class_name"] == "CausalVideoAutoencoder"
        assert c["latent_channels"] == 8

    def test_no_config_key_returns_none(self):
        assert detect_ltx_video_vae_config({}) is None

    def test_malformed_json_returns_none(self):
        assert detect_ltx_video_vae_config({"config": "{not json"}) is None

    def test_non_vae_config_returns_none(self):
        metadata = {"config": json.dumps({"transformer": {"_class_name": "AVTransformer3DModel"}})}
        assert detect_ltx_video_vae_config(metadata) is None

    def test_wrong_class_name_returns_none(self):
        metadata = {"config": json.dumps({"vae": {"_class_name": "SomeOtherVAE"}})}
        assert detect_ltx_video_vae_config(metadata) is None


def test_timestep_conditioning_raises_unsupported():
    from src.platform.runtime.native.errors import NativeEngineUnsupportedError

    config = dict(_TINY_CONFIG, timestep_conditioning=True)
    with pytest.raises(NativeEngineUnsupportedError, match="timestep_conditioning"):
        LTXCausalVideoVAE.from_config(config, disable_weight_init)


def test_self_consistent_state_dict_passes_load_integrity():
    module = _build_tiny()
    sd = module.state_dict()
    spec = _VaeSpec(family="vae", variant="ltx_causal_video")
    load_into_module(module, sd, spec)  # must not raise


def test_post_load_is_safe_noop():
    module = LTXCausalVideoVAE.from_config(_TINY_CONFIG, disable_weight_init)
    module.post_load()


def test_encode_rejects_invalid_frame_count():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 4, 8, 8)  # 4 is not 1 + 8*k
    with pytest.raises(ValueError, match="1 \\+ 8\\*k"):
        module.encode(pixels)


def test_encode_decode_image_roundtrip_shape():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 1, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent = module.encode(pixels)
        recon = module.decode(latent)

    # one compress_all_res(mult2)/compress_all(mult2) stage -> 2x spatial+temporal downscale.
    assert latent.shape == (1, 8, 1, 8, 8)
    assert recon.shape == (1, 3, 1, 16, 16)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


def test_encode_decode_multiframe_roundtrip_shape():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 9, 16, 16) * 2.0 - 1.0  # 9 = 1 + 8*1

    with torch.no_grad():
        latent = module.encode(pixels)
        recon = module.decode(latent)

    assert latent.shape[2] > 1  # temporal compression happened, still >1 latent frame
    assert recon.shape == (1, 3, 9, 16, 16)
    assert torch.isfinite(latent).all()
    assert torch.isfinite(recon).all()


def test_reset_cache_clears_thread_local_state():
    module = _build_tiny()
    pixels = torch.rand(1, 3, 1, 16, 16) * 2.0 - 1.0
    with torch.no_grad():
        module.encode(pixels)  # encode() resets its own cache internally (see finally block)
    for m in module.modules():
        if hasattr(m, "temporal_cache_state"):
            assert m.temporal_cache_state == {}


def test_repeated_encode_decode_calls_are_independent():
    """Calling encode/decode twice in a row (same thread) must not leak state
    from the first call into the second -- the whole point of resetting the
    thread-local cache after each top-level call."""
    module = _build_tiny()
    pixels_a = torch.rand(1, 3, 1, 16, 16) * 2.0 - 1.0
    pixels_b = torch.rand(1, 3, 1, 16, 16) * 2.0 - 1.0

    with torch.no_grad():
        latent_a1 = module.encode(pixels_a)
        latent_b = module.encode(pixels_b)
        latent_a2 = module.encode(pixels_a)

    assert torch.equal(latent_a1, latent_a2)
    assert not torch.equal(latent_a1, latent_b)


def test_accepts_preloaded_sd_and_metadata_without_reading_file():
    """The engine's ``_load_vae`` reads+slices the all-in-one checkpoint once
    and hands the result straight to this loader (see ``engine.py``'s
    ``_load_vae``/``_load_vae_module``) -- pass an obviously-nonexistent path
    to prove no second file read happens."""
    module = _build_tiny()
    sd = module.state_dict()
    metadata = {"config": json.dumps({"vae": _TINY_CONFIG})}

    loaded = load_ltx_video_vae(
        Path("does/not/exist.safetensors"), disable_weight_init, device="cpu",
        sd=sd, metadata=metadata,
    )
    assert loaded.latent_channels == 8


def test_preloaded_sd_still_strips_vae_prefix():
    """A caller passing an *unsliced* all-in-one ``sd`` (``vae.``-prefixed)
    still loads correctly -- the strip-prefix fallback inside this loader
    runs regardless of whether ``sd`` came from a fresh read or a caller."""
    module = _build_tiny()
    sd = {f"vae.{k}": v for k, v in module.state_dict().items()}
    metadata = {"config": json.dumps({"vae": _TINY_CONFIG})}

    loaded = load_ltx_video_vae(
        Path("does/not/exist.safetensors"), disable_weight_init, device="cpu",
        sd=sd, metadata=metadata,
    )
    assert loaded.latent_channels == 8


class TestPatchifyUnpatchifyOrdering:
    """Regression tests for channel ordering in _patchify/_unpatchify.

    The channel decomposition must use (c, p, r, q) ordering -- NOT (c, p, q, r)
    -- to match the ltx-core / diffusers convention.  A swap of q and r produces
    visually identical shapes but garbles the pixel layout, causing uniform
    grain/texture artifacts in every decoded frame.
    """

    def test_roundtrip_identity(self):
        """patchify -> unpatchify must recover the original tensor."""
        from src.platform.runtime.native.vae.ltx_causal_video import _patchify, _unpatchify

        x = torch.randn(1, 3, 9, 32, 32)
        patched = _patchify(x, patch_size_hw=4, patch_size_t=1)
        assert patched.shape == (1, 48, 9, 8, 8)
        recovered = _unpatchify(patched, patch_size_hw=4, patch_size_t=1)
        assert torch.equal(recovered, x), "patchify -> unpatchify roundtrip failed"

    def test_matches_einops_reference(self):
        """Native _unpatchify must match einops 'b (c p r q) f h w -> ...'."""
        from einops import rearrange
        from src.platform.runtime.native.vae.ltx_causal_video import _unpatchify

        torch.manual_seed(42)
        x = torch.randn(1, 48, 9, 8, 8)
        native = _unpatchify(x, patch_size_hw=4, patch_size_t=1)
        reference = rearrange(
            x,
            "b (c p r q) f h w -> b c (f p) (h q) (w r)",
            p=1, q=4, r=4,
        )
        assert torch.equal(native, reference), (
            f"unpatchify ordering mismatch: max|D|={torch.abs(native - reference).max():.3e}"
        )

    def test_patchify_matches_einops_reference(self):
        """Native _patchify must match einops 'b c (f p) (h q) (w r) -> ...'."""
        from einops import rearrange
        from src.platform.runtime.native.vae.ltx_causal_video import _patchify

        torch.manual_seed(42)
        x = torch.randn(1, 3, 9, 32, 32)
        native = _patchify(x, patch_size_hw=4, patch_size_t=1)
        reference = rearrange(
            x,
            "b c (f p) (h q) (w r) -> b (c p r q) f h w",
            p=1, q=4, r=4,
        )
        assert torch.equal(native, reference), (
            f"patchify ordering mismatch: max|D|={torch.abs(native - reference).max():.3e}"
        )

    def test_matches_diffusers_unpatchify(self):
        """Native _unpatchify must match the diffusers LTX2 decoder's unpatchify."""
        from src.platform.runtime.native.vae.ltx_causal_video import _unpatchify

        torch.manual_seed(42)
        x = torch.randn(1, 48, 9, 8, 8)
        native = _unpatchify(x, patch_size_hw=4, patch_size_t=1)

        # Diffusers unpatchify (from autoencoder_kl_ltx2.py LTX2VideoDecoder3d):
        p, p_t = 4, 1
        b, nc, nf, h, w = x.shape
        diffusers = x.reshape(b, -1, p_t, p, p, nf, h, w)
        diffusers = diffusers.permute(0, 1, 5, 2, 6, 4, 7, 3).flatten(6, 7).flatten(4, 5).flatten(2, 3)

        assert torch.equal(native, diffusers), (
            f"native vs diffusers unpatchify mismatch: max|D|={torch.abs(native - diffusers).max():.3e}"
        )


@pytest.mark.requires_models
@pytest.mark.skipif(not _LTX2_VAE_PATH.exists(), reason="models/vae/LTX2_video_vae_bf16.safetensors not present")
class TestRealLtx2VideoVae:
    def test_load_and_image_roundtrip(self):
        vae = load_ltx_video_vae(_LTX2_VAE_PATH, disable_weight_init, device="cpu")
        vae.eval()
        assert vae.latent_channels == 128

        weight_dtype = next(vae.parameters()).dtype
        pixels = (torch.rand(1, 3, 1, 32, 32) * 2.0 - 1.0).to(weight_dtype)
        with torch.no_grad():
            latent = vae.encode(pixels)
            recon = vae.decode(latent)

        # patch_size(4) * 3 encoder downsample stages(2x each) = 32x spatial.
        assert latent.shape == (1, 128, 1, 1, 1)
        assert recon.shape == (1, 3, 1, 32, 32)
        assert torch.isfinite(latent).all()
        assert torch.isfinite(recon).all()

    def test_multiframe_video_roundtrip(self):
        vae = load_ltx_video_vae(_LTX2_VAE_PATH, disable_weight_init, device="cpu")
        vae.eval()
        weight_dtype = next(vae.parameters()).dtype
        pixels = (torch.rand(1, 3, 9, 64, 64) * 2.0 - 1.0).to(weight_dtype)

        with torch.no_grad():
            latent = vae.encode(pixels)
            recon = vae.decode(latent)

        assert latent.shape == (1, 128, 2, 2, 2)
        assert recon.shape == (1, 3, 9, 64, 64)
        assert torch.isfinite(latent).all()
        assert torch.isfinite(recon).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _LTX23_VAE_PATH.exists(), reason="models/vae/LTX23_video_vae_bf16.safetensors not present")
class TestRealLtx23VideoVae:
    """LTX23's decoder uses compress_space/compress_time (non-residual) block
    types LTX2's decoder never exercises -- a distinct code path from LTX2."""

    def test_load_and_image_roundtrip(self):
        vae = load_ltx_video_vae(_LTX23_VAE_PATH, disable_weight_init, device="cpu")
        vae.eval()
        assert vae.latent_channels == 128

        weight_dtype = next(vae.parameters()).dtype
        pixels = (torch.rand(1, 3, 1, 32, 32) * 2.0 - 1.0).to(weight_dtype)
        with torch.no_grad():
            latent = vae.encode(pixels)
            recon = vae.decode(latent)

        assert latent.shape == (1, 128, 1, 1, 1)
        assert recon.shape == (1, 3, 1, 32, 32)
        assert torch.isfinite(latent).all()
        assert torch.isfinite(recon).all()

    def test_multiframe_video_roundtrip(self):
        vae = load_ltx_video_vae(_LTX23_VAE_PATH, disable_weight_init, device="cpu")
        vae.eval()
        weight_dtype = next(vae.parameters()).dtype
        pixels = (torch.rand(1, 3, 9, 64, 64) * 2.0 - 1.0).to(weight_dtype)

        with torch.no_grad():
            latent = vae.encode(pixels)
            recon = vae.decode(latent)

        assert latent.shape == (1, 128, 2, 2, 2)
        assert recon.shape == (1, 3, 9, 64, 64)
        assert torch.isfinite(latent).all()
        assert torch.isfinite(recon).all()
