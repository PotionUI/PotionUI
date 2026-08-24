"""Tests for the LTX-2.5 ``CausalDiffusionVAE`` (diffusion-decoder video VAE).

The real-header test is the one that can catch a wrong key set: the tiny-config
tests elsewhere build a module and load its OWN ``state_dict()`` back, which
cannot notice that the constructed keys disagree with the shipped checkpoint's.
Here the real file is opened header-only (``safe_open`` reads keys and shapes
without materializing any weight) and the production-sized module is built on
the meta device, so the comparison costs neither RAM nor disk reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.vae_detect import (
    detect_ltx_diffusion_vae_config,
    detect_ltx_video_vae_config,
)
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.vae.loader import _VaeSpec
from src.platform.runtime.native.vae.ltx_diffusion_video import (
    LTXDiffusionVideoVAE,
    _neighborhood_attention,
)
from vendor.gpl.comfyui.ops import disable_weight_init

_REAL_VAE_PATH = Path("models/vae/ltx-2.5-video-vae-bf16.safetensors")
_LTX23_VAE_PATH = Path("models/vae/LTX23_video_vae_bf16.safetensors")

# The diagnostic-only statistics buffers the module always builds; the real
# checkpoint ships only the two that normalize/un_normalize read. Mirrors the
# allowlist ``load_ltx_diffusion_video_vae`` passes to ``_VaeSpec``.
_EXPECTED_MISSING = {
    "per_channel_statistics.mean-of-stds",
    "per_channel_statistics.mean-of-stds_over_std-of-means",
    "per_channel_statistics.channel",
}

# Small end to end: the encoder's compression (patch 4 x 2 x 2 x 2 = 32 spatial,
# 2 x 2 x 2 = 8 temporal) matches the decoder's expansion (upsample strides
# 2 x 1 x 2 x 2 = 8 spatial x patch 4 = 32; temporal 1 x 2 x 2 x 2 = 8), exactly
# as the real config does -- an inconsistent pair would decode to the wrong size
# for reasons that have nothing to do with the code under test.
_TINY_CONFIG = {
    "_class_name": "CausalDiffusionVAE",
    "dims": 3,
    "model_output_type": "x0",
    "spatial_padding_mode": "zeros",
    "encoder": {
        "_class_name": "Encoder",
        "dims": 3,
        "in_channels": 3,
        "out_channels": 8,
        "blocks": [
            ["res_x", {"num_layers": 1}],
            ["compress_space_res", {"multiplier": 2}],
            ["res_x", {"num_layers": 1}],
            ["compress_time_res", {"multiplier": 2}],
            ["res_x", {"num_layers": 1}],
            ["compress_all_res", {"multiplier": 2}],
            ["res_x", {"num_layers": 1}],
            ["compress_all_res", {"multiplier": 1}],
            ["res_x", {"num_layers": 1}],
        ],
        "patch_size": 4,
        "latent_log_var": "constant",
        "latent_log_var_value": -7.824046010856292,
        "norm_layer": "pixel_norm",
        "base_channels": 8,
        "spatial_padding_mode": "zeros",
    },
    "decoder": {
        "_class_name": "NADiffusionDecoder",
        "in_channels": 8,
        "out_channels": 3,
        "patch_size": 4,
        "head_dim": 8,
        "stage_channels": [32, 16, 16, 16, 16],
        "stage_depths": [1, 1, 1, 1, 2],
        "stage_kernels": [[3, 3, 3], [3, 3, 3], [3, 3, 3], [3, 3, 3], [3, 3, 3]],
        "upsamples": [[[1, 2, 2], 2], [[2, 1, 1], 1], [[2, 2, 2], 1], [[2, 2, 2], 1]],
        "spatial_padding_mode": "zeros",
        "resampler_kind": "linear",
        "stage5_kernel": [3, 3, 3],
        "timestep_scale_multiplier": 1000.0,
        "default_num_inference_steps": 1,
    },
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


def _build_tiny(**decoder_overrides) -> LTXDiffusionVideoVAE:
    config = dict(_TINY_CONFIG)
    if decoder_overrides:
        config["decoder"] = dict(config["decoder"], **decoder_overrides)
    module = LTXDiffusionVideoVAE.from_config(config, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


def _real_header() -> tuple[dict[str, str], dict[str, tuple[int, ...]]]:
    from safetensors import safe_open

    with safe_open(_REAL_VAE_PATH, framework="pt") as f:
        metadata = f.metadata() or {}
        shapes = {key: tuple(f.get_slice(key).get_shape()) for key in f.keys()}
    return metadata, shapes


class TestDetectLtxDiffusionVaeConfig:
    def test_detects_embedded_config(self):
        metadata = {"config": json.dumps({"vae": _TINY_CONFIG})}
        config = detect_ltx_diffusion_vae_config(metadata)
        assert config is not None
        assert config["_class_name"] == "CausalDiffusionVAE"
        assert config["decoder"]["in_channels"] == 8

    def test_no_config_key_returns_none(self):
        assert detect_ltx_diffusion_vae_config({}) is None

    def test_malformed_json_returns_none(self):
        assert detect_ltx_diffusion_vae_config({"config": "{not json"}) is None

    def test_conv_decoder_class_is_not_detected(self):
        metadata = {"config": json.dumps({"vae": {"_class_name": "CausalVideoAutoencoder"}})}
        assert detect_ltx_diffusion_vae_config(metadata) is None

    def test_conv_detector_does_not_claim_the_diffusion_config(self):
        metadata = {"config": json.dumps({"vae": _TINY_CONFIG})}
        assert detect_ltx_video_vae_config(metadata) is None

    @pytest.mark.requires_models
    @pytest.mark.skipif(not _REAL_VAE_PATH.exists(), reason="real LTX-2.5 VAE not present")
    def test_real_metadata_is_detected(self):
        metadata, _ = _real_header()
        config = detect_ltx_diffusion_vae_config(metadata)
        assert config is not None
        assert config["decoder"]["_class_name"] == "NADiffusionDecoder"
        assert config["decoder"]["default_num_inference_steps"] == 1
        assert config["model_output_type"] == "x0"
        # The two LTX video detectors must stay mutually exclusive.
        assert detect_ltx_video_vae_config(metadata) is None

    @pytest.mark.requires_models
    @pytest.mark.skipif(not _LTX23_VAE_PATH.exists(), reason="real LTX-2.3 VAE not present")
    def test_real_conv_checkpoint_is_not_detected(self):
        from safetensors import safe_open

        with safe_open(_LTX23_VAE_PATH, framework="pt") as f:
            metadata = f.metadata() or {}
        assert detect_ltx_diffusion_vae_config(metadata) is None
        assert detect_ltx_video_vae_config(metadata) is not None


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_VAE_PATH.exists(), reason="real LTX-2.5 VAE not present")
class TestRealHeaderParity:
    def test_key_and_shape_parity(self):
        metadata, shapes = _real_header()
        config = detect_ltx_diffusion_vae_config(metadata)
        with torch.device("meta"):
            module = LTXDiffusionVideoVAE.from_config(config, disable_weight_init)
        built = {key: tuple(value.shape) for key, value in module.state_dict().items()}

        assert set(shapes) - set(built) == set(), "checkpoint keys the module does not build"
        assert set(built) - set(shapes) == _EXPECTED_MISSING
        mismatched = {key: (shapes[key], built[key]) for key in shapes if shapes[key] != built[key]}
        assert mismatched == {}

    def test_config_derived_geometry_matches_the_checkpoint(self):
        metadata, shapes = _real_header()
        with torch.device("meta"):
            module = LTXDiffusionVideoVAE.from_config(detect_ltx_diffusion_vae_config(metadata), disable_weight_init)

        assert module.latent_channels == shapes["decoder.conv_in.weight"][1]
        assert module.spatial_compression_ratio == 32
        assert module.temporal_compression_ratio == 8
        assert module.decoder.default_num_inference_steps == 1
        assert module.decoder.model_output_type == "x0"
        assert module.decoder.timestep_scale_multiplier == 1000.0
        # ``stage_kernels`` ships five entries; the four deterministic stages
        # take the first four and stage 5 takes ``stage5_kernel``.
        assert len(module.decoder.det_stages) == 4
        assert module.decoder.diff_blocks[0].attn.kernel_size == (11, 11, 11)
        assert module.decoder.det_stages[0][0].attn.kernel_size == (3, 7, 7)

    def test_type_emb_is_carried_but_unused(self):
        """The checkpoint ships ``decoder.type_emb``; neither the diffusers port
        nor ltx-core's own decoder reads it. It exists here for key parity only,
        so perturbing it must not change a decode."""
        module = _build_tiny()
        latent = torch.randn(1, 8, 2, 4, 6)
        with torch.no_grad():
            before = module.decode(latent, generator=torch.Generator().manual_seed(7))
            module.decoder.type_emb.add_(100.0)
            after = module.decode(latent, generator=torch.Generator().manual_seed(7))
        assert torch.equal(before, after)


class TestNeighborhoodAttention:
    """The tiled evaluation must be the exact neighborhood attention, not an
    approximation of it: every query attends to a ``kernel_size`` window that is
    centred where possible and shifted inward at the grid borders."""

    @staticmethod
    def _dense_reference(query, key, value, kernel):
        batch, frames, height, width, heads, head_dim = query.shape
        kt, kh, kw = (min(k, n) for k, n in zip(kernel, (frames, height, width)))
        index = torch.arange(frames * height * width)
        pos_t, rest = index // (height * width), index % (height * width)
        pos_h, pos_w = rest // width, rest % width
        start_t = torch.clamp(pos_t - kt // 2, 0, frames - kt)
        start_h = torch.clamp(pos_h - kh // 2, 0, height - kh)
        start_w = torch.clamp(pos_w - kw // 2, 0, width - kw)
        mask = (
            (pos_t[None, :] >= start_t[:, None]) & (pos_t[None, :] < start_t[:, None] + kt)
            & (pos_h[None, :] >= start_h[:, None]) & (pos_h[None, :] < start_h[:, None] + kh)
            & (pos_w[None, :] >= start_w[:, None]) & (pos_w[None, :] < start_w[:, None] + kw)
        )
        flat = [t.reshape(batch, frames * height * width, heads, head_dim).transpose(1, 2)
                for t in (query, key, value)]
        out = F.scaled_dot_product_attention(*flat, attn_mask=mask[None, None])
        return out.transpose(1, 2).reshape(batch, frames, height, width, heads, head_dim)

    @pytest.mark.parametrize(
        "grid,kernel",
        [
            ((5, 7, 9), (3, 5, 5)),
            ((9, 11, 13), (11, 11, 11)),
            ((4, 4, 4), (3, 3, 3)),
            ((3, 5, 5), (3, 5, 5)),   # grid exactly at the kernel on every axis
            ((7, 6, 5), (3, 7, 7)),   # kernel wider than the grid on two axes
        ],
    )
    def test_matches_dense_masked_reference(self, grid, kernel):
        torch.manual_seed(0)
        frames, height, width = grid
        query, key, value = (
            torch.randn(2, frames, height, width, 3, 16, dtype=torch.float64) for _ in range(3)
        )
        got = _neighborhood_attention(query, key, value, kernel)
        expected = self._dense_reference(query, key, value, kernel)
        assert torch.allclose(got, expected, atol=1e-10)

    def test_matches_the_reference_when_the_score_budget_forces_many_chunks(self, monkeypatch):
        """The default budget fits a whole small grid in one chunk, so the
        multi-chunk concat path only runs at production sizes unless forced."""
        import src.platform.runtime.native.vae.ltx_diffusion_video as module_under_test

        monkeypatch.setattr(module_under_test, "_ATTENTION_SCORE_BUDGET", 1)
        torch.manual_seed(0)
        query, key, value = (torch.randn(2, 9, 11, 13, 3, 16, dtype=torch.float64) for _ in range(3))
        got = module_under_test._neighborhood_attention(query, key, value, (11, 11, 11))
        expected = self._dense_reference(query, key, value, (11, 11, 11))
        assert torch.allclose(got, expected, atol=1e-10)

    def test_rejects_a_grid_smaller_than_its_kernel(self):
        module = _build_tiny()
        latent = torch.randn(1, 8, 2, 2, 2)
        with pytest.raises(ValueError, match="at least its kernel size"):
            module.decode(latent)


class TestDecodeRecipe:
    def test_single_step_x0_takes_the_prediction_directly(self):
        module = _build_tiny()
        seen = []
        original = module.decoder.forward_diffusion_step

        def spy(context, x_t, timestep):
            seen.append(timestep.clone())
            return original(context, x_t, timestep)

        module.decoder.forward_diffusion_step = spy
        latent = torch.randn(1, 8, 2, 4, 6)
        with torch.no_grad():
            module.decode(latent, generator=torch.Generator().manual_seed(0))

        assert len(seen) == 1
        assert torch.equal(seen[0], torch.tensor([1.0]))

    @pytest.mark.parametrize("steps", [2, 3])
    def test_multi_step_timesteps_are_linspace_one_to_one_over_n(self, steps):
        module = _build_tiny()
        seen = []
        original = module.decoder.forward_diffusion_step

        def spy(context, x_t, timestep):
            seen.append(float(timestep[0]))
            return original(context, x_t, timestep)

        module.decoder.forward_diffusion_step = spy
        latent = torch.randn(1, 8, 2, 4, 6)
        with torch.no_grad():
            module.decode(latent, generator=torch.Generator().manual_seed(0), num_inference_steps=steps)

        expected = torch.linspace(1.0, 1.0 / steps, steps).tolist()
        assert len(seen) == steps
        assert seen == pytest.approx(expected, abs=1e-6)

    def test_decode_shape_and_finiteness(self):
        module = _build_tiny()
        latent = torch.randn(1, 8, 3, 4, 6)
        with torch.no_grad():
            pixels = module.decode(latent, generator=torch.Generator().manual_seed(0))
        # Causal temporal mapping: (T - 1) * 8 + 1 frames, 32x spatial.
        assert pixels.shape == (1, 3, 17, 128, 192)
        assert torch.isfinite(pixels).all()

    def test_decode_consumes_the_sampled_noise(self):
        """Stage 5 denoises ``x_t`` through ``conv_in_x_t``; two different noise
        draws of the same latent must not decode to the same pixels."""
        module = _build_tiny()
        latent = torch.randn(1, 8, 2, 4, 6)
        with torch.no_grad():
            first = module.decode(latent, generator=torch.Generator().manual_seed(0))
            second = module.decode(latent, generator=torch.Generator().manual_seed(1))
            repeat = module.decode(latent, generator=torch.Generator().manual_seed(0))
        assert not torch.allclose(first, second)
        assert torch.equal(first, repeat)

    def test_decodes_in_the_checkpoint_dtype(self):
        """The real file is bf16; RoPE and the denoise step both compute in
        fp32 internally and must hand the result back at the input width."""
        module = _build_tiny().to(torch.bfloat16)
        latent = torch.randn(1, 8, 2, 4, 6, dtype=torch.bfloat16)
        with torch.no_grad():
            pixels = module.decode(latent, generator=torch.Generator().manual_seed(0))
        assert pixels.dtype == torch.bfloat16
        assert torch.isfinite(pixels).all()

    def test_decode_is_conditioned_on_the_latent(self):
        module = _build_tiny()
        with torch.no_grad():
            first = module.decode(torch.randn(1, 8, 2, 4, 6), generator=torch.Generator().manual_seed(0))
            second = module.decode(torch.randn(1, 8, 2, 4, 6), generator=torch.Generator().manual_seed(0))
        assert not torch.allclose(first, second)


class TestTiledDecode:
    def test_tiling_is_off_by_default(self):
        assert _build_tiny().use_tiling is False

    def test_tiled_decode_matches_the_untiled_output_shape(self):
        module = _build_tiny()
        latent = torch.randn(1, 8, 3, 8, 8)
        with torch.no_grad():
            untiled = module.decode(latent, generator=torch.Generator().manual_seed(0))
            # Sizes land in LATENT units after dividing by the compression
            # ratios (32 spatial, 8 temporal here): height/width tile to 4
            # latent cells stride 3 (>= the tiny config's stage-0 kernel floor
            # of 3), giving 2 tiles per spatial axis on an 8-cell latent grid;
            # the 3-frame latent can't split below its own floor, so it stays
            # a single temporal tile.
            module.enable_tiling(
                tile_sample_min_height=128, tile_sample_stride_height=96,
                tile_sample_min_width=128, tile_sample_stride_width=96,
                tile_sample_min_num_frames=24, tile_sample_stride_num_frames=24,
            )
            assert module.use_tiling is True
            tiled = module.decode(latent, generator=torch.Generator().manual_seed(0))
        assert tiled.shape == untiled.shape
        assert torch.isfinite(tiled).all()

    def test_disable_tiling_restores_the_whole_volume_path(self):
        module = _build_tiny()
        module.enable_tiling()
        module.disable_tiling()
        assert module.use_tiling is False

    # Latent tile 4 cells / stride 3 on every axis (>= the tiny config's
    # stage-0 kernel floor of 3), forcing 2 tiles on each of T, H, W against
    # an (6, 8, 8) latent -- 8 tiles total, none of them a features-space
    # no-op the way a single-axis split would be.
    _MULTI_TILE_SIZES = dict(
        tile_sample_min_num_frames=32, tile_sample_stride_num_frames=24,
        tile_sample_min_height=128, tile_sample_stride_height=96,
        tile_sample_min_width=128, tile_sample_stride_width=96,
    )

    def test_tiled_decode_matches_untiled_within_tolerance_at_every_seam(self):
        """Stages 1-3 now run per tile, so a query near a tile boundary sees
        truncated neighborhood-attention context relative to the untiled
        (whole-clip) run -- interiors are not expected to match exactly, only
        within a tolerance, the same acceptance the stage-4/5-only tiling
        already had. Checked at the clip edges and blend seams specifically,
        not just in aggregate."""
        module = _build_tiny()
        latent = torch.randn(1, 8, 6, 8, 8)
        with torch.no_grad():
            untiled = module.decode(latent, generator=torch.Generator().manual_seed(0))
            module.enable_tiling(**self._MULTI_TILE_SIZES)
            tiled = module.decode(latent, generator=torch.Generator().manual_seed(0))

        assert tiled.shape == untiled.shape
        assert torch.isfinite(tiled).all()
        assert torch.allclose(tiled, untiled, atol=0.1)
        assert torch.allclose(tiled[:, :, 0], untiled[:, :, 0], atol=0.1)
        assert torch.allclose(tiled[:, :, -1], untiled[:, :, -1], atol=0.1)
        assert torch.allclose(tiled[:, :, :, 0, :], untiled[:, :, :, 0, :], atol=0.1)
        assert torch.allclose(tiled[:, :, :, -1, :], untiled[:, :, :, -1, :], atol=0.1)
        assert torch.allclose(tiled[:, :, :, :, 0], untiled[:, :, :, :, 0], atol=0.1)
        assert torch.allclose(tiled[:, :, :, :, -1], untiled[:, :, :, :, -1], atol=0.1)

    def test_tiled_decode_is_deterministic_for_a_fixed_tile_grid(self):
        module = _build_tiny()
        latent = torch.randn(1, 8, 6, 8, 8)
        module.enable_tiling(**self._MULTI_TILE_SIZES)
        with torch.no_grad():
            first = module.decode(latent, generator=torch.Generator().manual_seed(3))
            second = module.decode(latent, generator=torch.Generator().manual_seed(3))
        assert torch.equal(first, second)

    def test_stages_1_to_3_run_per_tile_on_latent_slices_smaller_than_the_full_grid(self):
        """Regression test for the bug this tiling rework fixes: stages 1-3
        used to run exactly once, on the FULL un-tiled latent, inside
        ``tiled_decode`` -- materializing a full-clip feature volume no
        matter how small the tile size, which is what actually OOM'd."""
        module = _build_tiny()
        latent = torch.randn(1, 8, 6, 8, 8)
        original = module.decoder.forward_stages_1_to_3
        calls = []

        def spy(hidden_states, *args, **kwargs):
            calls.append(tuple(hidden_states.shape))
            return original(hidden_states, *args, **kwargs)

        module.decoder.forward_stages_1_to_3 = spy
        module.enable_tiling(**self._MULTI_TILE_SIZES)
        with torch.no_grad():
            module.decode(latent, generator=torch.Generator().manual_seed(0))

        assert len(calls) > 1
        full_grid = tuple(latent.shape)
        for shape in calls:
            assert shape[2] < full_grid[2]
            assert shape[3] < full_grid[3]
            assert shape[4] < full_grid[4]


