"""Tests for the shared LTX-2.5 VRAM-aware whole-clip/tiled diffusion-decode
ladder.

Unlike the encode ladder's tests, these drive a REAL (tiny-config)
``LTXDiffusionVideoVAE`` rather than a `SimpleNamespace`: the estimate reads the
module's own widths and the tiled path goes through `enable_tiling` and
`decode()`'s internal dispatch, so a fake would structurally pass even if the
module's shape drifted out from under the ladder. Whole-clip OOM is simulated by
patching ``decoder.forward`` -- the entry point the whole-clip path uses and the
tiled path does not, so the fallback still runs for real.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.pipes._shared.vae.ltx_tiled_decode import (
    DECODE_NOISE_SEED_OFFSET,
    auto_decode_tile_sizes,
    context_token_count,
    decode_bytes_per_context_token,
    decode_bytes_per_latent_cell,
    decode_with_oom_retry,
    estimate_stages_1_to_3_gb,
    estimate_whole_clip_decode_gb,
    supports_diffusion_tiled_decode,
)
from src.platform.runtime.native.vae.ltx_diffusion_video import LTXDiffusionVideoVAE
from tests.platform.runtime.native.vae.test_ltx_diffusion_video import _TINY_CONFIG, _randomize_weights
from vendor.gpl.comfyui.ops import disable_weight_init

_MOD = "src.pipelines.pipes._shared.vae.ltx_tiled_decode"

# Big enough that a tiled decode really does produce more than one tile at the
# sizes these tests force, and that every neighborhood kernel is satisfied.
_LATENT_SHAPE = (1, 8, 3, 8, 8)


def _build_vae():
    module = LTXDiffusionVideoVAE.from_config(_TINY_CONFIG, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return SimpleNamespace(module=module, compute_dtype=torch.float32)


class _FakeResidencyRegistry:
    def __init__(self):
        self.offload_all_calls = []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


class _CountingSpy:
    """Records call count while delegating, so a test can prove the tiled path
    actually ran rather than ``decode()`` quietly deciding not to tile."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.wrapped(*args, **kwargs)


class _OomOnFirstCalls:
    """Wraps ``decoder.forward``, raising OOM for the first ``times`` calls."""

    def __init__(self, wrapped, times: int):
        self.wrapped = wrapped
        self.times = times
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.times:
            raise torch.cuda.OutOfMemoryError("simulated")
        return self.wrapped(*args, **kwargs)


def _run(vae, latent, *, free_vram, manager=None, generator=None):
    manager = manager or _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_MOD}.free_vram_gb", return_value=free_vram):
        pixels = decode_with_oom_retry(vae, latent, "cuda", generator=generator,
                                       profiler_mark="caller.decode")
    return pixels, manager, mock_clear


