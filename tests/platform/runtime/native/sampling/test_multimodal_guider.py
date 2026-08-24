"""Unit tests for the MultiModalGuider (Lightricks LTX-2.3 quality recipe).

Tests the combination formula, std-preserving rescale, per-modality slicing,
and forward-count optimisation (stg_scale=0, cfg=1, modality=1 skip their
respective passes).

Ported from the reference: ltx-core/components/guiders.py,
  ltx-pipelines/utils/denoisers.py (Apache-2.0, rev a2c3f24).
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import torch
import pytest

from src.platform.runtime.native.sampling.multimodal_guider import (
    MultiModalGuidance,
    MultiModalGuiderParams,
    multimodal_combine,
    _needs_uncond,
    _needs_perturbed,
    _needs_modality_off,
    _should_skip_step,
    LTX_23_VIDEO_PARAMS,
    LTX_23_AUDIO_PARAMS,
)


# -- multimodal_combine formula ------------------------------------------------

class TestMultimodalCombine:
    """Verify the combine formula matches the reference by hand-computing
    expected values for known inputs."""

    def test_cfg_only(self):
        """CFG with stg=0 and modality=1 (disabled) -> standard CFG formula."""
        params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=0.0, modality_scale=1.0, rescale_scale=0.0)
        cond = torch.tensor([1.0, 2.0, 3.0])
        uncond = torch.tensor([0.0, 0.0, 0.0])

        result = multimodal_combine(cond, uncond, 0.0, 0.0, params)

        # pred = cond + (3-1)*(cond - uncond) = cond + 2*cond = 3*cond
        expected = cond * 3.0
        torch.testing.assert_close(result, expected)

    def test_stg_only(self):
        """STG with cfg=1 (no CFG) and modality=1 (disabled)."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=1.5, modality_scale=1.0, rescale_scale=0.0)
        cond = torch.tensor([1.0, 2.0, 3.0])
        perturbed = torch.tensor([0.5, 1.0, 1.5])

        result = multimodal_combine(cond, 0.0, perturbed, 0.0, params)

        # pred = cond + 0*(cond-uncond) + 1.5*(cond-perturbed) + 0*(cond-mod)
        expected = cond + 1.5 * (cond - perturbed)
        torch.testing.assert_close(result, expected)

    def test_modality_only(self):
        """Modality guidance with cfg=1 (no CFG) and stg=0 (disabled)."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0, modality_scale=3.0, rescale_scale=0.0)
        cond = torch.tensor([1.0, 2.0])
        mod_off = torch.tensor([0.5, 1.0])

        result = multimodal_combine(cond, 0.0, 0.0, mod_off, params)

        expected = cond + 2.0 * (cond - mod_off)
        torch.testing.assert_close(result, expected)

    def test_full_formula(self):
        """All four terms active (reference LTX_2_3_PARAMS)."""
        params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=1.0, modality_scale=3.0, rescale_scale=0.0)
        cond = torch.tensor([2.0, 4.0])
        uncond = torch.tensor([1.0, 2.0])
        perturbed = torch.tensor([1.5, 3.0])
        mod_off = torch.tensor([1.8, 3.5])

        result = multimodal_combine(cond, uncond, perturbed, mod_off, params)

        expected = (
            cond
            + 2.0 * (cond - uncond)
            + 1.0 * (cond - perturbed)
            + 2.0 * (cond - mod_off)
        )
        torch.testing.assert_close(result, expected)

    def test_rescale_preserves_std(self):
        """Rescale blends pred.std toward cond.std."""
        params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=0.0, modality_scale=1.0,
                                         rescale_scale=0.7)
        torch.manual_seed(42)
        cond = torch.randn(128)
        uncond = torch.randn(128)

        result = multimodal_combine(cond, uncond, 0.0, 0.0, params)

        # Check that the result's std is closer to cond's than the unrescaled version
        unrescaled_params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=0.0,
                                                     modality_scale=1.0, rescale_scale=0.0)
        unrescaled = multimodal_combine(cond, uncond, 0.0, 0.0, unrescaled_params)

        cond_std = cond.std().item()
        result_std = result.std().item()
        unrescaled_std = unrescaled.std().item()

        # rescale=0.7 should bring result's std closer to cond's
        assert abs(result_std - cond_std) < abs(unrescaled_std - cond_std)

    def test_rescale_zero_is_noop(self):
        """rescale_scale=0.0 -> no rescaling applied."""
        params = MultiModalGuiderParams(cfg_scale=3.0, rescale_scale=0.0)
        cond = torch.tensor([1.0, 2.0])
        uncond = torch.tensor([0.0, 0.0])

        result = multimodal_combine(cond, uncond, 0.0, 0.0, params)
        expected = cond + 2.0 * (cond - uncond)
        torch.testing.assert_close(result, expected)


# -- forward-count optimisation ------------------------------------------------

class TestForwardSkipFlags:
    def test_cfg1_no_uncond(self):
        assert not _needs_uncond(MultiModalGuiderParams(cfg_scale=1.0))

    def test_cfg_gt1_needs_uncond(self):
        assert _needs_uncond(MultiModalGuiderParams(cfg_scale=3.0))

    def test_stg0_no_perturbed(self):
        assert not _needs_perturbed(MultiModalGuiderParams(stg_scale=0.0))

    def test_stg_gt0_needs_perturbed(self):
        assert _needs_perturbed(MultiModalGuiderParams(stg_scale=1.0))

    def test_modality1_no_off(self):
        assert not _needs_modality_off(MultiModalGuiderParams(modality_scale=1.0))

    def test_modality_gt1_needs_off(self):
        assert _needs_modality_off(MultiModalGuiderParams(modality_scale=3.0))

    def test_skip_step_0_never_skips(self):
        p = MultiModalGuiderParams(skip_step=0)
        for step in range(10):
            assert not _should_skip_step(p, step)

    def test_skip_step_1_skips_odd(self):
        p = MultiModalGuiderParams(skip_step=1)
        # skip_step=1: step % 2 != 0 -> skip
        assert not _should_skip_step(p, 0)
        assert _should_skip_step(p, 1)
        assert not _should_skip_step(p, 2)
        assert _should_skip_step(p, 3)


# -- MultiModalGuidance strategy (mock model_fn) ------------------------------

class TestMultiModalGuidanceForwardCount:
    """Count how many times model_fn is called under different param configs."""

    def _count_calls(self, video_params, audio_params=None, has_audio=False):
        """Run one step of MultiModalGuidance and count model_fn calls."""
        calls = []

        def mock_model_fn(x, sigma, cond_dict):
            calls.append(cond_dict.copy())
            return x.clone()

        v_tokens = 10
        if has_audio:
            x = torch.randn(1, v_tokens + 5, 4)
        else:
            x = torch.randn(1, v_tokens, 4)

        cond = {"context": "pos", "mm_video_tokens": v_tokens}
        uncond = {"context": "neg", "mm_video_tokens": v_tokens}

        strategy = MultiModalGuidance(video_params, audio_params)
        strategy(mock_model_fn, x, torch.tensor([1.0]), cond, uncond, step_index=0)
        return len(calls), calls

    def test_full_quality_recipe_4_forwards(self):
        """LTX_2_3_PARAMS default: cfg=3, stg=1, modality=3 -> 4 forwards."""
        count, _ = self._count_calls(
            LTX_23_VIDEO_PARAMS,
            LTX_23_AUDIO_PARAMS,
            has_audio=True,
        )
        assert count == 4  # cond, uncond, stg, modality-off

    def test_cfg_only_2_forwards(self):
        """cfg=3 but stg=0 and modality=1 -> 2 forwards (cond + uncond)."""
        params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=0.0, modality_scale=1.0)
        count, _ = self._count_calls(params)
        assert count == 2

    def test_no_guidance_1_forward(self):
        """cfg=1, stg=0, modality=1 -> 1 forward (cond only).
        _count_calls always passes uncond, but _needs_uncond returns False for
        cfg=1 so the uncond forward is skipped."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0, modality_scale=1.0)
        count, _ = self._count_calls(params)
        assert count == 1

    def test_stg_forward_carries_skip_blocks(self):
        """STG forward should pass stg_skip_blocks in conditioning dict."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=1.0,
                                         stg_blocks=[28], modality_scale=1.0)
        _, calls = self._count_calls(params)
        # calls: [cond, stg-perturbed]
        assert len(calls) == 2
        stg_call = calls[1]
        assert stg_call.get("stg_skip_blocks") == [28]

    def test_modality_forward_carries_disable_flag(self):
        """Modality-off forward should pass disable_cross_modal."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0, modality_scale=3.0)
        _, calls = self._count_calls(params, has_audio=True,
                                     audio_params=MultiModalGuiderParams(modality_scale=3.0))
        # calls: [cond, modality-off]
        assert len(calls) == 2
        mod_call = calls[1]
        assert mod_call.get("disable_cross_modal") is True

    def test_video_only_no_modality_forward(self):
        """Without audio, modality_scale > 1 still doesn't produce a forward."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0, modality_scale=3.0)
        count, _ = self._count_calls(params, has_audio=False)
        assert count == 1  # only cond

    def test_skip_step_reuses_previous(self):
        """skip_step=1 reuses step 0's output at step 1."""
        params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=0.0,
                                         modality_scale=1.0, skip_step=1)
        calls = []

        def mock_model_fn(x, sigma, cond_dict):
            calls.append(1)
            return x * 0.5

        x = torch.randn(1, 10, 4)
        cond = {"context": "pos", "mm_video_tokens": 10}
        uncond = {"context": "neg", "mm_video_tokens": 10}

        strategy = MultiModalGuidance(params)
        out0 = strategy(mock_model_fn, x, torch.tensor([1.0]), cond, uncond, step_index=0)
        calls_step0 = len(calls)

        out1 = strategy(mock_model_fn, x, torch.tensor([0.5]), cond, uncond, step_index=1)
        calls_step1 = len(calls) - calls_step0

        assert calls_step0 == 2  # cond + uncond
        assert calls_step1 == 0  # skipped, reused
        torch.testing.assert_close(out0, out1)


