"""Tests for the RF Euler-Ancestral sampler (LTX-2.5 stage-1)."""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.algorithms.euler_ancestral import (
    ANCESTRAL_NOISE_SEED_OFFSET,
    sample_euler_ancestral,
)
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.denoise_loop import SAMPLERS, STOCHASTIC_SAMPLERS
from src.platform.runtime.native.sampling.hooks import BaseStepHook
from src.platform.runtime.native.errors import SamplingCancelled


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


def _run(x, sigmas, v0=1.0, **opts):
    return sample_euler_ancestral(
        _const_velocity_model(v0), x.clone(), sigmas, NoCFG(), {}, None,
        sampler_options=opts or None,
    )


def _reference_step(x, v, sigma, sigma_next, eta, s_noise, noise):
    """Hand-computed reference matching diffusers'
    LTXEulerAncestralRFScheduler.step exactly (independent re-derivation of
    the formula the module implements, used to catch transcription bugs)."""
    x0 = x - sigma * v
    if sigma_next == 0:
        return x0
    downstep_ratio = 1.0 + (sigma_next / sigma - 1.0) * eta
    sigma_down = sigma_next * downstep_ratio
    alpha_next = 1.0 - sigma_next
    alpha_down = 1.0 - sigma_down
    ratio = sigma_down / sigma
    x_det = ratio * x + (1.0 - ratio) * x0
    if eta == 0.0 or s_noise == 0.0:
        return x_det
    renoise_var = max(sigma_next**2 - sigma_down**2 * alpha_next**2 / (alpha_down**2 + 1e-12), 0.0)
    return (alpha_next / (alpha_down + 1e-12)) * x_det + noise * math.sqrt(renoise_var) * s_noise


def test_matches_hand_computed_reference_multi_step():
    torch.manual_seed(0)
    sigmas = torch.tensor([1.0, 0.7, 0.4, 0.15, 0.0])
    eta, s_noise, v0 = 1.0, 1.0, 0.6
    x = torch.tensor([[2.0, -1.0, 0.5]])

    # Draw the exact same noise stream sample_euler_ancestral will draw (one
    # torch.randn call per non-terminal, non-deterministic step) to build the
    # independent reference trajectory step by step.
    gen_ref = torch.Generator().manual_seed(99)
    gen_actual = torch.Generator().manual_seed(99)

    x_ref = x.clone()
    for i in range(len(sigmas) - 1):
        sigma, sigma_next = float(sigmas[i]), float(sigmas[i + 1])
        noise = torch.randn(x_ref.shape, generator=gen_ref) if sigma_next != 0 else torch.zeros_like(x_ref)
        x_ref = _reference_step(x_ref, torch.full_like(x_ref, v0), sigma, sigma_next, eta, s_noise, noise)

    out = sample_euler_ancestral(
        _const_velocity_model(v0), x.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": eta, "s_noise": s_noise, "generator": gen_actual},
    )
    assert torch.allclose(out, x_ref, atol=1e-6)


