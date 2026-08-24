"""Euler-loop tests: exact constant-velocity convergence, hooks, cancellation."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sampling.algorithms import sample_euler
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook


def _const_velocity_model(v0):
    """model_fn returning a fixed velocity ``v0`` regardless of x/sigma."""

    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


def test_constant_velocity_euler_is_exact():
    # With constant velocity v0 the euler steps telescope:
    #   x_final = x_init + (sigmas[-1] - sigmas[0]) * v0 = x_init - v0  (sigma 1->0)
    x_init = torch.tensor([[10.0, 20.0]])
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    out = sample_euler(
        _const_velocity_model(2.0), x_init.clone(), sigmas, NoCFG(), {}, None
    )
    assert torch.allclose(out, x_init - 2.0)


def test_euler_multi_step_schedule_exact():
    x_init = torch.zeros(1, 3)
    sigmas = torch.tensor([1.0, 0.7, 0.3, 0.0])
    out = sample_euler(_const_velocity_model(5.0), x_init, sigmas, NoCFG(), {}, None)
    assert torch.allclose(out, torch.full_like(x_init, -5.0))


def test_hooks_fire_once_per_step():
    class Counter(BaseStepHook):
        def __init__(self):
            self.starts = 0
            self.steps = 0
            self.ends = 0
            self.x0s = []

        def on_start(self, total_steps):
            self.starts += 1
            self.total = total_steps

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            self.steps += 1
            self.x0s.append(denoised_x0)

        def on_end(self):
            self.ends += 1

    counter = Counter()
    sigmas = torch.tensor([1.0, 0.5, 0.0])  # 2 steps
    sample_euler(
        _const_velocity_model(1.0),
        torch.zeros(1, 2),
        sigmas,
        NoCFG(),
        {},
        None,
        hooks=[counter],
    )
    assert counter.starts == 1
    assert counter.steps == 2
    assert counter.total == 2
    assert counter.ends == 1
    assert all(x0 is not None for x0 in counter.x0s)


def test_x0_estimate_passed_to_hooks():
    # x0_est = x - sigma*v. At step 0: x=x_init, sigma=1, v=3 -> x0 = x_init - 3.
    seen = []

    class Grab(BaseStepHook):
        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            seen.append((step_index, denoised_x0.clone()))

    x_init = torch.tensor([[4.0]])
    sigmas = torch.tensor([1.0, 0.0])
    sample_euler(
        _const_velocity_model(3.0), x_init.clone(), sigmas, NoCFG(), {}, None,
        hooks=[Grab()],
    )
    step0_x0 = seen[0][1]
    assert torch.allclose(step0_x0, x_init - 3.0)


def test_cancellation_raises_mid_loop():
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] >= 3  # cancel at the 3rd poll (step index 2)

    sigmas = torch.linspace(1.0, 0.0, 11)  # 10 steps
    with pytest.raises(SamplingCancelled) as exc:
        sample_euler(
            _const_velocity_model(1.0),
            torch.zeros(1, 2),
            sigmas,
            NoCFG(),
            {},
            None,
            is_cancelled=is_cancelled,
        )
    assert exc.value.step_index == 2


def test_on_end_runs_even_on_cancel():
    ended = {"v": False}

    class EndHook(BaseStepHook):
        def on_end(self):
            ended["v"] = True

    def is_cancelled():
        return True  # cancel immediately

    with pytest.raises(SamplingCancelled):
        sample_euler(
            _const_velocity_model(1.0),
            torch.zeros(1, 2),
            torch.tensor([1.0, 0.5, 0.0]),
            NoCFG(),
            {},
            None,
            hooks=[EndHook()],
            is_cancelled=is_cancelled,
        )
    assert ended["v"] is True