class TestMultiModalGuidancePerModality:
    """Verify per-modality combination over packed video+audio state."""

    def test_separate_video_audio_combine(self):
        """Each modality slice gets its own params applied."""
        v_tokens = 5
        a_tokens = 3
        x = torch.randn(1, v_tokens + a_tokens, 4)

        # Setup: video cfg=2, audio cfg=4 (different params)
        v_params = MultiModalGuiderParams(cfg_scale=2.0, stg_scale=0.0, modality_scale=1.0, rescale_scale=0.0)
        a_params = MultiModalGuiderParams(cfg_scale=4.0, stg_scale=0.0, modality_scale=1.0, rescale_scale=0.0)

        cond_out = torch.randn(1, v_tokens + a_tokens, 4)
        uncond_out = torch.randn(1, v_tokens + a_tokens, 4)

        call_idx = [0]
        def mock_model_fn(x, sigma, cond_dict):
            if call_idx[0] == 0:
                call_idx[0] += 1
                return cond_out.clone()
            else:
                call_idx[0] += 1
                return uncond_out.clone()

        cond = {"context": "pos", "mm_video_tokens": v_tokens}
        uncond = {"context": "neg", "mm_video_tokens": v_tokens}

        strategy = MultiModalGuidance(v_params, a_params)
        result = strategy(mock_model_fn, x, torch.tensor([1.0]), cond, uncond, step_index=0)

        # Verify video slice: cond + (2-1)*(cond-uncond) = cond + (cond-uncond) = 2*cond - uncond
        expected_v = cond_out[:, :v_tokens] + 1.0 * (cond_out[:, :v_tokens] - uncond_out[:, :v_tokens])
        torch.testing.assert_close(result[:, :v_tokens], expected_v)

        # Verify audio slice: cond + (4-1)*(cond-uncond) = 4*cond - 3*uncond
        expected_a = cond_out[:, v_tokens:] + 3.0 * (cond_out[:, v_tokens:] - uncond_out[:, v_tokens:])
        torch.testing.assert_close(result[:, v_tokens:], expected_a)


