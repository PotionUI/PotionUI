"""Tests for the LTX-2/2.3 tiled video-VAE ENCODE port.

Ported from Lightricks' first-party ``ltx-core`` (local checkout, Apache-2.0,
read as spec -- not imported, this repo has no dependency on it). See
``src/platform/runtime/native/vae/ltx_tiling.py``'s module docstring for the
full algorithm summary and its documented deviations from whole-clip encode.
"""

from __future__ import annotations

import pytest
import torch

from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.ltx_causal_video import LTXCausalVideoVAE
from src.platform.runtime.native.vae.ltx_tiling import (
    LtxTilingConfig,
    SpatialTilingConfig,
    TemporalTilingConfig,
    _compute_rectangular_mask_1d,
    _Interval,
    _map_spatial_interval_to_latent,
    _map_temporal_interval_to_latent,
    _prepare_tiles_for_encoding,
    _split_by_size,
    _split_temporal_for_encode,
    tiled_encode,
)

# A "real-scale" tiny config: unlike test_ltx_causal_video.py's `_TINY_CONFIG`
# (single 2x/2x/2x downsample stage, purely for cheap roundtrip-shape tests),
# this reproduces the STANDARD deployed architecture's compression ratio --
# patch_size=4 (4x spatial) + compress_space_res + compress_time_res + 2x
# compress_all_res = 8x more spatial (32x total) and 8x temporal -- because
# `ltx_tiling.py` hardcodes scale=8 (T) / 32 (H, W), matching real
# checkpoints (see its module docstring), NOT derived from the config. Using
# the plain 2x/2x/2x `_TINY_CONFIG` here would silently test the wrong scale.
_REAL_SCALE_TINY_CONFIG = {
    "_class_name": "CausalVideoAutoencoder",
    "dims": 3,
    "in_channels": 3,
    "out_channels": 3,
    "latent_channels": 4,
    "encoder_blocks": [
        ["res_x", {"num_layers": 1}],
        ["compress_space_res", {"multiplier": 2}],
        ["compress_time_res", {"multiplier": 2}],
        ["compress_all_res", {"multiplier": 2}],
        ["compress_all_res", {"multiplier": 2}],
        ["res_x", {"num_layers": 1}],
    ],
    "decoder_blocks": [
        ["res_x", {"num_layers": 1, "inject_noise": False}],
        ["compress_all", {"residual": True, "multiplier": 2}],
        ["compress_all", {"residual": True, "multiplier": 2}],
        ["compress_time", {"multiplier": 2}],
        ["compress_space", {"multiplier": 2}],
        ["res_x", {"num_layers": 1, "inject_noise": False}],
    ],
    "scaling_factor": 1.0,
    "norm_layer": "pixel_norm",
    "patch_size": 4,
    "latent_log_var": "uniform",
    "use_quant_conv": False,
    "causal_decoder": False,
    "timestep_conditioning": False,
    "encoder_base_channels": 8,
    "decoder_base_channels": 8,
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


def _build_real_scale_tiny(seed: int = 0) -> LTXCausalVideoVAE:
    torch.manual_seed(seed)
    module = LTXCausalVideoVAE.from_config(_REAL_SCALE_TINY_CONFIG, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


# ---------------------------------------------------------------------------
# Config validation -- mirrors ltx-core's SpatialTilingConfig/TemporalTilingConfig
# ---------------------------------------------------------------------------


class TestTilingConfigValidation:
    def test_spatial_default(self):
        cfg = LtxTilingConfig.default()
        assert cfg.spatial == SpatialTilingConfig(tile_size_in_pixels=512, tile_overlap_in_pixels=64)
        assert cfg.temporal == TemporalTilingConfig(tile_size_in_frames=64, tile_overlap_in_frames=24)

    def test_spatial_tile_size_too_small_rejected(self):
        with pytest.raises(ValueError, match="at least 64"):
            SpatialTilingConfig(tile_size_in_pixels=32)

    def test_spatial_tile_size_not_divisible_by_32_rejected(self):
        with pytest.raises(ValueError, match="divisible by 32"):
            SpatialTilingConfig(tile_size_in_pixels=100)

    def test_spatial_overlap_not_divisible_by_32_rejected(self):
        with pytest.raises(ValueError, match="divisible by 32"):
            SpatialTilingConfig(tile_size_in_pixels=128, tile_overlap_in_pixels=50)

    def test_spatial_overlap_must_be_less_than_tile_size(self):
        with pytest.raises(ValueError, match="Overlap must be less than tile size"):
            SpatialTilingConfig(tile_size_in_pixels=128, tile_overlap_in_pixels=128)

    def test_temporal_tile_size_too_small_rejected(self):
        with pytest.raises(ValueError, match="at least 16"):
            TemporalTilingConfig(tile_size_in_frames=8)

    def test_temporal_tile_size_not_divisible_by_8_rejected(self):
        with pytest.raises(ValueError, match="divisible by 8"):
            TemporalTilingConfig(tile_size_in_frames=20)

    def test_temporal_overlap_must_be_less_than_tile_size(self):
        with pytest.raises(ValueError, match="Overlap must be less than tile size"):
            TemporalTilingConfig(tile_size_in_frames=24, tile_overlap_in_frames=24)


# ---------------------------------------------------------------------------
# (b) Split-boundary shape math -- several T values, default temporal config
# ---------------------------------------------------------------------------


class TestTemporalSplitBoundaryShapeMath:
    """`_split_temporal_for_encode`'s whole point is keeping every tile's own
    frame count on the `1 + 8*k` lattice `encode()` requires -- verify that
    invariant plus gapless coverage for a spread of T values, including the
    two the maintainer's own datapoint uses (121) and its neighbor (129), and
    two small single-tile cases (9, 17)."""

    @pytest.mark.parametrize("t", [121, 129, 9, 17])
    def test_intervals_are_all_1_plus_8k(self, t):
        intervals = _split_temporal_for_encode(t, tile_size_frames=64, overlap_frames=24)
        for iv in intervals:
            length = iv.end - iv.start
            assert (length - 1) % 8 == 0, f"T={t}: interval {iv} has length {length}, not 1+8k"

    @pytest.mark.parametrize("t", [121, 129, 9, 17])
    def test_intervals_cover_full_range_without_gaps(self, t):
        intervals = _split_temporal_for_encode(t, tile_size_frames=64, overlap_frames=24)
        assert intervals[0].start == 0
        # NOTE: split_temporal's "+1" extension can make a middle interval's
        # `end` run past the true video length `t` (verified: for T=121 the
        # penultimate interval ends at 105, past 121? no -- but for smaller T
        # this extension is a no-op since T<=tile_size returns a single
        # interval). Only assert gapless coverage (next.start <= prev.end).
        for prev, nxt in zip(intervals, intervals[1:]):
            assert nxt.start <= prev.end, f"T={t}: gap between {prev} and {nxt}"
        assert intervals[-1].end == t

    def test_single_tile_when_t_fits_in_one_tile(self):
        # 9 <= 64 (tile_size) -> no split at all, matches DEFAULT_SPLIT_OPERATION.
        intervals = _split_temporal_for_encode(9, tile_size_frames=64, overlap_frames=24)
        assert intervals == [(0, 9, 0, 0)]

    def test_121_frame_datapoint_matches_hand_derivation(self):
        """The maintainer's own instrumented-OOM datapoint (480x832x121) --
        hand-derived expected intervals, see ltx_tiling.py's module docstring
        point 5/derivation."""
        intervals = _split_temporal_for_encode(121, tile_size_frames=64, overlap_frames=24)
        lengths = [iv.end - iv.start for iv in intervals]
        assert lengths == [65, 65, 41]


# ---------------------------------------------------------------------------
# Pure formula tests -- spatial split + mapping functions
# ---------------------------------------------------------------------------


class TestSpatialSplitAndMapping:
    def test_split_by_size_no_split_when_within_tile(self):
        assert _split_by_size(64, 96, 64) == [(0, 64, 0, 0)]

    def test_split_by_size_three_tiles(self):
        # H=160, tile=96, overlap=64 -> matches the module's own worked example.
        intervals = _split_by_size(160, 96, 64)
        assert [(iv.start, iv.end) for iv in intervals] == [(0, 96), (32, 128), (64, 160)]
        assert intervals[0].right_ramp == 64 and intervals[0].left_ramp == 0
        assert intervals[-1].left_ramp == 64 and intervals[-1].right_ramp == 0

    def test_compute_rectangular_mask_1d_discards_hard_not_blended(self):
        mask = _compute_rectangular_mask_1d(6, left_ramp=2, right_ramp=1)
        assert torch.equal(mask, torch.tensor([0.0, 0.0, 1.0, 1.0, 1.0, 0.0]))

    def test_map_spatial_interval_to_latent_discard_math(self):
        # First tile of the 160/96/64 example: start=0,end=96,left=0,right=64, scale=32.
        out_slice, mask = _map_spatial_interval_to_latent(_Interval(0, 96, 0, 64), 32)
        assert out_slice == slice(0, 3)
        # left_ramp=max(0, 0//32 - 1)=0 ; right_ramp = 1 (has a right neighbor).
        assert torch.equal(mask, torch.tensor([1.0, 1.0, 0.0]))

        # Middle tile: start=32,end=128,left=64,right=64.
        out_slice2, mask2 = _map_spatial_interval_to_latent(_Interval(32, 128, 64, 64), 32)
        assert out_slice2 == slice(1, 4)
        # left_ramp = 64//32 - 1 = 1 ; right_ramp = 1.
        assert torch.equal(mask2, torch.tensor([0.0, 1.0, 0.0]))

    def test_map_temporal_interval_to_latent_rejects_nonzero_right_ramp(self):
        with pytest.raises(ValueError, match="right ramp equal to 0"):
            _map_temporal_interval_to_latent(_Interval(0, 65, 0, 8), 8)


# ---------------------------------------------------------------------------
# (a) Numerics: exact-match cases + weight-coverage invariants
# ---------------------------------------------------------------------------


class TestTiledEncodeNumerics:
    def test_none_config_is_bit_exact_with_whole_encode(self):
        """No tiling_config -> single default (whole-axis, zero-ramp) tile
        per axis -> mask is all-ones everywhere -> the divide is a no-op.
        This is the ONE case ltx-core's own algorithm guarantees exact
        parity with a plain `encode()` call."""
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 9, 64, 64) * 2.0 - 1.0
        with torch.no_grad():
            whole = vae.encode(pixels)
            tiled = vae.tiled_encode(pixels, None)
        assert torch.equal(whole, tiled)

    def test_tile_larger_than_video_is_bit_exact_with_whole_encode(self):
        """A tiling_config whose tile sizes exceed the video's own T/H/W also
        degenerates to a single tile per axis (via `_split_by_size`'s
        dimension_size <= size early return) -- exact parity again, this
        time going through the REAL config-driven code path (not `None`)."""
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 9, 64, 64) * 2.0 - 1.0
        huge_tiling = LtxTilingConfig(
            spatial=SpatialTilingConfig(tile_size_in_pixels=512, tile_overlap_in_pixels=64),
            temporal=TemporalTilingConfig(tile_size_in_frames=64, tile_overlap_in_frames=24),
        )
        with torch.no_grad():
            whole = vae.encode(pixels)
            tiled = vae.tiled_encode(pixels, huge_tiling)
        assert torch.equal(whole, tiled)

    def test_spatial_tiling_weight_coverage_is_exactly_one_everywhere(self):
        """Spatial-only tiling has no "+1"-style extension (unlike temporal),
        so ltx-core's discard math should partition the output latent
        EXACTLY -- no gaps (weight 0, which would corrupt output with the
        1e-8 clamp) and no overlaps (weight > 1, a silent 2-way average)."""
        video_shape = torch.Size([1, 3, 9, 160, 160])
        tiling = LtxTilingConfig(
            spatial=SpatialTilingConfig(tile_size_in_pixels=96, tile_overlap_in_pixels=64),
            temporal=None,
        )
        tiles = _prepare_tiles_for_encoding(video_shape, tiling)
        assert len(tiles) == 9  # 3x3 spatial grid, single temporal tile
        weight_sum = torch.zeros(1, 1, 2, 5, 5)
        for tile in tiles:
            weight_sum[tile.out_coords] += tile.blend_mask
        assert torch.equal(weight_sum, torch.ones_like(weight_sum))

    def test_temporal_tiling_weight_never_drops_below_one(self):
        """Temporal tiling's "+1" extension (module docstring point 7) makes
        exactly one latent per internal junction get weight 2 (a 2-way
        average, not a gap) -- verify there are NO true gaps (weight < 1)
        anywhere, and that weight > 1 only happens at the documented
        junction positions."""
        video_shape = torch.Size([1, 3, 41, 32, 32])
        tiling = LtxTilingConfig(
            spatial=None,
            temporal=TemporalTilingConfig(tile_size_in_frames=24, tile_overlap_in_frames=16),
        )
        tiles = _prepare_tiles_for_encoding(video_shape, tiling)
        weight_sum = torch.zeros(1, 1, 6, 1, 1)
        for tile in tiles:
            weight_sum[tile.out_coords] += tile.blend_mask
        flat = weight_sum.flatten()
        assert (flat >= 1.0).all(), f"gap detected: {flat}"
        # Hand-derived for this exact (T=41, size=24, overlap=16) case.
        assert torch.equal(flat, torch.tensor([1.0, 1.0, 1.0, 2.0, 2.0, 2.0]))

    def test_tiled_encode_output_is_finite_and_correct_shape_when_actually_tiled(self):
        """A genuinely multi-tile encode (spatial AND temporal both active).
        Deliberately NOT compared numerically to whole-clip encode here: for
        a network this shallow relative to how aggressively small the tiles
        are, ltx-core's fixed (receptive-field-independent) discard widths
        do not guarantee close agreement near seams -- see the module
        docstring's "Deviation from whole-clip encode" section. Shape/
        finiteness is the correctness bar for this case; exact-match cases
        above cover the numerically-guaranteed scenarios."""
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 41, 160, 160) * 2.0 - 1.0
        tiling = LtxTilingConfig(
            spatial=SpatialTilingConfig(tile_size_in_pixels=96, tile_overlap_in_pixels=64),
            temporal=TemporalTilingConfig(tile_size_in_frames=24, tile_overlap_in_frames=16),
        )
        with torch.no_grad():
            whole = vae.encode(pixels)
            tiled = vae.tiled_encode(pixels, tiling)
        assert tiled.shape == whole.shape
        assert torch.isfinite(tiled).all()

    def test_crops_invalid_frame_count_like_encode(self):
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 12, 64, 64) * 2.0 - 1.0  # 12 is not 1+8k
        with torch.no_grad():
            tiled = vae.tiled_encode(pixels, None)
        assert tiled.shape[2] == 2  # cropped to 9 frames -> (9-1)//8+1 = 2


