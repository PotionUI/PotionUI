"""Tests for the beta/exponential schedule options and the detail-daemon warp."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


def _assert_strictly_decreasing_and_terminal(sigmas: torch.Tensor, steps: int):
    assert sigmas.shape == (steps + 1,)
    assert sigmas[0].item() == pytest.approx(1.0, abs=1e-6)
    assert sigmas[-1].item() == 0.0
    diffs = sigmas[1:] - sigmas[:-1]
    assert torch.all(diffs < 0)


# --------------------------------------------------------------------------- #
# default behaviour is unchanged (byte-identical regression)
# --------------------------------------------------------------------------- #

def test_default_schedule_unaffected_by_new_params():
    a = build_sigmas(20, shift=2.02)
    b = build_sigmas(20, shift=2.02, schedule=None, detail_strength=0.0)
    assert torch.equal(a, b)


def test_default_detail_strength_is_noop_for_every_schedule_family():
    baseline_shift = build_sigmas(24, shift=2.02)
    baseline_flux = build_sigmas(24, base_shift=0.5, max_shift=1.15, image_seq_len=4096)
    baseline_beta = build_sigmas(24, schedule="beta")
    baseline_exp = build_sigmas(24, schedule="exponential")

    assert torch.equal(baseline_shift, build_sigmas(24, shift=2.02, detail_strength=0.0))
    assert torch.equal(
        baseline_flux,
        build_sigmas(24, base_shift=0.5, max_shift=1.15, image_seq_len=4096, detail_strength=0.0),
    )
    assert torch.equal(baseline_beta, build_sigmas(24, schedule="beta", detail_strength=0.0))
    assert torch.equal(baseline_exp, build_sigmas(24, schedule="exponential", detail_strength=0.0))


# --------------------------------------------------------------------------- #
# exponential schedule
# --------------------------------------------------------------------------- #

def test_exponential_schedule_monotonic_and_endpoints():
    sigmas = build_sigmas(16, schedule="exponential")
    _assert_strictly_decreasing_and_terminal(sigmas, 16)


def test_exponential_schedule_respects_sigma_min_option():
    import math

    n = 8
    sigma_min = 0.05
    sigmas = build_sigmas(n, schedule="exponential", schedule_options={"sigma_min": sigma_min})
    # sigmas[k] == exp(log(sigma_min) * k/n) for interior points (terminal is
    # forced to exact 0 regardless of sigma_min).
    expected_second_to_last = math.exp(math.log(sigma_min) * (n - 1) / n)
    assert sigmas[-2].item() == pytest.approx(expected_second_to_last, rel=1e-5)
    # A smaller sigma_min pulls every interior sigma down.
    sigmas_smaller_min = build_sigmas(n, schedule="exponential", schedule_options={"sigma_min": 0.001})
    assert sigmas_smaller_min[-2].item() < sigmas[-2].item()


def test_exponential_invalid_sigma_min_raises():
    with pytest.raises(ValueError):
        build_sigmas(4, schedule="exponential", schedule_options={"sigma_min": 0.0})


# --------------------------------------------------------------------------- #
# beta schedule
# --------------------------------------------------------------------------- #

def test_beta_schedule_monotonic_and_endpoints():
    sigmas = build_sigmas(16, schedule="beta")
    _assert_strictly_decreasing_and_terminal(sigmas, 16)


def test_beta_schedule_custom_alpha_beta():
    sigmas = build_sigmas(16, schedule="beta", schedule_options={"alpha": 0.4, "beta": 0.9})
    _assert_strictly_decreasing_and_terminal(sigmas, 16)


def test_beta_schedule_length_matches_steps():
    for steps in (1, 4, 8, 32):
        sigmas = build_sigmas(steps, schedule="beta")
        assert sigmas.shape == (steps + 1,)


# --------------------------------------------------------------------------- #
# denoise truncation still applies to the new schedules
# --------------------------------------------------------------------------- #

def test_exponential_schedule_truncates_with_denoise():
    sigmas = build_sigmas(4, schedule="exponential", denoise=0.5)
    assert sigmas.shape == (5,)
    assert sigmas[0].item() < 1.0
    assert sigmas[-1].item() == 0.0


def test_beta_schedule_truncates_with_denoise():
    sigmas = build_sigmas(4, schedule="beta", denoise=0.5)
    assert sigmas.shape == (5,)
    assert sigmas[0].item() < 1.0
    assert sigmas[-1].item() == 0.0


def test_unknown_schedule_raises():
    with pytest.raises(ValueError):
        build_sigmas(4, schedule="bogus")


# --------------------------------------------------------------------------- #
# detail-daemon warp
# --------------------------------------------------------------------------- #

def test_detail_warp_preserves_endpoints_and_length():
    sigmas = build_sigmas(20, shift=2.02, detail_strength=0.2)
    assert sigmas.shape == (21,)
    assert sigmas[0].item() == pytest.approx(1.0, abs=1e-6)
    assert sigmas[-1].item() == 0.0


def test_detail_warp_stays_strictly_decreasing_within_bounds():
    for strength in (-0.3, -0.15, 0.15, 0.3):
        sigmas = build_sigmas(30, shift=2.02, detail_strength=strength)
        diffs = sigmas[1:] - sigmas[:-1]
        assert torch.all(diffs < 0), f"strength={strength} broke monotonicity"
        assert torch.all(sigmas >= 0.0)
        assert torch.all(sigmas <= sigmas[0])


def test_detail_warp_actually_changes_interior_sigmas():
    baseline = build_sigmas(20, shift=2.02)
    warped = build_sigmas(20, shift=2.02, detail_strength=0.25)
    assert not torch.equal(baseline, warped)
    assert torch.equal(baseline[0], warped[0])
    assert torch.equal(baseline[-1], warped[-1])


def test_detail_warp_window_bounds_respected():
    # A window collapsed to a single point (start==end) is a no-op guard clause.
    baseline = build_sigmas(20, shift=2.02)
    warped = build_sigmas(20, shift=2.02, detail_strength=0.25, detail_start=0.5, detail_end=0.5)
    assert torch.equal(baseline, warped)


def test_detail_warp_composes_with_exponential_schedule():
    sigmas = build_sigmas(20, schedule="exponential", detail_strength=0.2)
    assert sigmas.shape == (21,)
    assert sigmas[0].item() == pytest.approx(1.0, abs=1e-6)
    assert sigmas[-1].item() == 0.0
    diffs = sigmas[1:] - sigmas[:-1]
    assert torch.all(diffs < 0)


# --------------------------------------------------------------------------- #
# manual schedule (ComfyUI ManualSigmas-style distilled-refine
# recipe -- steps/shift/denoise are ignored, the list's length IS the count)
# --------------------------------------------------------------------------- #

# The maintainer's validated ComfyUI LTX-2.3 distilled-refine recipe (see
# docs/models/ltx.md): 5 dense high-sigma steps, a fast drop to a near-clean
# 0.1 residual, then one final step to 0.0.
_MAINTAINER_RECIPE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"


def test_manual_schedule_parses_comma_separated_string_exactly():
    sigmas = build_sigmas(24, schedule="manual", schedule_options={"sigmas": _MAINTAINER_RECIPE})
    expected = [1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0]
    assert sigmas.shape == (9,)
    assert torch.allclose(sigmas, torch.tensor(expected), atol=1e-6)


def test_manual_schedule_accepts_pre_parsed_sequence():
    values = [1.0, 0.7, 0.3, 0.0]
    from_string = build_sigmas(99, schedule="manual", schedule_options={"sigmas": "1.0, 0.7, 0.3, 0.0"})
    from_list = build_sigmas(99, schedule="manual", schedule_options={"sigmas": values})
    assert torch.equal(from_string, from_list)


def test_manual_schedule_length_ignores_steps_argument():
    # `steps` is a required positional/keyword arg but must have NO bearing on
    # a manual schedule's length -- the maintainer's 9-value recipe must come
    # back as 9 values whether `steps` says 1, 24, or 100.
    for steps in (1, 24, 100):
        sigmas = build_sigmas(steps, schedule="manual", schedule_options={"sigmas": _MAINTAINER_RECIPE})
        assert sigmas.shape == (9,)


def test_manual_schedule_ignores_denoise_truncation():
    full = build_sigmas(24, schedule="manual", schedule_options={"sigmas": _MAINTAINER_RECIPE})
    truncated_denoise = build_sigmas(
        24, schedule="manual", schedule_options={"sigmas": _MAINTAINER_RECIPE}, denoise=0.5,
    )
    assert torch.equal(full, truncated_denoise)


def test_manual_schedule_forces_exact_head_and_tail():
    # A hand-typed list drifting a hair off 1.0/0.0 (copy-paste rounding) is
    # still snapped to the exact contract every other schedule guarantees.
    sigmas = build_sigmas(1, schedule="manual", schedule_options={"sigmas": [0.998, 0.5, 0.0002]})
    assert sigmas[0].item() == 1.0
    assert sigmas[-1].item() == 0.0
    assert sigmas[1].item() == pytest.approx(0.5, abs=1e-6)


def test_manual_schedule_rejects_ascending_values():
    with pytest.raises(ValueError):
        build_sigmas(1, schedule="manual", schedule_options={"sigmas": "0.5, 0.9, 0.0"})


def test_manual_schedule_rejects_too_few_values():
    with pytest.raises(ValueError):
        build_sigmas(1, schedule="manual", schedule_options={"sigmas": "1.0"})


def test_manual_schedule_rejects_missing_sigmas_option():
    with pytest.raises((ValueError, TypeError)):
        build_sigmas(1, schedule="manual", schedule_options={})


def test_manual_schedule_composes_with_detail_daemon_warp():
    baseline = build_sigmas(24, schedule="manual", schedule_options={"sigmas": _MAINTAINER_RECIPE})
    warped = build_sigmas(
        24, schedule="manual", schedule_options={"sigmas": _MAINTAINER_RECIPE}, detail_strength=0.2,
    )
    assert baseline.shape == warped.shape
    assert not torch.equal(baseline, warped)
    assert torch.equal(baseline[0], warped[0])
    assert torch.equal(baseline[-1], warped[-1])
