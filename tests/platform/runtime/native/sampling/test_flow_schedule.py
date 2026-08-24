"""Sigma-schedule tests: exact values vs hand-computed ComfyUI formulas."""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


def test_constant_shift_exact_values():
    # ModelSamplingDiscreteFlow: sigma = shift*t / (1 + (shift-1)*t), shift=2.02.
    sigmas = build_sigmas(4, shift=2.02)
    assert sigmas.shape == (5,)
    shift = 2.02
    expected = [shift * t / (1 + (shift - 1) * t) for t in (1.0, 0.75, 0.5, 0.25, 0.0)]
    # spot-check the literals so a formula regression is caught independently.
    assert expected[0] == pytest.approx(1.0)
    assert expected[1] == pytest.approx(0.8583569405, abs=1e-9)
    assert expected[2] == pytest.approx(0.6688741722, abs=1e-9)
    assert expected[3] == pytest.approx(0.4023904382, abs=1e-9)
    assert torch.allclose(sigmas, torch.tensor(expected, dtype=torch.float32), atol=1e-6)


def test_constant_shift_descending_and_terminal():
    sigmas = build_sigmas(20, shift=2.02)
    assert sigmas[0].item() == pytest.approx(1.0, abs=1e-6)
    assert sigmas[-1].item() == 0.0
    diffs = sigmas[1:] - sigmas[:-1]
    assert torch.all(diffs < 0)  # strictly descending


def test_shift_one_is_identity():
    sigmas = build_sigmas(4, shift=1.0)
    expected = torch.linspace(1.0, 0.0, 5)
    assert torch.allclose(sigmas, expected, atol=1e-6)


# --- LTX-2/2.3 (ltxav) shift vs the real diffusers scheduler ---------------
#
# diffusers' LTX2Pipeline (venv/lib/python3.12/site-packages/diffusers/pipelines/
# ltx2/pipeline_ltx2.py, ~line 1166) calls
#   mu = calculate_shift(scheduler.config.max_image_seq_len, base_image_seq_len,
#                         max_image_seq_len, base_shift, max_shift)
# with the *first* argument (image_seq_len) pinned to max_image_seq_len itself.
# calculate_shift is a linear interpolation between (base_seq_len, base_shift)
# and (max_seq_len, max_shift); evaluating it at x == max_seq_len returns
# max_shift exactly, so mu == scheduler.config.max_shift regardless of actual
# resolution — default 2.05 (FlowMatchEulerDiscreteScheduler.config.max_shift
# is not overridden by the ltx2 scheduler config). FlowMatchEulerDiscreteScheduler
# with use_dynamic_shifting=True then applies the "exponential" time_shift:
#   sigma = exp(mu) / (exp(mu) + (1/t - 1))
# which is algebraically identical to our constant-shift formula
# `shift * t / (1 + (shift - 1) * t)` with shift = exp(mu). This test proves
# that equivalence against the real scheduler class rather than assuming it.
LTXAV_SHIFT = 7.767901106306771  # exp(2.05)


@pytest.mark.parametrize("steps", [8, 24, 40])
def test_ltxav_shift_matches_diffusers_ltx2_scheduler(steps):
    import numpy as np
    from diffusers.schedulers.scheduling_flow_match_euler_discrete import (
        FlowMatchEulerDiscreteScheduler,
    )

    assert LTXAV_SHIFT == pytest.approx(math.exp(2.05), rel=0, abs=1e-12)

    # Mirrors LTX2Pipeline.__call__: sigmas = np.linspace(1.0, 1/steps, steps),
    # mu pinned at max_shift (2.05 default), dynamic shifting scheduler.
    scheduler = FlowMatchEulerDiscreteScheduler(use_dynamic_shifting=True)
    sigmas_in = np.linspace(1.0, 1 / steps, steps)
    scheduler.set_timesteps(sigmas=sigmas_in, mu=2.05)
    reference = scheduler.sigmas.to(torch.float64)

    ours = build_sigmas(steps, shift=LTXAV_SHIFT).to(torch.float64)

    assert reference.shape == ours.shape
    assert torch.allclose(reference, ours, rtol=1e-6, atol=1e-6)


