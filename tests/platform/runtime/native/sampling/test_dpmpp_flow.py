"""DPM-Solver++(2M) flow sampler: exactness, hooks, cancellation."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sampling.algorithms import sample_dpmpp_2m
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


def test_constant_velocity_matches_euler_exactly():
    # Constant velocity -> x0 estimate is constant -> multistep term vanishes ->
    # every step is the exact Euler update. Final x = x_init - v0 (sigma 1->0).
    x_init = torch.tensor([[10.0, 20.0, -3.0]])
    sigmas = torch.tensor([1.0, 0.7, 0.4, 0.15, 0.0])
    out = sample_dpmpp_2m(
        _const_velocity_model(2.0), x_init.clone(), sigmas, NoCFG(), {}, None
    )
    assert torch.allclose(out, x_init - 2.0, atol=1e-5)


def test_hooks_fire_once_per_step():
    class Counter(BaseStepHook):
        def __init__(self):
            self.steps = 0
            self.starts = 0
            self.ends = 0

        def on_start(self, total_steps):
            self.starts += 1

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            self.steps += 1
            assert denoised_x0 is not None

        def on_end(self):
            self.ends += 1

    counter = Counter()
    sigmas = torch.linspace(1.0, 0.0, 6)  # 5 steps
    sample_dpmpp_2m(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[counter],
    )
    assert (counter.starts, counter.steps, counter.ends) == (1, 5, 1)


def test_cancellation_mid_loop():
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] >= 4  # cancel at step index 3

    with pytest.raises(SamplingCancelled) as exc:
        sample_dpmpp_2m(
            _const_velocity_model(1.0),
            torch.zeros(1, 2),
            torch.linspace(1.0, 0.0, 11),
            NoCFG(),
            {},
            None,
            is_cancelled=is_cancelled,
        )
    assert exc.value.step_index == 3


# -- discontinuity_steps: 2M history reset at an expert switch --------------
# (Same mechanism as unipc's, for dpmpp_2m's own multistep history.)

def _two_phase_model(v_before, v_after, switch_sigma):
    def model_fn(x, sigma, cond):
        sigma_val = float(sigma.reshape(-1)[0])
        v = v_before if sigma_val > switch_sigma else v_after
        return torch.full_like(x, v)

    return model_fn


def _split_pass_reference(model_fn, x_init, sigmas, switch):
    first = sample_dpmpp_2m(model_fn, x_init.clone(), sigmas[:switch + 1], NoCFG(), {}, None)
    return sample_dpmpp_2m(model_fn, first, sigmas[switch:], NoCFG(), {}, None)


def test_dpmpp_2m_without_reset_diverges_from_split_pass_reference():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sample_dpmpp_2m(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None)
    out_split = _split_pass_reference(model_fn, x_init, sigmas, switch)
    assert not torch.allclose(out_continuous, out_split)


def test_dpmpp_2m_with_reset_matches_split_pass_reference_exactly():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sample_dpmpp_2m(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                                      sampler_options={"discontinuity_steps": {switch}})
    out_split = _split_pass_reference(model_fn, x_init, sigmas, switch)
    assert torch.allclose(out_continuous, out_split, atol=1e-6)


def test_dpmpp_2m_discontinuity_reset_is_a_noop_before_the_switch_step():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    captured = {"reset": [], "no_reset": []}

    class _Recorder(BaseStepHook):
        def __init__(self, key):
            self.key = key

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            captured[self.key].append(x.clone())

    sample_dpmpp_2m(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                    hooks=[_Recorder("no_reset")])
    sample_dpmpp_2m(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                    hooks=[_Recorder("reset")],
                    sampler_options={"discontinuity_steps": {switch}})

    for i in range(switch):
        assert torch.allclose(captured["no_reset"][i], captured["reset"][i]), i