class TestEstimate:
    def test_context_token_count_follows_the_causal_frame_mapping(self):
        vae = _build_vae()
        latent = torch.zeros(*_LATENT_SHAPE)
        # (3 - 1) * 8 + 1 = 17 pixel frames; 8 latent cells * (32 // 4) = 64 grid.
        assert context_token_count(vae.module, latent) == 17 * 64 * 64

    def test_estimate_is_tokens_times_the_summed_per_token_terms_plus_stages_1_to_3(self):
        vae = _build_vae()
        latent = torch.zeros(*_LATENT_SHAPE)
        expected = (
            context_token_count(vae.module, latent)
            * decode_bytes_per_context_token(vae.module) / (1024 ** 3)
            + estimate_stages_1_to_3_gb(vae.module, latent)
        )
        assert estimate_whole_clip_decode_gb(vae.module, latent) == pytest.approx(expected)

    def test_stages_1_to_3_estimate_scales_with_latent_cells_not_stage5_tokens(self):
        """The term this test guards is exactly the one the diagnosed bug was
        missing: stages 1-3 price by LATENT cell count, which is invariant to
        the causal pixel-frame mapping stage 5's token count uses."""
        vae = _build_vae()
        small = torch.zeros(1, 8, 3, 4, 4)
        big = torch.zeros(1, 8, 3, 8, 8)
        small_gb = estimate_stages_1_to_3_gb(vae.module, small)
        big_gb = estimate_stages_1_to_3_gb(vae.module, big)
        assert small_gb > 0
        # 4x the latent cells (H and W both doubled) -> 4x the estimate.
        assert big_gb == pytest.approx(small_gb * 4)

    def test_per_latent_cell_bytes_sum_the_documented_terms(self):
        vae = _build_vae()
        decoder = vae.module.decoder
        element = 4  # the tiny module is fp32
        cumulative_tokens = 1
        expected = 0
        for blocks, upsample in zip(decoder.det_stages[:-1], decoder.upsamples[:-1]):
            block = blocks[0]
            stage_width = block.attn.heads * block.attn.head_dim
            mlp_hidden = block.mlp.w_gate.weight.shape[0]
            proj_out = upsample.proj.weight.shape[0]
            per_token = 4 * stage_width * element + 3 * mlp_hidden * element + proj_out * element
            expected += cumulative_tokens * per_token
            cumulative_tokens *= math.prod(upsample.stride)
        assert decode_bytes_per_latent_cell(vae.module) == expected

    def test_per_token_bytes_sum_the_documented_terms(self):
        vae = _build_vae()
        attn = vae.module.decoder.diff_blocks[0].attn
        width = attn.heads * attn.head_dim
        element = 4  # the tiny module is fp32
        pixel_channels = 3 * vae.module.decoder.patch_size ** 2
        expected = (
            3 * width * element                         # context + hidden + one copy
            + pixel_channels * element                  # x_t canvas
            + 4 * width * element                       # fused qkv + three reshaped copies
            + 3 * attn.heads * max(attn.rope.rope_dim_split) * 4   # rope fp32 promotion
        )
        assert decode_bytes_per_context_token(vae.module) == expected

    def test_production_grid_projects_past_a_5090(self):
        """The reported datapoint: 121 frames at 768x1280 is latent (16, 24, 40),
        and it OOM'd a 31.37 GiB card. The projection must agree that it cannot
        fit, or the ladder would never reach for tiling."""
        # Real 2.5 widths rather than the tiny module's, since this asserts the
        # production number: 256 stage-5 channels, 4 heads of 64, patch 4, bf16.
        # det_stages/upsamples[:-1] are stages 1-3's own (plausible, not the
        # checkpoint's exact) widths, needed now that the whole-clip estimate
        # also prices that phase; upsamples[-1] is stage 4's, unchanged.
        det_stages_1_to_3 = [
            [SimpleNamespace(
                attn=SimpleNamespace(heads=2, head_dim=64),
                mlp=SimpleNamespace(w_gate=SimpleNamespace(weight=torch.zeros(512, 128))),
            )]
            for _ in range(3)
        ]
        upsamples_1_to_3 = [
            SimpleNamespace(stride=stride, proj=SimpleNamespace(weight=torch.zeros(256, 128)))
            for stride in ((1, 2, 2), (2, 1, 1), (2, 2, 2))
        ]
        real = SimpleNamespace(
            decoder=SimpleNamespace(
                patch_size=4, out_channels=3,
                diff_blocks=[SimpleNamespace(attn=SimpleNamespace(
                    heads=4, head_dim=64, rope=SimpleNamespace(rope_dim_split=(16, 24, 24)),
                ))],
                det_stages=det_stages_1_to_3 + [None],
                upsamples=upsamples_1_to_3 + [SimpleNamespace(stride=(2, 2, 2))],
            ),
            spatial_compression_ratio=32,
            temporal_compression_ratio=8,
            parameters=lambda: iter([torch.zeros(1, dtype=torch.bfloat16)]),
        )
        latent = torch.zeros(1, 128, 16, 24, 40)
        assert context_token_count(real, latent) == 121 * 192 * 320
        assert estimate_whole_clip_decode_gb(real, latent) > 31.37