# ---------------------------------------------------------------------------
# Independent (import-free) reimplementation of ltx-core's algorithm, written
# fresh from reading the source as spec -- NOT importing ltx_tiling's private
# helpers. Compared bit-for-bit against `LTXCausalVideoVAE.tiled_encode` on
# the same tiny VAE + tensors: if the port silently deviated from the
# reference, an independently-written transcription would need to share the
# exact same mistake for this test to pass by accident.
# ---------------------------------------------------------------------------


def _ref_split_by_size(dimension_size, size, overlap):
    if dimension_size <= size:
        return [(0, dimension_size, 0, 0)]
    amount = (dimension_size + size - 2 * overlap - 1) // (size - overlap)
    intervals = [(0, size, 0, overlap)]
    for i in range(1, amount - 1):
        start = i * (size - overlap)
        intervals.append((start, start + size, overlap, overlap))
    start_last = (amount - 1) * (size - overlap)
    intervals.append((start_last, dimension_size, overlap, 0))
    return intervals


def _ref_split_temporal(dimension_size, tile_size_frames, overlap_frames):
    if dimension_size <= tile_size_frames:
        return [(0, dimension_size, 0, 0)]
    base = _ref_split_by_size(dimension_size, tile_size_frames, overlap_frames)
    out = [(s, e + 1, lr, 0) for (s, e, lr, _rr) in base[:-1]]
    s, e, lr, _rr = base[-1]
    out.append((s, e, lr, 0))
    return out