def test_flux_dynamic_mu_exact_values():
    # ModelSamplingFlux: mu interpolated from seq_len, then flux_time_shift.
    # base=0.5, max=1.15, seq_len=4096 -> mu == max_shift == 1.15.
    sigmas = build_sigmas(4, base_shift=0.5, max_shift=1.15, image_seq_len=4096)
    assert sigmas.shape == (5,)
    mu = 1.15
    exp_mu = math.exp(mu)

    def flux(t):
        if t == 0.0:
            return 0.0
        return exp_mu / (exp_mu + (1.0 / t - 1.0))

    expected = [flux(t) for t in (1.0, 0.75, 0.5, 0.25, 0.0)]
    assert expected[0] == pytest.approx(1.0)
    assert expected[1] == pytest.approx(0.9045330, abs=1e-5)
    assert expected[2] == pytest.approx(0.7595109, abs=1e-5)
    assert expected[3] == pytest.approx(0.5128432, abs=1e-5)
    assert torch.allclose(sigmas, torch.tensor(expected, dtype=torch.float32), atol=1e-6)


# --- Krea-2 Turbo fixed-mu (parity fix) ------------------------------
#
# Upstream (krea-ai/krea-2 sampling.py `timesteps()`) documents the resolution-
# dynamic mu interpolation as the BASE/midtrain checkpoint's schedule, but says
# explicitly: "Pass an explicit `mu` to pin a constant shift regardless of
# resolution (used by the distilled checkpoint, which was trained at a fixed
# mu=1.15)." diffusers' Krea2Pipeline (pipeline_krea2.py) confirms this
# structurally: `if self.config.is_distilled: mu = 1.15 else: mu =
# calculate_shift(...)`. Krea-2 Turbo IS the distilled checkpoint, so its
# ModelSpec now carries `fixed_mu: 1.15` (registry.py) instead of the old
# `dynamic_shift` anchors, and `build_sigmas` must produce the IDENTICAL
# schedule at every resolution when `fixed_mu` is set.

KREA2_TURBO_FIXED_MU = 1.15


@pytest.mark.parametrize("image_seq_len", [256, 4096, 16384])  # 256px, 1024px, 2048px worth of tokens
def test_fixed_mu_ignores_image_seq_len(image_seq_len):
    # fixed_mu must produce the SAME schedule regardless of resolution -- the
    # whole point of the fix (the old dynamic_shift path varied with seq_len).
    sigmas = build_sigmas(8, fixed_mu=KREA2_TURBO_FIXED_MU, image_seq_len=image_seq_len)
    reference = build_sigmas(8, fixed_mu=KREA2_TURBO_FIXED_MU)  # image_seq_len omitted entirely
    assert torch.allclose(sigmas, reference)


def test_fixed_mu_exact_values_match_upstream_flux_time_shift():
    # Reproduces krea-ai/krea-2 sampling.py's `timesteps()`:
    #   ts = exp(mu) / (exp(mu) + (1/t - 1) ** 1.0)
    # with mu pinned at 1.15 (the distilled/turbo checkpoint's fixed value),
    # completely independent of image_seq_len (dropped from the call entirely,
    # matching how the turbo generator never supplies a resolution to it).
    steps = 8
    sigmas = build_sigmas(steps, fixed_mu=KREA2_TURBO_FIXED_MU)
    assert sigmas.shape == (steps + 1,)

    exp_mu = math.exp(KREA2_TURBO_FIXED_MU)

    def flux(t):
        if t == 0.0:
            return 0.0
        return exp_mu / (exp_mu + (1.0 / t - 1.0))

    t = torch.linspace(1.0, 0.0, steps + 1)
    expected = torch.tensor([flux(float(tt)) for tt in t], dtype=torch.float32)
    assert torch.allclose(sigmas, expected, atol=1e-6)
    assert sigmas[0].item() == pytest.approx(1.0)
    assert sigmas[-1].item() == 0.0