def test_eta_zero_matches_plain_euler_bit_identical():
    torch.manual_seed(0)
    x_init = torch.randn(2, 3, 4)
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])

    out_euler = sample_euler(_const_velocity_model(1.7), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_anc = _run(x_init, sigmas, v0=1.7, eta=0.0)
    assert torch.equal(out_euler, out_anc)


def test_terminal_step_is_always_deterministic():
    x_init = torch.tensor([[5.0]])
    sigmas = torch.tensor([1.0, 0.0])
    gen = torch.Generator().manual_seed(7)
    out = _run(x_init, sigmas, v0=2.0, eta=1.0, generator=gen)
    # x0 = x - sigma*v = 5 - 1*2 = 3; terminal step is always deterministic.
    assert torch.allclose(out, torch.tensor([[3.0]]))


def test_reproducible_with_same_generator_seed_differs_with_another():
    x_init = torch.zeros(1, 4)
    sigmas = torch.tensor([1.0, 0.5, 0.0])

    gen1 = torch.Generator().manual_seed(42)
    out1 = _run(x_init, sigmas, v0=0.0, eta=1.0, generator=gen1)
    gen2 = torch.Generator().manual_seed(42)
    out2 = _run(x_init, sigmas, v0=0.0, eta=1.0, generator=gen2)
    assert torch.equal(out1, out2)

    gen3 = torch.Generator().manual_seed(43)
    out3 = _run(x_init, sigmas, v0=0.0, eta=1.0, generator=gen3)
    assert not torch.equal(out1, out3)


def test_s_noise_zero_skips_renoise_but_stays_at_downstep():
    # s_noise=0 with eta>0: per the reference, no noise is injected and the
    # result is the (partial) downstep target -- NOT the same as eta=0 (which
    # steps all the way to sigma_next deterministically).
    x_init = torch.tensor([[4.0]])
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    out_s0 = _run(x_init, sigmas, v0=1.0, eta=1.0, s_noise=0.0)
    out_eta0 = _run(x_init, sigmas, v0=1.0, eta=0.0)
    assert torch.isfinite(out_s0).all()
    assert not torch.equal(out_s0, out_eta0)


def test_larger_s_noise_increases_spread_across_seeds():
    x_init = torch.zeros(1, 4)
    sigmas = torch.tensor([1.0, 0.5, 0.0])

    def spread(s_noise):
        outs = [
            _run(x_init, sigmas, v0=0.0, eta=1.0, s_noise=s_noise, generator=torch.Generator().manual_seed(s))
            for s in range(20)
        ]
        return torch.stack(outs).std().item()

    assert spread(2.0) > spread(0.5)


def test_intermediate_eta_between_deterministic_and_full():
    x_init = torch.randn(1, 8)
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])

    def run(eta):
        gen = torch.Generator().manual_seed(1)
        return _run(x_init, sigmas, v0=1.0, eta=eta, generator=gen)

    out_det = run(0.0)
    out_half = run(0.5)
    out_full = run(1.0)
    assert torch.isfinite(out_half).all()
    assert not torch.equal(out_det, out_half)
    assert not torch.equal(out_full, out_half)


def test_invalid_eta_raises():
    x_init = torch.zeros(1, 2)
    sigmas = torch.tensor([1.0, 0.0])
    with pytest.raises(ValueError):
        _run(x_init, sigmas, v0=0.0, eta=1.5)


def test_no_sampler_options_defaults_to_eta_one_s_noise_one():
    # LTX-2.5 facts: defaults must be eta=1.0, s_noise=1.0 with no explicit
    # sampler_options -- confirmed by non-equality with the eta=0 (plain
    # Euler) path using the same seed/model.
    torch.manual_seed(123)
    x_init = torch.randn(1, 6)
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    out_default = sample_euler_ancestral(_const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_det = sample_euler(_const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    assert torch.isfinite(out_default).all()
    assert not torch.equal(out_default, out_det)


def test_hooks_fire_once_per_step():
    class Counter(BaseStepHook):
        def __init__(self):
            self.starts = self.steps = self.ends = 0

        def on_start(self, total_steps):
            self.starts += 1
            self.total = total_steps

        def on_step(self, *_):
            self.steps += 1

        def on_end(self):
            self.ends += 1

    counter = Counter()
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    sample_euler_ancestral(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[counter], sampler_options={"eta": 1.0},
    )
    assert (counter.starts, counter.steps, counter.total, counter.ends) == (1, 2, 2, 1)


def test_cancellation_raises():
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 2

    sigmas = torch.tensor([1.0, 0.7, 0.4, 0.0])
    with pytest.raises(SamplingCancelled):
        sample_euler_ancestral(
            _const_velocity_model(1.0), torch.zeros(1, 3), sigmas, NoCFG(), {}, None, is_cancelled=cancel,
        )


# --- registry wiring --------------------------------------------------------

def test_registered_in_samplers_and_stochastic_samplers():
    assert "euler_ancestral" in SAMPLERS and callable(SAMPLERS["euler_ancestral"])
    assert "euler_ancestral" in STOCHASTIC_SAMPLERS


def test_ancestral_noise_seed_offset_is_a_large_positive_int():
    # Just a sanity pin on the contract other agents/pipes rely on: a fixed
    # int offset, large enough to never collide with a small seed's own
    # neighbourhood in practice.
    assert isinstance(ANCESTRAL_NOISE_SEED_OFFSET, int)
    assert ANCESTRAL_NOISE_SEED_OFFSET == 10000