def _ref_rect_mask(length, left_ramp, right_ramp):
    mask = torch.ones(length)
    if left_ramp > 0:
        mask[:left_ramp] = 0
    if right_ramp > 0:
        mask[-right_ramp:] = 0
    return mask


def _ref_map_spatial(begin, end, left_ramp, right_ramp, scale):
    start, stop = begin // scale, end // scale
    lr = max(0, left_ramp // scale - 1)
    rr = 0 if right_ramp == 0 else 1
    return slice(start, stop), _ref_rect_mask(stop - start, lr, rr)


def _ref_map_temporal(begin, end, left_ramp, right_ramp, scale):
    start = begin // scale
    stop = (end - 1) // scale + 1
    lr = 0 if left_ramp == 0 else 1 + (left_ramp - 1) // scale
    rr = right_ramp // scale
    assert rr == 0
    return slice(start, stop), _ref_rect_mask(stop - start, lr, rr)


def _ref_tiled_encode(vae, video, spatial_cfg, temporal_cfg):
    """Fresh reimplementation of `VideoEncoder.tiled_encode` /
    `prepare_tiles_for_encoding` for (B,3,T,H,W) video, T/H/W axes only."""
    _b, _c, t, h, w = video.shape
    scale_t, scale_h, scale_w = 8, 32, 32

    t_ivs = [(0, t, 0, 0)]
    h_ivs = [(0, h, 0, 0)]
    w_ivs = [(0, w, 0, 0)]
    if spatial_cfg is not None:
        overlap = max(spatial_cfg.tile_overlap_in_pixels, 64)
        h_ivs = _ref_split_by_size(h, spatial_cfg.tile_size_in_pixels, overlap)
        w_ivs = _ref_split_by_size(w, spatial_cfg.tile_size_in_pixels, overlap)
    if temporal_cfg is not None:
        overlap_f = max(temporal_cfg.tile_overlap_in_frames, 16)
        t_ivs = _ref_split_temporal(t, temporal_cfg.tile_size_in_frames, overlap_f)

    model_device = next(vae.parameters()).device
    model_dtype = next(vae.parameters()).dtype
    out_t = (t - 1) // scale_t + 1
    out_h = h // scale_h
    out_w = w // scale_w
    buf = torch.zeros((video.shape[0], vae.latent_channels, out_t, out_h, out_w), device=model_device, dtype=model_dtype)
    wbuf = torch.zeros_like(buf)

    for (ts, te, tl, tr) in t_ivs:
        t_out, t_mask = _ref_map_temporal(ts, te, tl, tr, scale_t)
        for (hs, he, hl, hr) in h_ivs:
            h_out, h_mask = _ref_map_spatial(hs, he, hl, hr, scale_h)
            for (ws, we, wl, wr) in w_ivs:
                w_out, w_mask = _ref_map_spatial(ws, we, wl, wr, scale_w)
                tile_pixels = video[:, :, ts:te, hs:he, ws:we]
                latent_tile = vae.encode(tile_pixels)
                mask = (t_mask.view(-1, 1, 1) * h_mask.view(1, -1, 1) * w_mask.view(1, 1, -1))
                mask = mask.view(1, 1, *mask.shape).to(device=model_device, dtype=model_dtype)
                buf[:, :, t_out, h_out, w_out] += latent_tile * mask
                wbuf[:, :, t_out, h_out, w_out] += mask

    return buf / wbuf.clamp(min=1e-8)


class TestAgainstIndependentReimplementation:
    def test_spatial_tiling_matches_independent_reimplementation_exactly(self):
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 9, 160, 160) * 2.0 - 1.0
        tiling = LtxTilingConfig(
            spatial=SpatialTilingConfig(tile_size_in_pixels=96, tile_overlap_in_pixels=64),
            temporal=None,
        )
        with torch.no_grad():
            ported = vae.tiled_encode(pixels, tiling)
            reference = _ref_tiled_encode(vae, pixels, tiling.spatial, tiling.temporal)
        assert torch.equal(ported, reference)

    def test_temporal_tiling_matches_independent_reimplementation_exactly(self):
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 41, 32, 32) * 2.0 - 1.0
        tiling = LtxTilingConfig(
            spatial=None,
            temporal=TemporalTilingConfig(tile_size_in_frames=24, tile_overlap_in_frames=16),
        )
        with torch.no_grad():
            ported = vae.tiled_encode(pixels, tiling)
            reference = _ref_tiled_encode(vae, pixels, tiling.spatial, tiling.temporal)
        assert torch.equal(ported, reference)

    def test_combined_spatial_and_temporal_tiling_matches_independent_reimplementation(self):
        vae = _build_real_scale_tiny()
        pixels = torch.rand(1, 3, 41, 160, 160) * 2.0 - 1.0
        tiling = LtxTilingConfig(
            spatial=SpatialTilingConfig(tile_size_in_pixels=96, tile_overlap_in_pixels=64),
            temporal=TemporalTilingConfig(tile_size_in_frames=24, tile_overlap_in_frames=16),
        )
        with torch.no_grad():
            ported = vae.tiled_encode(pixels, tiling)
            reference = _ref_tiled_encode(vae, pixels, tiling.spatial, tiling.temporal)
        assert torch.equal(ported, reference)
