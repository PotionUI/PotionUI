"""Tests for the LTX two-stage upscale geometry preflight helpers
(`src/pipelines/pipes/latent_upscaler/ltx/geometry.py`).

Verifies the proposal's divisibility claims against the actual resampler
math (`LTXLatentUpsampler`'s `_SpatialRationalResampler`/`_BlurDownsample`,
`src/platform/runtime/native/vae/ltx_latent_upsampler.py`) rather than
trusting a hand-rederived formula:

- 2.0x (`den=1`) always agrees -- confirmed by an exhaustive sweep below.
- 1.5x (`den=2`) requires each pixel axis to be a multiple of 64px, NOT 96px
  as an earlier proposal claimed -- `required_axis_divisor(1.5) == 64` is
  asserted directly, and the sweep cross-checks that rule against the real
  simulation for every 32px-aligned axis up to a generous bound.
"""

from __future__ import annotations

import pytest

from src.pipelines.pipes.latent_upscaler.ltx.geometry import (
    temporal_upsample_out_frames,
    compute_two_stage_geometry,
    nearest_achievable_pixel,
    nearest_achievable_resolution,
    required_axis_divisor,
)
from src.pipelines.pipes.generator.txt2vid_ltx.main import _SPATIAL_DOWNSCALE


class TestRequiredAxisDivisor:
    def test_1_5x_requires_64px_not_96px(self):
        """Refutes the "divisible by 96" proposal -- the real requirement,
        derived from `_rational_for_scale(1.5) == (3, 2)`, is
        `_SPATIAL_DOWNSCALE * den == 32 * 2 == 64`."""
        assert required_axis_divisor(1.5) == 64

    def test_2_0x_requires_only_the_ordinary_32px_grid(self):
        assert required_axis_divisor(2.0) == 32


class TestComputeTwoStageGeometryConfirmedCases:
    """Concrete before/after numbers, confirmed against the real resampler
    formula (not hand algebra) -- see the module docstring."""

    def test_default_resolution_768x512_passes_at_1_5x(self):
        """The preset's own default (both axes already 64px-divisible:
        768/64=12, 512/64=8) is NOT the maintainer's failing case."""
        g = compute_two_stage_geometry(768, 512, 1.5)
        assert g.ok
        assert (g.actual_width_lat, g.actual_height_lat) == (g.expected_width_lat, g.expected_height_lat)

    def test_default_resolution_768x512_passes_at_2_0x(self):
        g = compute_two_stage_geometry(768, 512, 2.0)
        assert g.ok

    def test_hq_portrait_544x960_fails_at_1_5x(self):
        """544 is 32px-aligned but NOT 64px-aligned (544/64=8.5) -- the
        preset's own "HQ Portrait" resolution picker entry. Stage 1's
        resampled latent (26 wide) disagrees with stage 2's independently
        snapped config (25 wide) by exactly 1 latent column."""
        g = compute_two_stage_geometry(544, 960, 1.5)
        assert not g.ok
        assert g.actual_width_lat == 26
        assert g.expected_width_lat == 25
        assert g.actual_height_lat == g.expected_height_lat == 45

    def test_hq_portrait_544x960_passes_at_2_0x(self):
        g = compute_two_stage_geometry(544, 960, 2.0)
        assert g.ok

    def test_hq_landscape_960x544_fails_at_1_5x(self):
        g = compute_two_stage_geometry(960, 544, 1.5)
        assert not g.ok
        assert g.actual_height_lat == 26
        assert g.expected_height_lat == 25

    def test_off_grid_axis_reports_expected_lower_than_actual(self):
        """The mismatch always resolves the same direction: the resampler's
        real output is exactly 1 latent unit HIGHER than stage 2's
        independently (round-then-snap) configured expectation -- ties in
        `snap_to_multiple` round down, the resampler's blur_down floors then
        adds 1."""
        g = compute_two_stage_geometry(832, 480, 1.5)
        assert not g.ok
        assert g.actual_height_lat == g.expected_height_lat + 1