class TestLadder:
    def test_plain_decode_when_it_fits(self):
        vae = _build_vae()
        latent = torch.randn(*_LATENT_SHAPE)
        pixels, manager, mock_clear = _run(vae, latent, free_vram=1000.0)

        assert pixels.shape == (1, 3, 17, 256, 256)
        assert manager.offload_all_calls == []
        mock_clear.assert_not_called()
        assert vae.module.use_tiling is False

    def test_single_oom_retries_whole_clip_after_eviction(self):
        vae = _build_vae()
        spy = _OomOnFirstCalls(vae.module.decoder.forward, times=1)
        vae.module.decoder.forward = spy
        latent = torch.randn(*_LATENT_SHAPE)

        pixels, manager, mock_clear = _run(vae, latent, free_vram=1000.0)

        assert spy.calls == 2                      # OOM'd, then succeeded whole-clip
        assert len(manager.offload_all_calls) == 1
        assert manager.offload_all_calls[0] == ("cuda", (vae,))
        mock_clear.assert_called_once()
        assert pixels.shape == (1, 3, 17, 256, 256)
        assert vae.module.use_tiling is False

    def test_double_oom_falls_back_to_the_tiled_path(self):
        vae = _build_vae()
        spy = _OomOnFirstCalls(vae.module.decoder.forward, times=2)
        vae.module.decoder.forward = spy
        latent = torch.randn(*_LATENT_SHAPE)

        tiled_spy = _CountingSpy(vae.module.tiled_decode)
        vae.module.tiled_decode = tiled_spy

        with patch.dict("os.environ", {"NATIVE_LTX_DIFFUSION_TILE_PX": "128",
                                       "NATIVE_LTX_DIFFUSION_TILE_FRAMES": "16"}):
            pixels, manager, _ = _run(vae, latent, free_vram=1000.0)

        # decoder.forward is the whole-clip entry point only; the tiled path
        # never calls it, so it stays at the two simulated failures.
        assert spy.calls == 2
        assert tiled_spy.calls == 1               # tiling really engaged
        assert len(manager.offload_all_calls) == 1
        assert pixels.shape == (1, 3, 17, 256, 256)
        assert torch.isfinite(pixels).all()
        assert vae.module.use_tiling is False

    def test_projected_overflow_skips_the_whole_clip_attempt(self):
        vae = _build_vae()
        spy = _OomOnFirstCalls(vae.module.decoder.forward, times=0)
        vae.module.decoder.forward = spy
        latent = torch.randn(*_LATENT_SHAPE)

        tiled_spy = _CountingSpy(vae.module.tiled_decode)
        vae.module.tiled_decode = tiled_spy

        with patch.dict("os.environ", {"NATIVE_LTX_DIFFUSION_TILE_PX": "128",
                                       "NATIVE_LTX_DIFFUSION_TILE_FRAMES": "16"}):
            pixels, manager, mock_clear = _run(vae, latent, free_vram=0.001)

        assert spy.calls == 0                      # never even tried whole-clip
        assert tiled_spy.calls == 1
        assert len(manager.offload_all_calls) == 1
        mock_clear.assert_called_once()
        assert pixels.shape == (1, 3, 17, 256, 256)

    def test_tiled_oom_raises_a_crisp_error_naming_the_grid(self):
        vae = _build_vae()
        vae.module.decoder.forward = _OomOnFirstCalls(vae.module.decoder.forward, times=99)
        original_stage_4 = vae.module.decoder.forward_stage_4
        vae.module.decoder.forward_stage_4 = _OomOnFirstCalls(original_stage_4, times=99)
        latent = torch.randn(*_LATENT_SHAPE)

        with pytest.raises(torch.cuda.OutOfMemoryError, match="even with tiled decoding") as excinfo:
            _run(vae, latent, free_vram=1000.0)

        message = str(excinfo.value)
        assert "(3, 8, 8)" in message
        assert "lower resolution or fewer frames" in message
        assert vae.module.use_tiling is False     # restored despite the raise

    def test_conv_vae_bypasses_the_ladder_entirely(self):
        """The 2.0/2.3 VAE self-chunks internally and has no tiled decode, so it
        must take a bare ``decode()`` with no VRAM query and no eviction."""
        calls = []
        vae = SimpleNamespace(
            module=SimpleNamespace(decode=lambda z: calls.append(z) or torch.zeros(1, 3, 9, 16, 16)),
            compute_dtype=torch.float32,
        )
        assert supports_diffusion_tiled_decode(vae.module) is False

        with patch(f"{_MOD}.free_vram_gb") as mock_vram, \
             patch(f"{_MOD}.get_residency_registry") as mock_manager:
            pixels = decode_with_oom_retry(vae, torch.randn(1, 8, 2, 4, 4), "cuda",
                                           profiler_mark="caller.decode")

        assert len(calls) == 1
        assert pixels.shape == (1, 3, 9, 16, 16)
        mock_vram.assert_not_called()
        mock_manager.assert_not_called()