@pytest.mark.parametrize(
    "resolution_px,expected_seq_len,expected_mu",
    [
        # h_len = (latent_px + 1) // 2 with latent_px = px // 8 (Wan21 VAE downsample)
        # and patch=2 (Krea-2 patchify); seq_len = h_len * w_len for a square image.
        (1024, 4096, 0.90625),   # audit's cited ~0.906
        (2048, 16384, 2.20625),  # audit's cited ~2.206
    ],
)
def test_old_dynamic_shift_would_have_varied_with_resolution(resolution_px, expected_seq_len, expected_mu):
    """Documents the BUG the fix replaces: the retired `dynamic_shift` anchors
    (y1=0.5@x1=256, y2=1.15@x2=6400) produced a resolution-DEPENDENT mu at
    1024px/2048px (the audit's ~0.906/~2.206), instead of official Krea-2
    Turbo's fixed mu=1.15 at every resolution. Guards against silently
    reintroducing `dynamic_shift` on the turbo ModelSpec."""
    latent_px = resolution_px // 8
    h_len = (latent_px + 1) // 2
    seq_len = h_len * h_len
    assert seq_len == expected_seq_len

    old_dynamic_shift = {"y1": 0.5, "y2": 1.15, "x1_px": 256, "x2_px": 1280, "align": 16}
    sigmas = build_sigmas(8, dynamic_shift=old_dynamic_shift, image_seq_len=seq_len)

    exp_mu = math.exp(expected_mu)

    def flux(t):
        if t == 0.0:
            return 0.0
        return exp_mu / (exp_mu + (1.0 / t - 1.0))

    t = torch.linspace(1.0, 0.0, 9)
    expected_sigmas = torch.tensor([flux(float(tt)) for tt in t], dtype=torch.float32)
    assert torch.allclose(sigmas, expected_sigmas, atol=1e-5)

    # And this varying-with-resolution schedule must NOT equal the fixed-mu one.
    fixed = build_sigmas(8, fixed_mu=KREA2_TURBO_FIXED_MU)
    assert not torch.allclose(sigmas, fixed, atol=1e-4)


def test_flux_mu_interpolation_endpoints():
    # At seq_len == 256 mu == base_shift; at 4096 mu == max_shift. Verify via
    # sigma at t=0.5: sigma = e^mu / (e^mu + 1).
    for seq_len, mu in ((256, 0.5), (4096, 1.15)):
        sigmas = build_sigmas(2, base_shift=0.5, max_shift=1.15, image_seq_len=seq_len)
        # steps=2 -> t=[1,0.5,0]; middle entry is t=0.5.
        exp_mu = math.exp(mu)
        assert sigmas[1].item() == pytest.approx(exp_mu / (exp_mu + 1.0), abs=1e-6)


def test_denoise_truncation():
    # denoise=0.5 builds an 8-step identity schedule and keeps the last 5 sigmas.
    sigmas = build_sigmas(4, shift=1.0, denoise=0.5)
    assert sigmas.shape == (5,)
    assert sigmas[0].item() == pytest.approx(0.5, abs=1e-6)
    assert sigmas[-1].item() == 0.0
    # Values match the tail of linspace(1,0,9): [0.5,0.375,0.25,0.125,0].
    expected = torch.tensor([0.5, 0.375, 0.25, 0.125, 0.0])
    assert torch.allclose(sigmas, expected, atol=1e-6)


def test_full_denoise_no_truncation():
    a = build_sigmas(4, shift=2.02, denoise=1.0)
    b = build_sigmas(4, shift=2.02)
    assert torch.allclose(a, b)


def test_invalid_args():
    with pytest.raises(ValueError):
        build_sigmas(0, shift=2.02)
    with pytest.raises(ValueError):
        build_sigmas(4, shift=2.02, denoise=0.0)
    with pytest.raises(ValueError):
        build_sigmas(4, shift=2.02, denoise=1.5)