class TestSweepAgainstRealSimulation:
    """Cross-checks `required_axis_divisor`'s closed-form rule against the
    actual per-axis simulation (`compute_two_stage_geometry`) over every
    32px-aligned pixel value up to a generous bound, for both exposed UI
    scales -- proves the divisor is both necessary AND sufficient, not just
    true for the handful of concrete cases above."""

    @pytest.mark.parametrize("scale", [1.5, 2.0])
    def test_divisor_rule_matches_simulation_exactly(self, scale):
        divisor = required_axis_divisor(scale)
        mismatches = []
        for pixel in range(_SPATIAL_DOWNSCALE, _SPATIAL_DOWNSCALE * 64 + 1, _SPATIAL_DOWNSCALE):
            g = compute_two_stage_geometry(pixel, pixel, scale)
            predicted_ok = (pixel % divisor == 0)
            if predicted_ok != g.ok:
                mismatches.append((pixel, predicted_ok, g.ok))
        assert mismatches == []

    def test_2_0x_is_always_achievable_on_the_32px_grid(self):
        for pixel in range(_SPATIAL_DOWNSCALE, _SPATIAL_DOWNSCALE * 64 + 1, _SPATIAL_DOWNSCALE):
            assert compute_two_stage_geometry(pixel, pixel, 2.0).ok

    def test_video_yml_resolution_picker_entries(self):
        """Every option in content/presets/_shared/resolutions/video.yml, at 1.5x --
        locks in exactly which of the preset's own picker entries are
        currently broken, so a future resampler change that fixes (or
        regresses) any of these is caught here rather than only in a live
        generation."""
        expected_ok = {
            (832, 480): False, (480, 832): False,
            (960, 544): False, (544, 960): False,
            (640, 480): False, (480, 640): False,
            (1280, 720): True, (720, 1280): True,
        }
        for (w, h), ok in expected_ok.items():
            assert compute_two_stage_geometry(w, h, 1.5).ok is ok, f"{w}x{h}"

    def test_video_yml_resolution_picker_entries_all_pass_at_2_0x(self):
        for (w, h) in [(832, 480), (480, 832), (960, 544), (544, 960),
                       (640, 480), (480, 640), (1280, 720), (720, 1280)]:
            assert compute_two_stage_geometry(w, h, 2.0).ok


class TestNearestAchievable:
    def test_nearest_pixel_for_a_failing_axis_is_the_next_lower_64px_multiple(self):
        # 544 -> 26 latent (odd a=17); 512 (a=16, even) is the next 64px
        # multiple down and passes; 576 (a=18, even) is equidistant up but
        # snap_to_multiple's own round-down-on-ties direction is mirrored by
        # trying the lower candidate first.
        assert nearest_achievable_pixel(544, 1.5) == 512

    def test_nearest_pixel_is_a_no_op_when_already_achievable(self):
        assert nearest_achievable_pixel(768, 1.5) == 768
        assert nearest_achievable_pixel(512, 1.5) == 512

    def test_nearest_resolution_computes_each_axis_independently(self):
        assert nearest_achievable_resolution(960, 544, 1.5) == (960, 512)
        assert nearest_achievable_resolution(544, 960, 1.5) == (512, 960)

    def test_nearest_resolution_itself_is_achievable(self):
        for (w, h) in [(832, 480), (960, 544), (640, 480)]:
            sw, sh = nearest_achievable_resolution(w, h, 1.5)
            assert compute_two_stage_geometry(sw, sh, 1.5).ok, f"suggested {sw}x{sh} for {w}x{h}"


class TestStage1SnapIsRespected:
    def test_non_32px_aligned_request_is_snapped_before_the_check(self):
        """An off-grid request (e.g. from a custom width/height field) is
        snapped to the 32px grid FIRST (mirrors generator/video_ltx's own
        `_snap_geometry`), same as stage 1 would actually do, before the
        two-stage comparison runs."""
        g = compute_two_stage_geometry(770, 510, 1.5)
        assert (g.stage1_width, g.stage1_height) == (768, 512)
        assert g.ok


class TestTemporalUpsampleOutFrames:
    """``T -> 2T - 1``, the mapping a temporal x2 round applies. The arch's
    real forward is pinned against this helper in
    ``tests/platform/runtime/native/vae/test_ltx_latent_upsampler.py``."""

    @pytest.mark.parametrize("frames_in,frames_out", [(1, 1), (2, 3), (3, 5), (9, 17), (16, 31), (121, 241)])
    def test_mapping(self, frames_in, frames_out):
        assert temporal_upsample_out_frames(frames_in) == frames_out

    def test_a_single_frame_is_a_fixed_point(self):
        """T=1 upsamples to 2 and drops one, so a still stays a still."""
        assert temporal_upsample_out_frames(1) == 1

    def test_the_latent_and_pixel_mappings_agree(self):
        """Why one helper serves both: a latent count T corresponds to
        (T-1)*8+1 pixel frames, and the mapping commutes with that."""
        for t_lat in range(1, 40):
            pixels = (t_lat - 1) * 8 + 1
            out_lat = temporal_upsample_out_frames(t_lat)
            assert (out_lat - 1) * 8 + 1 == temporal_upsample_out_frames(pixels)

    def test_the_1_plus_8k_lattice_survives_a_round(self):
        for k in range(0, 20):
            pixels = 8 * k + 1
            assert (temporal_upsample_out_frames(pixels) - 1) % 8 == 0

    def test_rounds_compose(self):
        """Two temporal rounds are the spec's (N-1)*2**rounds + 1."""
        for n in (1, 9, 121):
            twice = temporal_upsample_out_frames(temporal_upsample_out_frames(n))
            assert twice == (n - 1) * 4 + 1

    def test_zero_frames_raises(self):
        with pytest.raises(ValueError, match="at least 1 frame"):
            temporal_upsample_out_frames(0)