class TestConvPathOomRetry:
    """The conv (2.0/2.3) decoder self-chunks its OWN activations, but a
    caller that keeps other large models resident on the same device (e.g.
    the detailer's DiT) can still OOM here -- one evict-and-retry rung, not
    the full whole-clip/tiled ladder (which the conv decoder doesn't have)."""

    @staticmethod
    def _conv_vae(decode):
        return SimpleNamespace(module=SimpleNamespace(decode=decode), compute_dtype=torch.float32)

    def test_succeeds_on_first_try_without_touching_the_residency_manager(self):
        calls = []
        vae = self._conv_vae(lambda z: calls.append(z) or torch.zeros(1, 3, 9, 16, 16))
        manager = _FakeResidencyRegistry()

        with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
             patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
            pixels = decode_with_oom_retry(vae, torch.randn(1, 8, 2, 4, 4), "cuda",
                                           profiler_mark="caller.decode")

        assert len(calls) == 1
        assert pixels.shape == (1, 3, 9, 16, 16)
        assert manager.offload_all_calls == []
        mock_clear.assert_not_called()

    def test_oom_evicts_foreign_components_and_retries_once(self):
        calls = {"n": 0}

        def flaky_decode(z):
            calls["n"] += 1
            if calls["n"] == 1:
                raise torch.cuda.OutOfMemoryError("boom")
            return torch.zeros(1, 3, 9, 16, 16)

        vae = self._conv_vae(flaky_decode)
        manager = _FakeResidencyRegistry()

        with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
             patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
            pixels = decode_with_oom_retry(vae, torch.randn(1, 8, 2, 4, 4), "cuda",
                                           profiler_mark="caller.decode")

        assert calls["n"] == 2
        assert manager.offload_all_calls == [("cuda", (vae,))]
        mock_clear.assert_called_once()
        assert pixels.shape == (1, 3, 9, 16, 16)

    def test_still_oom_after_eviction_raises_a_clear_error(self):
        vae = self._conv_vae(lambda z: (_ for _ in ()).throw(torch.cuda.OutOfMemoryError("boom")))
        manager = _FakeResidencyRegistry()

        with patch(f"{_MOD}.get_residency_registry", return_value=manager), \
             patch(f"{_MOD}.clear_gpu_memory"):
            with pytest.raises(torch.cuda.OutOfMemoryError, match="even after evicting"):
                decode_with_oom_retry(vae, torch.randn(1, 8, 2, 4, 4), "cuda",
                                      profiler_mark="caller.decode")


