"""Tests for the LTX multi-scale latent upsampler.

The standalone checkpoint (e.g.
``ltx-2.3-spatial-upscaler-x1.5-1.0.safetensors``) is not shipped locally
either, so this stays construction + detection + load-integrity + tiny
forward smoke test only, no real-file coverage -- but detection
(``detect_ltx_latent_upsampler_config``) and loading
(``load_ltx_latent_upsampler``) are now real, exercised end-to-end against
self-built state dicts / embedded metadata (see ``TestDetectLtx
LatentUpsamplerConfig`` / ``TestLoadLtxLatentUpsampler`` below).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.vae_detect import detect_ltx_latent_upsampler_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.loader import load_ltx_latent_upsampler
from src.platform.runtime.native.vae.ltx_latent_upsampler import (
    LTXLatentUpsampler,
    rational_resample_out_size,
)

# num_groups=32 in GroupNorm -- mid_channels must be a multiple of 32.
_TINY_CONFIG = {
    "in_channels": 8,
    "mid_channels": 32,
    "num_blocks_per_stage": 1,
    "dims": 3,
}


class _Spec:
    def key_is_expected_missing(self, key: str) -> bool:
        return False

    def key_is_expected_unexpected(self, key: str) -> bool:
        return False


def _randomize(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


class TestConstructionAndLoadIntegrity:
    def test_default_config_builds(self):
        LTXLatentUpsampler.from_config({}, disable_weight_init)

    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = LTXLatentUpsampler.from_config({**_TINY_CONFIG, "spatial_upsample": True}, disable_weight_init)
        _randomize(module)
        sd = module.state_dict()

        module2 = LTXLatentUpsampler.from_config({**_TINY_CONFIG, "spatial_upsample": True}, disable_weight_init)
        load_into_module(module2, sd, _Spec())

    def test_post_load_is_safe_noop(self):
        module = LTXLatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        module.post_load()

    def test_neither_spatial_nor_temporal_raises(self):
        import pytest

        from src.platform.runtime.native.errors import NativeEngineUnsupportedError

        with pytest.raises(NativeEngineUnsupportedError):
            LTXLatentUpsampler.from_config(
                {**_TINY_CONFIG, "spatial_upsample": False, "temporal_upsample": False}, disable_weight_init
            )

    def test_unsupported_spatial_scale_raises(self):
        import pytest

        from src.platform.runtime.native.errors import NativeEngineUnsupportedError

        with pytest.raises(NativeEngineUnsupportedError):
            LTXLatentUpsampler.from_config(
                {**_TINY_CONFIG, "rational_resampler": True, "spatial_scale": 3.0}, disable_weight_init
            )


class TestTinyForward:
    def _latents(self) -> torch.Tensor:
        return torch.randn(1, _TINY_CONFIG["in_channels"], 3, 4, 4)

    def test_spatial_upsample_2x(self):
        module = LTXLatentUpsampler.from_config({**_TINY_CONFIG, "spatial_upsample": True}, disable_weight_init)
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents())
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 3, 8, 8)
        assert torch.isfinite(out).all()

    def test_spatial_and_temporal_upsample(self):
        module = LTXLatentUpsampler.from_config(
            {**_TINY_CONFIG, "spatial_upsample": True, "temporal_upsample": True}, disable_weight_init
        )
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents())
        assert out.shape[0] == 1
        assert out.shape[1] == _TINY_CONFIG["in_channels"]
        assert torch.isfinite(out).all()

    def test_temporal_upsample_only(self):
        module = LTXLatentUpsampler.from_config(
            {**_TINY_CONFIG, "spatial_upsample": False, "temporal_upsample": True}, disable_weight_init
        )
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents())
        assert out.shape[3:] == (4, 4)  # spatial dims untouched
        assert torch.isfinite(out).all()

    def test_rational_resampler_1_5x(self):
        module = LTXLatentUpsampler.from_config(
            {**_TINY_CONFIG, "spatial_upsample": True, "rational_resampler": True, "spatial_scale": 1.5},
            disable_weight_init,
        )
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents())
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 3, 6, 6)
        assert torch.isfinite(out).all()

    def test_dims_2_matches_dims_3_shape(self):
        module = LTXLatentUpsampler.from_config({**_TINY_CONFIG, "dims": 2}, disable_weight_init)
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents())
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 3, 8, 8)
        assert torch.isfinite(out).all()


# The checkpoint's embedded config IS the LatentUpsampler
# config directly (no "vae"/"upsampler" nesting) -- see
# ``detect_ltx_latent_upsampler_config``'s docstring and
# ``vae_detect.py`` module docstring #5.
_UPSCALER_CONFIG = {
    "_class_name": "LatentUpsampler",
    "in_channels": 8,
    "mid_channels": 32,
    "num_blocks_per_stage": 1,
    "dims": 3,
    "spatial_upsample": True,
    "temporal_upsample": False,
    "spatial_scale": 1.5,
    "rational_resampler": True,
}


class TestDetectLtxLatentUpsamplerConfig:
    def test_detects_embedded_config(self):
        metadata = {"config": json.dumps(_UPSCALER_CONFIG)}
        c = detect_ltx_latent_upsampler_config(metadata)
        assert c is not None
        assert c["_class_name"] == "LatentUpsampler"
        assert c["spatial_scale"] == 1.5

    def test_no_config_key_returns_none(self):
        assert detect_ltx_latent_upsampler_config({}) is None

    def test_malformed_json_returns_none(self):
        assert detect_ltx_latent_upsampler_config({"config": "{not json"}) is None

    def test_wrong_class_name_returns_none(self):
        metadata = {"config": json.dumps({"_class_name": "CausalVideoAutoencoder"})}
        assert detect_ltx_latent_upsampler_config(metadata) is None

    def test_video_vae_config_not_mistaken_for_upsampler(self):
        # The video VAE's config nests under "vae" and has its own
        # _class_name -- must not false-positive against the flat upsampler
        # config shape.
        metadata = {"config": json.dumps({"vae": {"_class_name": "CausalVideoAutoencoder"}})}
        assert detect_ltx_latent_upsampler_config(metadata) is None


def _build_upscaler(**overrides) -> tuple[LTXLatentUpsampler, dict]:
    config = {**_UPSCALER_CONFIG, **overrides}
    module = LTXLatentUpsampler.from_config(config, disable_weight_init)
    _randomize(module)
    return module, config


class TestLoadLtxLatentUpsampler:
    """End-to-end detect -> load -> forward against a self-built state dict
    (no real checkpoint ships this model yet -- see module docstring)."""

    def test_round_trip_1_5x(self):
        module, config = _build_upscaler()
        sd = module.state_dict()
        metadata = {"config": json.dumps(config)}

        loaded = load_ltx_latent_upsampler(
            "fake-ltx-2.3-spatial-upscaler-x1.5.safetensors",
            disable_weight_init, sd=sd, metadata=metadata,
        )
        assert loaded.spatial_scale == 1.5
        assert loaded.rational_resampler is True

        latent = torch.randn(1, _UPSCALER_CONFIG["in_channels"], 5, 8, 8)
        with torch.no_grad():
            out = loaded(latent)
        # Spatial dims scale by 1.5x; frame count (temporal) is preserved.
        assert out.shape == (1, _UPSCALER_CONFIG["in_channels"], 5, 12, 12)
        assert torch.isfinite(out).all()

    def test_round_trip_2_0x(self):
        module, config = _build_upscaler(spatial_scale=2.0, rational_resampler=False)
        sd = module.state_dict()
        metadata = {"config": json.dumps(config)}

        loaded = load_ltx_latent_upsampler(
            "fake-ltx-2.3-spatial-upscaler-x2.safetensors",
            disable_weight_init, sd=sd, metadata=metadata,
        )
        assert loaded.spatial_scale == 2.0
        assert loaded.rational_resampler is False

        latent = torch.randn(1, _UPSCALER_CONFIG["in_channels"], 5, 8, 8)
        with torch.no_grad():
            out = loaded(latent)
        # Spatial dims double; frame count (temporal) is preserved.
        assert out.shape == (1, _UPSCALER_CONFIG["in_channels"], 5, 16, 16)
        assert torch.isfinite(out).all()

    def test_round_trip_1_5x_output_matches_rational_resample_out_size(self):
        """`rational_resample_out_size` (the closed-form helper the
        preflight geometry check reuses) must predict the SAME latent axes
        the real module forward produces -- not just for this test's own
        8x8 input, but the specific asymmetric shape the maintainer's live
        repro crashed on."""
        module, config = _build_upscaler()
        sd = module.state_dict()
        metadata = {"config": json.dumps(config)}
        loaded = load_ltx_latent_upsampler(
            "fake-ltx-2.3-spatial-upscaler-x1.5.safetensors",
            disable_weight_init, sd=sd, metadata=metadata,
        )
        for h, w in [(8, 8), (5, 9), (17, 24)]:
            latent = torch.randn(1, _UPSCALER_CONFIG["in_channels"], 3, h, w)
            with torch.no_grad():
                out = loaded(latent)
            assert out.shape[-2] == rational_resample_out_size(h, 1.5)
            assert out.shape[-1] == rational_resample_out_size(w, 1.5)

    def test_round_trip_2_0x_output_matches_rational_resample_out_size(self):
        module, config = _build_upscaler(spatial_scale=2.0, rational_resampler=False)
        sd = module.state_dict()
        metadata = {"config": json.dumps(config)}
        loaded = load_ltx_latent_upsampler(
            "fake-ltx-2.3-spatial-upscaler-x2.safetensors",
            disable_weight_init, sd=sd, metadata=metadata,
        )
        for h, w in [(8, 8), (5, 9)]:
            latent = torch.randn(1, _UPSCALER_CONFIG["in_channels"], 3, h, w)
            with torch.no_grad():
                out = loaded(latent)
            assert out.shape[-2] == rational_resample_out_size(h, 2.0)
            assert out.shape[-1] == rational_resample_out_size(w, 2.0)

    def test_no_embedded_config_raises(self):
        module, _config = _build_upscaler()
        sd = module.state_dict()
        with pytest.raises(NativeEngineUnsupportedError, match="no embedded LatentUpsampler config"):
            load_ltx_latent_upsampler("fake.safetensors", disable_weight_init, sd=sd, metadata={})

    def test_missing_signature_key_raises(self):
        module, config = _build_upscaler()
        sd = module.state_dict()
        del sd["post_upsample_res_blocks.0.conv2.bias"]
        metadata = {"config": json.dumps(config)}
        with pytest.raises(NativeEngineUnsupportedError, match="post_upsample_res_blocks.0.conv2.bias"):
            load_ltx_latent_upsampler("fake.safetensors", disable_weight_init, sd=sd, metadata=metadata)

    def test_reads_file_when_sd_not_preloaded(self, tmp_path, monkeypatch):
        module, config = _build_upscaler()
        sd = module.state_dict()
        metadata = {"config": json.dumps(config)}

        def _fake_load_torch_file(path, device="cpu"):
            return sd, metadata

        monkeypatch.setattr(
            "src.platform.runtime.native.vae.loader.load_torch_file", _fake_load_torch_file,
        )
        loaded = load_ltx_latent_upsampler(tmp_path / "fake.safetensors", disable_weight_init)
        assert loaded.spatial_scale == 1.5


# The LTX-2.5 temporal x2 checkpoint, not present locally (see the model-files
# table in ai/DFR_FACTS_SPEC.md). Same skip treatment as the other LTX
# real-file checks.
_REAL_TEMPORAL_PATH = Path("models/upscalers/ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors")

_TEMPORAL_CONFIG = {
    **_UPSCALER_CONFIG,
    "spatial_upsample": False,
    "temporal_upsample": True,
    "rational_resampler": False,
}


class TestTemporalFrameMapping:
    """``T -> 2T - 1``: the temporal branch pixel-shuffles the frame axis by 2
    and then drops the first frame. Verified line-for-line against diffusers
    ``pipelines/ltx2/latent_upsampler.py:272-274`` and Lightricks
    ``ltx_core/model/upsampler/model.py:109-113``, which agree.
    """

    def _module(self, **overrides) -> LTXLatentUpsampler:
        module = LTXLatentUpsampler.from_config({**_TEMPORAL_CONFIG, **overrides}, disable_weight_init)
        _randomize(module)
        return module

    @pytest.mark.parametrize("frames_in,frames_out", [(1, 1), (2, 3), (3, 5), (5, 9), (9, 17), (16, 31)])
    def test_temporal_only_frame_count(self, frames_in, frames_out):
        module = self._module()
        latent = torch.randn(1, _TEMPORAL_CONFIG["in_channels"], frames_in, 4, 4)
        with torch.no_grad():
            out = module(latent)
        assert out.shape[2] == frames_out
        assert out.shape[3:] == (4, 4)
        assert torch.isfinite(out).all()

    def test_the_drop_is_of_the_FIRST_frame_not_the_last(self):
        """Dropping the wrong end still yields 2T-1 frames, so a count-only
        assertion cannot tell the two apart. Compare against the undropped
        upsampler output directly."""
        module = self._module()
        latent = torch.randn(1, _TEMPORAL_CONFIG["in_channels"], 3, 4, 4)
        with torch.no_grad():
            x = module.initial_activation(module.initial_norm(module.initial_conv(latent)))
            for block in module.res_blocks:
                x = block(x)
            undropped = module.upsampler(x)
            out = module(latent)

        with torch.no_grad():
            post = undropped[:, :, 1:, :, :]
            for block in module.post_upsample_res_blocks:
                post = block(post)
            expected = module.final_conv(post)
        assert torch.allclose(out, expected, atol=1e-5)
        assert undropped.shape[2] == 6

    @pytest.mark.parametrize("frames_in,frames_out", [(1, 1), (3, 5), (5, 9)])
    def test_spatial_and_temporal_upsamples_both_axes(self, frames_in, frames_out):
        """The reference's ``if self.temporal_upsample`` branch covers BOTH
        temporal-only and spatial+temporal (the latter's upsampler is a
        PixelShuffleND(3)), so the frame drop applies to both."""
        module = LTXLatentUpsampler.from_config(
            {**_UPSCALER_CONFIG, "spatial_upsample": True, "temporal_upsample": True,
             "rational_resampler": False},
            disable_weight_init,
        )
        _randomize(module)
        latent = torch.randn(1, _UPSCALER_CONFIG["in_channels"], frames_in, 4, 4)
        with torch.no_grad():
            out = module(latent)
        assert out.shape[2] == frames_out
        assert out.shape[3:] == (8, 8)

    def test_spatial_only_leaves_the_frame_count_alone(self):
        module = LTXLatentUpsampler.from_config(_UPSCALER_CONFIG, disable_weight_init)
        _randomize(module)
        latent = torch.randn(1, _UPSCALER_CONFIG["in_channels"], 5, 8, 8)
        with torch.no_grad():
            out = module(latent)
        assert out.shape[2] == 5

    def test_matches_the_pipe_geometry_helper(self):
        """``geometry.temporal_upsample_out_frames`` is what the pipe and any
        preflight check reason with; it must predict the real forward."""
        from src.pipelines.pipes.latent_upscaler.ltx.geometry import temporal_upsample_out_frames

        module = self._module()
        for frames_in in (1, 2, 4, 7):
            latent = torch.randn(1, _TEMPORAL_CONFIG["in_channels"], frames_in, 4, 4)
            with torch.no_grad():
                out = module(latent)
            assert out.shape[2] == temporal_upsample_out_frames(frames_in)

    def test_temporal_with_dims_2_is_refused_at_construction(self):
        """Neither reference defines it: both build the temporal upsampler out
        of Conv3d and route dims=2 through a 4D per-frame forward."""
        with pytest.raises(NativeEngineUnsupportedError, match="temporal_upsample requires dims=3"):
            LTXLatentUpsampler.from_config({**_TEMPORAL_CONFIG, "dims": 2}, disable_weight_init)


class TestLoadTemporalUpsampler:
    def test_round_trip_declares_temporal(self):
        module = LTXLatentUpsampler.from_config(_TEMPORAL_CONFIG, disable_weight_init)
        _randomize(module)
        metadata = {"config": json.dumps(_TEMPORAL_CONFIG)}

        loaded = load_ltx_latent_upsampler(
            "fake-ltx-2.5-latent-temporal-upscaler-x2.safetensors",
            disable_weight_init, sd=module.state_dict(), metadata=metadata,
        )
        assert loaded.temporal_upsample is True
        assert loaded.spatial_upsample is False

        latent = torch.randn(1, _TEMPORAL_CONFIG["in_channels"], 5, 8, 8)
        with torch.no_grad():
            out = loaded(latent)
        assert out.shape == (1, _TEMPORAL_CONFIG["in_channels"], 9, 8, 8)

    def test_a_spatial_checkpoint_loads_without_declaring_temporal(self):
        module = LTXLatentUpsampler.from_config(_UPSCALER_CONFIG, disable_weight_init)
        _randomize(module)
        loaded = load_ltx_latent_upsampler(
            "fake-ltx-2.3-spatial-upscaler-x1.5.safetensors",
            disable_weight_init, sd=module.state_dict(),
            metadata={"config": json.dumps(_UPSCALER_CONFIG)},
        )
        assert loaded.temporal_upsample is False


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_TEMPORAL_PATH.exists(), reason="real LTX-2.5 temporal upscaler not present")
def test_real_temporal_checkpoint_declares_temporal_and_loads():
    from safetensors import safe_open

    from src.platform.runtime.native.detect.vae_detect import detect_ltx_latent_upsampler_config

    with safe_open(_REAL_TEMPORAL_PATH, framework="pt") as f:
        metadata = f.metadata() or {}
        shapes = {k: tuple(f.get_slice(k).get_shape()) for k in f.keys()}

    config = detect_ltx_latent_upsampler_config(metadata)
    assert config is not None, "the real temporal upscaler carries no embedded LatentUpsampler config"
    assert config.get("temporal_upsample") is True

    module = LTXLatentUpsampler.from_config(config, disable_weight_init)
    expected = {k: tuple(v.shape) for k, v in module.state_dict().items()}
    assert set(shapes) == set(expected)
    for key, shape in shapes.items():
        assert shape == expected[key], key
