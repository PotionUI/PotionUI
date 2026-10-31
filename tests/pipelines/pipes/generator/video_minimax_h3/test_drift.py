from __future__ import annotations

import numpy as np
import pytest

from src.pipelines.pipes.generator.video_minimax_h3.drift import (
    DriftGuard,
    colour_stats,
    correct_drift,
)
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe


def _clip(mean, std, *, frames=8, size=16, seed=0):
    rng = np.random.default_rng(seed)
    data = rng.normal(loc=mean, scale=std, size=(frames, size, size, 3))
    return np.clip(data, 0, 255).astype(np.uint8)


def test_stats_describe_the_channel_mean_and_spread():
    stats = colour_stats(_clip([100, 120, 140], 20, frames=16, size=32))
    assert stats.mean == pytest.approx([100, 120, 140], abs=3)
    assert stats.std == pytest.approx([20, 20, 20], abs=3)


def test_zero_strength_returns_the_frames_untouched():
    frames = _clip([90, 90, 90], 15)
    anchor = colour_stats(_clip([140, 140, 140], 15, seed=1))
    assert correct_drift(frames, anchor, 0.0) is frames


def test_full_strength_pulls_a_darkened_window_back_to_the_anchor():
    anchor = colour_stats(_clip([140, 130, 120], 25, seed=1))
    drifted = _clip([100, 100, 100], 25, seed=2)
    fixed = colour_stats(correct_drift(drifted, anchor, 1.0))
    assert fixed.mean == pytest.approx(anchor.mean, abs=4)


def test_partial_strength_lands_between_the_window_and_the_anchor():
    anchor = colour_stats(_clip([160, 160, 160], 20, seed=1))
    drifted = _clip([100, 100, 100], 20, seed=2)
    before = colour_stats(drifted).mean[0]
    half = colour_stats(correct_drift(drifted, anchor, 0.5)).mean[0]
    assert before < half < anchor.mean[0]


def test_the_contrast_correction_is_capped():
    anchor = colour_stats(_clip([128, 128, 128], 80, seed=1))
    flat = _clip([128, 128, 128], 5, seed=2)
    fixed = colour_stats(correct_drift(flat, anchor, 1.0))
    assert fixed.std[0] < 5 * 1.25 + 2


def test_the_output_keeps_shape_and_dtype_and_stays_in_range():
    anchor = colour_stats(_clip([250, 250, 250], 5, seed=1))
    out = correct_drift(_clip([10, 10, 10], 5, frames=20), anchor, 1.0)
    assert out.shape == (20, 16, 16, 3) and out.dtype == np.uint8
    assert out.min() >= 0 and out.max() <= 255


def test_the_first_window_is_the_anchor_and_is_never_changed():
    guard = DriftGuard(1.0)
    first = _clip([140, 140, 140], 20)
    assert guard.apply(first, continues_previous=False) is first


def test_a_continuing_window_is_pulled_toward_the_first():
    guard = DriftGuard(1.0)
    guard.apply(_clip([140, 140, 140], 20, seed=1), continues_previous=False)
    out = guard.apply(_clip([100, 100, 100], 20, seed=2), continues_previous=True)
    assert colour_stats(out).mean[0] == pytest.approx(140, abs=4)


def test_a_hard_cut_resets_the_anchor():
    guard = DriftGuard(1.0)
    guard.apply(_clip([140, 140, 140], 20, seed=1), continues_previous=False)
    cut = _clip([60, 60, 60], 20, seed=2)
    assert guard.apply(cut, continues_previous=False) is cut
    follow = guard.apply(_clip([90, 90, 90], 20, seed=3), continues_previous=True)
    assert colour_stats(follow).mean[0] == pytest.approx(60, abs=4)


def test_a_disabled_guard_passes_everything_through():
    guard = DriftGuard(0.0)
    first = _clip([140, 140, 140], 20)
    second = _clip([60, 60, 60], 20, seed=2)
    assert guard.apply(first, continues_previous=False) is first
    assert guard.apply(second, continues_previous=True) is second


def test_the_pipe_declares_the_knob_with_an_off_default():
    spec = next(s for s in GeneratorMinimaxH3Pipe.configuration() if s.name == "drift_correction")
    assert spec.default == 0.0
    assert (spec.min_value, spec.max_value) == (0.0, 1.0)
    assert spec.required is False


def test_partial_strength_also_scales_the_contrast_partway():
    anchor = colour_stats(_clip([128, 128, 128], 40, seed=1))
    flat = _clip([128, 128, 128], 30, seed=2)
    before = colour_stats(flat).std[0]
    half = colour_stats(correct_drift(flat, anchor, 0.5)).std[0]
    full = colour_stats(correct_drift(flat, anchor, 1.0)).std[0]
    assert before < half < full