class TestEngineRouting:
    """Detection -> loader dispatch -> ``_VaeSpec`` allowlist -> ``post_load``,
    driven through the same ``_load_vae`` production path a real file takes."""

    @staticmethod
    def _write_tiny_checkpoint(directory: Path, *, prefix: str = "") -> Path:
        from safetensors.torch import save_file

        module = _build_tiny()
        state = {
            f"{prefix}{key}": value.detach().contiguous()
            for key, value in module.state_dict().items()
            if key not in _EXPECTED_MISSING
        }
        path = directory / "tiny-ltx-2.5-video-vae.safetensors"
        save_file(state, str(path), metadata={"config": json.dumps({"vae": _TINY_CONFIG})})
        return path

    def test_load_vae_routes_a_standalone_file_to_the_diffusion_loader(self, tmp_path):
        from src.platform.runtime.native.engine import NativeEngineLoader

        path = self._write_tiny_checkpoint(tmp_path)
        native_model = NativeEngineLoader(device="cpu")._load_vae(path)

        assert isinstance(native_model.module, LTXDiffusionVideoVAE)
        assert native_model.kind == "vae"
        assert not any(p.is_meta for p in native_model.module.parameters())

    def test_load_vae_routes_a_vae_prefixed_all_in_one_slice(self, tmp_path):
        from src.platform.runtime.native.engine import NativeEngineLoader

        path = self._write_tiny_checkpoint(tmp_path, prefix="vae.")
        native_model = NativeEngineLoader(device="cpu")._load_vae(path)

        assert isinstance(native_model.module, LTXDiffusionVideoVAE)