class TestSeededDecodeNoise:
    """The diffusion decoder samples the pixels it denoises, so a decode is only
    reproducible if the caller hands it a seeded stream."""

    @staticmethod
    def _generator(seed: int) -> torch.Generator:
        return torch.Generator(device="cpu").manual_seed(seed + DECODE_NOISE_SEED_OFFSET)

    def test_same_seed_decodes_identically_whole_clip(self):
        vae = _build_vae()
        latent = torch.randn(*_LATENT_SHAPE)

        first, _, _ = _run(vae, latent, free_vram=1000.0, generator=self._generator(7))
        second, _, _ = _run(vae, latent, free_vram=1000.0, generator=self._generator(7))
        assert torch.equal(first, second)

    def test_different_seed_decodes_differently(self):
        vae = _build_vae()
        latent = torch.randn(*_LATENT_SHAPE)

        first, _, _ = _run(vae, latent, free_vram=1000.0, generator=self._generator(7))
        second, _, _ = _run(vae, latent, free_vram=1000.0, generator=self._generator(8))
        assert not torch.allclose(first, second)

    def test_same_seed_decodes_identically_when_forced_tiled(self):
        latent = torch.randn(*_LATENT_SHAPE)
        outputs = []
        for _ in range(2):
            vae = _build_vae()
            torch.manual_seed(0)
            _randomize_weights(vae.module)
            with patch.dict("os.environ", {"NATIVE_LTX_DIFFUSION_TILE_PX": "128",
                                           "NATIVE_LTX_DIFFUSION_TILE_FRAMES": "16"}):
                pixels, _, _ = _run(vae, latent, free_vram=0.001, generator=self._generator(7))
            outputs.append(pixels)
        assert torch.equal(outputs[0], outputs[1])

    def test_an_oom_on_an_earlier_rung_does_not_reroll_the_noise(self):
        """The ladder rewinds the generator before every rung, so which rung
        ends up succeeding cannot change what a given seed decodes from.

        The OOM is injected into ``decoder.denoise``, NOT ``decoder.forward``:
        ``forward`` draws ``x_t`` and only then calls ``denoise``, so failing at
        ``forward`` would abort before the generator was ever advanced and this
        test would pass with or without the rewind. Failing at ``denoise`` makes
        each whole-clip attempt consume a draw first, which is the situation the
        rewind exists for.
        """
        latent = torch.randn(*_LATENT_SHAPE)
        env = {"NATIVE_LTX_DIFFUSION_TILE_PX": "128", "NATIVE_LTX_DIFFUSION_TILE_FRAMES": "16"}

        straight_to_tiled = _build_vae()
        torch.manual_seed(0)
        _randomize_weights(straight_to_tiled.module)
        with patch.dict("os.environ", env):
            expected, _, _ = _run(straight_to_tiled, latent, free_vram=0.001,
                                  generator=self._generator(7))

        via_oom = _build_vae()
        torch.manual_seed(0)
        _randomize_weights(via_oom.module)
        # times=2: the two whole-clip attempts fail after their noise draw; the
        # tiled path's own per-tile denoise calls come after and succeed.
        spy = _OomOnFirstCalls(via_oom.module.decoder.denoise, times=2)
        via_oom.module.decoder.denoise = spy
        with patch.dict("os.environ", env):
            after_oom, _, _ = _run(via_oom, latent, free_vram=1000.0, generator=self._generator(7))

        assert spy.calls > 2, "the tiled fallback must have run after the two failures"
        assert torch.equal(expected, after_oom)

    def test_without_a_generator_the_decode_is_not_reproducible(self):
        """Guards the wiring itself: no generator means global RNG, which is the
        bug this parameter exists to close."""
        vae = _build_vae()
        latent = torch.randn(*_LATENT_SHAPE)

        first, _, _ = _run(vae, latent, free_vram=1000.0)
        second, _, _ = _run(vae, latent, free_vram=1000.0)
        assert not torch.allclose(first, second)

    def test_conv_vae_ignores_the_generator(self):
        calls = []
        vae = SimpleNamespace(
            module=SimpleNamespace(decode=lambda z: calls.append(z) or torch.zeros(1, 3, 9, 16, 16)),
            compute_dtype=torch.float32,
        )
        pixels = decode_with_oom_retry(
            vae, torch.randn(1, 8, 2, 4, 4), "cpu",
            generator=self._generator(7), profiler_mark="caller.decode",
        )
        assert len(calls) == 1
        assert pixels.shape == (1, 3, 9, 16, 16)

    def test_offset_cannot_collide_with_the_sampler_streams(self):
        from src.platform.runtime.native.sampling import ANCESTRAL_NOISE_SEED_OFFSET

        assert DECODE_NOISE_SEED_OFFSET != ANCESTRAL_NOISE_SEED_OFFSET
        assert DECODE_NOISE_SEED_OFFSET != 0
        # The three streams one request drives must be pairwise distinct.
        for seed in (0, 1, 42, 10_000, 999_999):
            streams = {seed, seed + ANCESTRAL_NOISE_SEED_OFFSET, seed + DECODE_NOISE_SEED_OFFSET}
            assert len(streams) == 3