class TestModalityGuidanceNeutrality:
    """Video-only (no audio slice) must apply a neutral modality
    term -- the skipped modality-off forward's ``0.0`` sentinel must not be
    multiplied by a non-zero ``(modality_scale - 1)`` coefficient."""

    def test_video_only_minimal_case_equals_cond(self):
        """cfg=1, stg=0, rescale=0, modality=3, no audio -> output == cond."""
        params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0,
                                         modality_scale=3.0, rescale_scale=0.0)
        v_tokens = 6
        x = torch.randn(1, v_tokens, 4)

        calls = []

        def mock_model_fn(x, sigma, cond_dict):
            calls.append(cond_dict.copy())
            return x.clone()

        cond = {"context": "pos", "mm_video_tokens": v_tokens}
        uncond = {"context": "neg", "mm_video_tokens": v_tokens}

        strategy = MultiModalGuidance(params)
        result = strategy(mock_model_fn, x, torch.tensor([1.0]), cond, uncond, step_index=0)

        assert len(calls) == 1
        torch.testing.assert_close(result, x, atol=0.0, rtol=0.0)

    def test_video_only_cfg_gt1_equals_pure_cfg(self):
        """cfg=3, stg=0, modality=3, no audio -> equals plain CFG combine
        (the modality term must contribute nothing)."""
        params = MultiModalGuiderParams(cfg_scale=3.0, stg_scale=0.0,
                                         modality_scale=3.0, rescale_scale=0.0)
        v_tokens = 5
        x = torch.randn(1, v_tokens, 4)
        cond_out = torch.randn(1, v_tokens, 4)
        uncond_out = torch.randn(1, v_tokens, 4)

        calls = []

        def mock_model_fn(x, sigma, cond_dict):
            calls.append(cond_dict.copy())
            return cond_out.clone() if len(calls) == 1 else uncond_out.clone()

        cond = {"context": "pos", "mm_video_tokens": v_tokens}
        uncond = {"context": "neg", "mm_video_tokens": v_tokens}

        strategy = MultiModalGuidance(params)
        result = strategy(mock_model_fn, x, torch.tensor([1.0]), cond, uncond, step_index=0)

        assert len(calls) == 2  # cond + uncond only, no modality-off forward
        expected = cond_out + 2.0 * (cond_out - uncond_out)
        torch.testing.assert_close(result, expected)

    def test_joint_av_modality_off_unaffected(self):
        """With audio present, the modality-off forward still runs and its
        real output is used -- the neutralisation must not touch this path."""
        v_tokens = 4
        a_tokens = 3
        x = torch.randn(1, v_tokens + a_tokens, 4)

        v_params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0,
                                           modality_scale=3.0, rescale_scale=0.0)
        a_params = MultiModalGuiderParams(cfg_scale=1.0, stg_scale=0.0,
                                           modality_scale=1.0, rescale_scale=0.0)

        cond_out = torch.randn(1, v_tokens + a_tokens, 4)
        mod_out = torch.randn(1, v_tokens + a_tokens, 4)

        calls = []

        def mock_model_fn(x, sigma, cond_dict):
            calls.append(cond_dict.copy())
            if cond_dict.get("disable_cross_modal"):
                return mod_out.clone()
            return cond_out.clone()

        cond = {"context": "pos", "mm_video_tokens": v_tokens}
        uncond = {"context": "neg", "mm_video_tokens": v_tokens}

        strategy = MultiModalGuidance(v_params, a_params)
        result = strategy(mock_model_fn, x, torch.tensor([1.0]), cond, uncond, step_index=0)

        assert len(calls) == 2  # cond + modality-off (cfg/stg both disabled)

        expected_video = (
            cond_out[:, :v_tokens] + 2.0 * (cond_out[:, :v_tokens] - mod_out[:, :v_tokens])
        )
        expected_audio = cond_out[:, v_tokens:]  # a_params.modality_scale == 1.0

        torch.testing.assert_close(result[:, :v_tokens], expected_video)
        torch.testing.assert_close(result[:, v_tokens:], expected_audio)


# -- reference constant checks -------------------------------------------------

class TestLTX23ParamsConstants:
    """Verify our ported constants match the reference's LTX_2_3_PARAMS."""

    def test_video_defaults(self):
        assert LTX_23_VIDEO_PARAMS.cfg_scale == 3.0
        assert LTX_23_VIDEO_PARAMS.stg_scale == 1.0
        assert LTX_23_VIDEO_PARAMS.rescale_scale == 0.7
        assert LTX_23_VIDEO_PARAMS.modality_scale == 3.0
        assert LTX_23_VIDEO_PARAMS.stg_blocks == [28]
        assert LTX_23_VIDEO_PARAMS.skip_step == 0

    def test_audio_defaults(self):
        assert LTX_23_AUDIO_PARAMS.cfg_scale == 7.0
        assert LTX_23_AUDIO_PARAMS.stg_scale == 1.0
        assert LTX_23_AUDIO_PARAMS.rescale_scale == 0.7
        assert LTX_23_AUDIO_PARAMS.modality_scale == 3.0
        assert LTX_23_AUDIO_PARAMS.stg_blocks == [28]
        assert LTX_23_AUDIO_PARAMS.skip_step == 0
