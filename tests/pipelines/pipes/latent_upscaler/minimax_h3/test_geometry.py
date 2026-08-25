"""Tests for `src/pipelines/pipes/latent_upscaler/minimax_h3/geometry.py`."""

from __future__ import annotations

import pytest
import torch
import torch.nn.functional as F

from src.pipelines.pipes.latent_upscaler.minimax_h3.geometry import (
    TargetGeometry,
    pad_frames_to_h3_grid,
    resolve_target_geometry,
    upsample_chunked,
)


class TestResolveTargetGeometry:
    def test_megapixels_mode_worked_example(self):
        """The shipped default (2.1MP) on a 1344x768 source -- every axis and
        the derived latent dims must match exactly."""
        g = resolve_target_geometry(768, 1344, mode="megapixels", megapixels=2.1)
        assert (g.height, g.width) == (1088, 1920)
        assert (g.latent_height, g.latent_width) == (68, 120)

    def test_scale_mode_doubles_both_axes(self):
        g = resolve_target_geometry(768, 1344, mode="scale", scale=2.0)
        assert (g.height, g.width) == (1536, 2688)
        assert g.effective_scale == pytest.approx(2.0)

    @pytest.mark.parametrize("height,width", [(700, 500), (513, 1001), (768, 1344), (100, 4000)])
    def test_target_axes_always_land_on_the_32px_grid(self, height, width):
        g = resolve_target_geometry(height, width, mode="megapixels", megapixels=3.0)
        assert g.height % 32 == 0
        assert g.width % 32 == 0

    def test_effective_scale_is_the_mean_of_the_two_axis_ratios(self):
        g = resolve_target_geometry(768, 1344, mode="scale", scale=1.5)
        h_ratio = g.height / 768
        w_ratio = g.width / 1344
        assert g.effective_scale == pytest.approx((h_ratio + w_ratio) / 2.0)

    def test_refuses_a_downscale_request(self):
        with pytest.raises(ValueError, match="only upscales"):
            resolve_target_geometry(1536, 2688, mode="scale", scale=0.5)

    def test_refuses_when_megapixels_target_is_smaller_than_source(self):
        with pytest.raises(ValueError, match="only upscales"):
            resolve_target_geometry(1536, 2688, mode="megapixels", megapixels=0.5)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="unknown target_mode"):
            resolve_target_geometry(768, 1344, mode="bogus")

    def test_latent_dims_are_pixel_dims_over_16(self):
        g = resolve_target_geometry(768, 1344, mode="scale", scale=2.0)
        assert g.latent_height == g.height // 16
        assert g.latent_width == g.width // 16


class TestPadFramesToH3Grid:
    def test_already_on_the_17n5_grid_is_a_noop(self):
        # 17*7 + 5 == 124
        frames = torch.rand(124, 4, 4, 3)
        padded, n0 = pad_frames_to_h3_grid(frames)
        assert padded.shape[0] == 124
        assert n0 == 124
        assert torch.equal(padded, frames)

    def test_pads_up_to_the_next_17n5_value_by_repeating_the_last_frame(self):
        # next 17*n+5 >= 100 is 17*6+5 == 107 -> pad 7 frames.
        frames = torch.arange(100).float().reshape(100, 1, 1, 1).expand(100, 2, 2, 3).contiguous()
        padded, n0 = pad_frames_to_h3_grid(frames)
        assert padded.shape[0] == 107
        assert n0 == 100
        assert torch.equal(padded[:100], frames)
        assert torch.equal(padded[100:], frames[-1:].expand(7, 2, 2, 3))


class _LinearRef:
    """Position-invariant stand-in for the real upsampler: trilinear-
    interpolates the window it is handed to `target_size`, with no learned
    cross-frame mixing. Since this pipe's chunking never changes T (target_t
    == the window's own T, see `upsample_chunked`'s docstring), the temporal
    resample is an identity -- so a correct chunk/overlap indexing must
    reproduce a whole-clip call EXACTLY, frame for frame."""

    def __call__(self, latent, *, scale, target_size):
        return F.interpolate(latent, size=target_size, mode="trilinear", align_corners=False)