class TestAutoTileSizes:
    def test_shrinks_the_tile_as_the_budget_shrinks(self):
        vae = _build_vae()
        latent = torch.zeros(*_LATENT_SHAPE)
        generous = auto_decode_tile_sizes(vae.module, latent, 1000.0)
        tight = auto_decode_tile_sizes(vae.module, latent, 0.002)

        assert generous["tile_sample_min_height"] == 768        # reference default
        assert tight["tile_sample_min_height"] < generous["tile_sample_min_height"]
        assert tight["tile_sample_min_num_frames"] < generous["tile_sample_min_num_frames"]

    def test_none_budget_keeps_the_reference_defaults(self):
        vae = _build_vae()
        sizes = auto_decode_tile_sizes(vae.module, torch.zeros(*_LATENT_SHAPE), None)
        assert sizes["tile_sample_min_height"] == 768
        assert sizes["tile_sample_min_num_frames"] == 80

    def test_top_rung_when_the_combined_estimate_fits(self):
        vae = _build_vae()
        sizes = auto_decode_tile_sizes(vae.module, torch.zeros(*_LATENT_SHAPE), 1000.0)
        assert sizes["tile_sample_min_height"] == 768
        assert sizes["tile_sample_min_num_frames"] == 80

    def test_the_stages_1_to_3_term_alone_can_push_a_rung_over_budget(self):
        """A budget between the top rung's stage-5-only cost and its combined
        (stage-5 + stages-1-3) per-tile cost must step down -- pricing stage 5
        alone, as the estimator did before this term existed, would have kept
        the top rung and OOM'd on stages 1-3."""
        vae = _build_vae()
        latent = torch.zeros(*_LATENT_SHAPE)
        top_rung = auto_decode_tile_sizes(vae.module, latent, 1000.0)
        tile_px, tile_frames = top_rung["tile_sample_min_height"], top_rung["tile_sample_min_num_frames"]
        patch_size = vae.module.decoder.patch_size
        ratio_t, ratio_hw = vae.module.temporal_compression_ratio, vae.module.spatial_compression_ratio

        tokens = tile_frames * (tile_px // patch_size) ** 2
        latent_cells = (tile_frames // ratio_t) * (tile_px // ratio_hw) ** 2
        stage5_only_gb = tokens * decode_bytes_per_context_token(vae.module) / (1024 ** 3)
        combined_gb = stage5_only_gb + latent_cells * decode_bytes_per_latent_cell(vae.module) / (1024 ** 3)
        assert stage5_only_gb < combined_gb, "the fake module's stages-1-3 cost must be nonzero"
        budget_between = (stage5_only_gb + combined_gb) / 2

        sizes = auto_decode_tile_sizes(vae.module, latent, budget_between)
        assert sizes["tile_sample_min_height"] < top_rung["tile_sample_min_height"]

    def test_sizes_snap_to_the_tiling_grid_cell(self):
        vae = _build_vae()
        upsample_stride = vae.module.decoder.upsamples[-1].stride
        frame_cell = upsample_stride[0]
        height_cell = upsample_stride[1] * vae.module.decoder.patch_size

        with patch.dict("os.environ", {"NATIVE_LTX_DIFFUSION_TILE_PX": "300",
                                       "NATIVE_LTX_DIFFUSION_TILE_FRAMES": "25"}):
            sizes = auto_decode_tile_sizes(vae.module, torch.zeros(*_LATENT_SHAPE), 1000.0)

        assert sizes["tile_sample_min_height"] % height_cell == 0
        assert sizes["tile_sample_min_num_frames"] % frame_cell == 0
        assert sizes["tile_sample_min_height"] <= 300
        assert sizes["tile_sample_min_num_frames"] <= 25

    def test_stride_stays_below_the_tile_so_seams_overlap(self):
        vae = _build_vae()
        sizes = auto_decode_tile_sizes(vae.module, torch.zeros(*_LATENT_SHAPE), 1000.0)
        assert sizes["tile_sample_stride_height"] < sizes["tile_sample_min_height"]
        assert sizes["tile_sample_stride_width"] < sizes["tile_sample_min_width"]
        assert sizes["tile_sample_stride_num_frames"] < sizes["tile_sample_min_num_frames"]

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-5"])
    def test_malformed_env_override_is_ignored(self, bad):
        vae = _build_vae()
        with patch.dict("os.environ", {"NATIVE_LTX_DIFFUSION_TILE_PX": bad}):
            sizes = auto_decode_tile_sizes(vae.module, torch.zeros(*_LATENT_SHAPE), None)
        assert sizes["tile_sample_min_height"] == 768