class TestModuleContract:
    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = _build_tiny()
        spec = _VaeSpec(family="vae", variant="ltx_diffusion_video")
        load_into_module(module, module.state_dict(), spec)  # must not raise

    def test_post_load_is_safe_noop(self):
        LTXDiffusionVideoVAE.from_config(_TINY_CONFIG, disable_weight_init).post_load()

    def test_encode_rejects_invalid_frame_count(self):
        module = _build_tiny()
        with pytest.raises(ValueError, match=r"1 \+ 8\*k"):
            module.encode(torch.rand(1, 3, 4, 128, 192))

    def test_encode_decode_roundtrip_shape(self):
        module = _build_tiny()
        pixels = torch.rand(1, 3, 17, 128, 192) * 2.0 - 1.0
        with torch.no_grad():
            latent = module.encode(pixels)
            recon = module.decode(latent, generator=torch.Generator().manual_seed(0))
        assert latent.shape == (1, 8, 3, 4, 6)
        assert recon.shape == pixels.shape

    def test_encode_clears_the_thread_local_conv_cache(self):
        module = _build_tiny()
        with torch.no_grad():
            module.encode(torch.rand(1, 3, 9, 128, 192) * 2.0 - 1.0)
        for sub in module.modules():
            if hasattr(sub, "temporal_cache_state"):
                assert sub.temporal_cache_state == {}

    def test_inconsistent_stage_channels_raise_unsupported(self):
        decoder = dict(_TINY_CONFIG["decoder"], stage_channels=[32, 32, 16, 16, 16])
        config = dict(_TINY_CONFIG, decoder=decoder)
        with pytest.raises(NativeEngineUnsupportedError, match="stage_channels"):
            LTXDiffusionVideoVAE.from_config(config, disable_weight_init)

    def test_unknown_resampler_kind_raises_unsupported(self):
        decoder = dict(_TINY_CONFIG["decoder"], resampler_kind="conv")
        config = dict(_TINY_CONFIG, decoder=decoder)
        with pytest.raises(NativeEngineUnsupportedError, match="resampler_kind"):
            LTXDiffusionVideoVAE.from_config(config, disable_weight_init)

    def test_unknown_model_output_type_raises_unsupported(self):
        config = dict(_TINY_CONFIG, model_output_type="epsilon")
        with pytest.raises(NativeEngineUnsupportedError, match="model_output_type"):
            LTXDiffusionVideoVAE.from_config(config, disable_weight_init)
