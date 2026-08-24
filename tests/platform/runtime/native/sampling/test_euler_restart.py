"""Restart-sampling wrapper tests."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.algorithms.euler_restart import sample_euler_restart
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


def test_zero_restarts_matches_plain_euler_exactly():
    torch.manual_seed(0)
    x_init = torch.randn(2, 3)
    sigmas = torch.tensor([1.0, 0.7, 0.3, 0.0])

    out_euler = sample_euler(_const_velocity_model(1.3), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_restart_default = sample_euler_restart(
        _const_velocity_model(1.3), x_init.clone(), sigmas, NoCFG(), {}, None,
    )  # sampler_options=None -> no restarts
    out_restart_explicit = sample_euler_restart(
        _const_velocity_model(1.3), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"restart_count": 0},
    )
    assert torch.equal(out_euler, out_restart_default)
    assert torch.equal(out_euler, out_restart_explicit)


def test_empty_restarts_list_matches_plain_euler_exactly():
    x_init = torch.randn(1, 4)
    sigmas = torch.tensor([1.0, 0.6, 0.2, 0.0])
    out_euler = sample_euler(_const_velocity_model(0.5), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_restart = sample_euler_restart(
        _const_velocity_model(0.5), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"restarts": []},
    )
    assert torch.equal(out_euler, out_restart)


def test_explicit_restarts_shape_and_step_count():
    class Counter(BaseStepHook):
        def __init__(self):
            self.steps = 0
            self.total = None

        def on_start(self, total_steps):
            self.total = total_steps

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            self.steps += 1

    x_init = torch.randn(1, 4, 4)
    sigmas = torch.tensor([1.0, 0.6, 0.2, 0.0])  # 3 main steps
    counter = Counter()
    gen = torch.Generator().manual_seed(5)
    out = sample_euler_restart(
        _const_velocity_model(0.3), x_init.clone(), sigmas, NoCFG(), {}, None,
        hooks=[counter],
        sampler_options={"restarts": [(0.4, 0.0, 2), (0.6, 0.0, 3)], "generator": gen},
    )
    assert out.shape == x_init.shape
    assert torch.isfinite(out).all()
    # 3 main steps + 2 + 3 restart steps = 8 (the extra re-noise model eval per
    # restart is not counted, only real x-advancing steps).
    assert counter.total == 8
    assert counter.steps == 8


def test_determinism_with_fixed_generator():
    x_init = torch.randn(1, 6)
    sigmas = torch.tensor([1.0, 0.5, 0.0])

    def run(seed):
        gen = torch.Generator().manual_seed(seed)
        return sample_euler_restart(
            _const_velocity_model(0.7), x_init.clone(), sigmas, NoCFG(), {}, None,
            sampler_options={"restart_count": 2, "restart_strength": 0.4, "generator": gen},
        )

    out1 = run(99)
    out2 = run(99)
    out3 = run(100)
    assert torch.equal(out1, out2)
    assert not torch.equal(out1, out3)


def test_restart_count_convenience_builds_expected_segment_count():
    class Counter(BaseStepHook):
        def __init__(self):
            self.total = None

        def on_start(self, total_steps):
            self.total = total_steps

    sigmas = torch.tensor([1.0, 0.5, 0.0])  # 2 main steps
    counter = Counter()
    gen = torch.Generator().manual_seed(1)
    sample_euler_restart(
        _const_velocity_model(0.1), torch.zeros(1, 3), sigmas, NoCFG(), {}, None,
        hooks=[counter],
        sampler_options={"restart_count": 3, "restart_strength": 0.5, "generator": gen},
    )
    # n_steps per segment = max(1, total_main_steps // restart_count) = max(1, 2//3) = 1.
    # total = 2 main + 3 segments * 1 step = 5.
    assert counter.total == 5


def test_invalid_restart_strength_raises():
    sigmas = torch.tensor([1.0, 0.0])
    with pytest.raises(ValueError):
        sample_euler_restart(
            _const_velocity_model(0.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
            sampler_options={"restart_count": 1, "restart_strength": 1.5},
        )


def test_on_end_runs_and_hooks_isolated_like_euler():
    ended = {"v": False}

    class EndHook(BaseStepHook):
        def on_end(self):
            ended["v"] = True

    sigmas = torch.tensor([1.0, 0.5, 0.0])
    sample_euler_restart(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[EndHook()],
    )
    assert ended["v"] is True
