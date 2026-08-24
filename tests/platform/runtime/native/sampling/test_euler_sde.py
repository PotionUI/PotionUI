"""Stochastic (ancestral) Euler sampler tests."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.algorithms.euler_sde import sample_euler_sde
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


def test_eta_zero_matches_plain_euler_exactly():
    torch.manual_seed(0)
    x_init = torch.randn(2, 3, 4)
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])

    out_euler = sample_euler(_const_velocity_model(1.7), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_sde = sample_euler_sde(
        _const_velocity_model(1.7), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 0.0},
    )
    assert torch.equal(out_euler, out_sde)


def test_eta_one_is_ancestral_and_reproducible_with_generator():
    x_init = torch.zeros(1, 4)
    sigmas = torch.tensor([1.0, 0.5, 0.0])

    gen1 = torch.Generator().manual_seed(42)
    out1 = sample_euler_sde(
        _const_velocity_model(0.0), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": gen1},
    )
    gen2 = torch.Generator().manual_seed(42)
    out2 = sample_euler_sde(
        _const_velocity_model(0.0), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": gen2},
    )
    assert torch.equal(out1, out2)

    # Different seed -> different (noise-driven) trajectory.
    gen3 = torch.Generator().manual_seed(43)
    out3 = sample_euler_sde(
        _const_velocity_model(0.0), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": gen3},
    )
    assert not torch.equal(out1, out3)


def test_terminal_step_is_always_deterministic():
    # Even with eta=1.0, the last step (sigma_next==0) must not draw noise:
    # x_final == x0_est of the last step exactly (no injected randomness).
    x_init = torch.tensor([[5.0]])
    sigmas = torch.tensor([1.0, 0.0])
    gen = torch.Generator().manual_seed(7)
    out = sample_euler_sde(
        _const_velocity_model(2.0), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": gen},
    )
    # x0 = x - sigma*v = 5 - 1*2 = 3; deterministic terminal step -> x_final = x0.
    assert torch.allclose(out, torch.tensor([[3.0]]))


def test_intermediate_eta_between_deterministic_and_ancestral():
    # A partial eta must differ from both eta=0 and eta=1 given the same seed,
    # and should not raise / stay finite.
    x_init = torch.randn(1, 8)
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])

    def run(eta):
        gen = torch.Generator().manual_seed(1)
        return sample_euler_sde(
            _const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None,
            sampler_options={"eta": eta, "generator": gen},
        )

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
        sample_euler_sde(
            _const_velocity_model(0.0), x_init, sigmas, NoCFG(), {}, None,
            sampler_options={"eta": 1.5},
        )


def test_hooks_fire_once_per_step():
    class Counter(BaseStepHook):
        def __init__(self):
            self.starts = 0
            self.steps = 0
            self.ends = 0

        def on_start(self, total_steps):
            self.starts += 1
            self.total = total_steps

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            self.steps += 1

        def on_end(self):
            self.ends += 1

    counter = Counter()
    sigmas = torch.tensor([1.0, 0.5, 0.0])  # 2 steps
    sample_euler_sde(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[counter], sampler_options={"eta": 1.0},
    )
    assert counter.starts == 1
    assert counter.steps == 2
    assert counter.total == 2
    assert counter.ends == 1


def test_no_sampler_options_defaults_to_fully_ancestral_and_falls_back_rng():
    # No sampler_options -> eta defaults to 1.0, generator defaults to None
    # (global RNG). Must not raise, and must not equal the deterministic path.
    torch.manual_seed(123)
    x_init = torch.randn(1, 6)
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    out = sample_euler_sde(_const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_det = sample_euler(_const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    assert torch.isfinite(out).all()
    assert not torch.equal(out, out_det)