class TestUpsampleChunked:
    def test_single_call_when_t_within_chunk(self):
        latent = torch.randn(1, 4, 3, 6, 8)
        mod = _LinearRef()
        out = upsample_chunked(mod, latent, 1.5, (12, 16), chunk=16, overlap=2)
        whole = mod(latent, scale=1.5, target_size=(3, 12, 16))
        assert torch.equal(out, whole)

    def test_chunked_matches_whole_clip_exactly_on_a_linear_module(self):
        """T=7, chunk=3, overlap=1 -- the case the card calls out explicitly.
        A wrong trim (off by one) either drops or duplicates a frame, which
        breaks the frame-count match on its own before values are even
        compared."""
        torch.manual_seed(0)
        latent = torch.randn(1, 4, 7, 6, 8)
        mod = _LinearRef()

        whole = mod(latent, scale=1.5, target_size=(7, 12, 16))
        chunked = upsample_chunked(mod, latent, 1.5, (12, 16), chunk=3, overlap=1)

        assert chunked.shape == whole.shape
        assert torch.equal(chunked, whole)

    @pytest.mark.parametrize("t,chunk,overlap", [(20, 16, 2), (17, 5, 2), (5, 16, 2), (33, 8, 3), (10, 4, 1)])
    def test_chunked_matches_whole_clip_across_shapes(self, t, chunk, overlap):
        torch.manual_seed(1)
        latent = torch.randn(1, 4, t, 4, 4)
        mod = _LinearRef()

        whole = mod(latent, scale=2.0, target_size=(t, 8, 8))
        chunked = upsample_chunked(mod, latent, 2.0, (8, 8), chunk=chunk, overlap=overlap)

        assert chunked.shape == whole.shape
        assert torch.equal(chunked, whole)

    def test_frame_count_is_unchanged_only_spatial_moves(self):
        latent = torch.randn(1, 4, 9, 4, 4)
        mod = _LinearRef()
        out = upsample_chunked(mod, latent, 2.0, (8, 8), chunk=4, overlap=1)
        assert out.shape == (1, 4, 9, 8, 8)

    def test_chunk_must_exceed_overlap(self):
        latent = torch.randn(1, 4, 20, 4, 4)
        mod = _LinearRef()
        with pytest.raises(ValueError, match="must be greater than overlap"):
            upsample_chunked(mod, latent, 2.0, (8, 8), chunk=2, overlap=2)


class TestTinyRealArch:
    """Best-effort check against the real `MiniMaxH3LatentUpsampler` -- unlike
    `_LinearRef` above, its ResBlocks/TemporalBlocks DO mix across frames, so
    a chunked call is only expected to match a whole-clip call away from
    chunk boundaries (the real receptive-field-limited case the card calls
    out). This test only asserts what chunking must NEVER get wrong
    regardless of receptive field: the output shape."""

    def _module(self):
        from vendor.gpl.comfyui.ops import disable_weight_init
        from src.platform.runtime.native.vae.minimax_h3_latent_upsampler import MiniMaxH3LatentUpsampler

        config = {
            "in_channels": 4, "channels": 64, "num_res_blocks": 2,
            "temporal_every": 2, "temporal_kernel": 3, "embed_dim": 8, "dropout": 0.0,
        }
        module = MiniMaxH3LatentUpsampler.from_config(config, disable_weight_init)
        module.eval()
        with torch.no_grad():
            for p in module.parameters():
                if p.is_floating_point():
                    p.normal_(0.0, 0.02)
        return module

    def test_chunked_output_shape_matches_whole_clip(self):
        torch.manual_seed(2)
        module = self._module()
        latent = torch.randn(1, 4, 7, 4, 4)

        with torch.no_grad():
            whole = module(latent, scale=1.5, target_size=(7, 6, 6))
            chunked = upsample_chunked(module, latent, 1.5, (6, 6), chunk=3, overlap=1)

        assert chunked.shape == whole.shape == (1, 4, 7, 6, 6)
