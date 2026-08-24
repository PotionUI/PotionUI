"""Order-of-accuracy sanity: on a nontrivial smooth flow ODE, the 2nd-order
multistep samplers (dpmpp_2m, unipc) at 10 steps must be closer to a 200-step
Euler reference than 10-step Euler is.

The velocity field ``v(x, sigma) = a*x + b*sigma`` gives a genuinely nonlinear
trajectory (x0 = x - sigma*v varies along the path), so a 1st-order method has
O(h) error while the 2nd-order methods have O(h^2)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.algorithms import (
    sample_dpmpp_2m,
    sample_euler,
    sample_unipc,
)
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


def _linear_field(a: float, b: float):
    def model_fn(x, sigma, cond):
        s = sigma.view(-1, *([1] * (x.ndim - 1)))
        return a * x + b * s

    return model_fn


def _run(sampler, steps):
    field = _linear_field(-0.5, 0.3)
    x_init = torch.tensor([[1.0]])
    sigmas = build_sigmas(steps, shift=2.02)
    return sampler(field, x_init.clone(), sigmas, NoCFG(), {}, None)


def test_multistep_beats_euler_order_of_accuracy():
    ref = _run(sample_euler, 200)

    err_euler = (_run(sample_euler, 10) - ref).abs().max().item()
    err_dpmpp = (_run(sample_dpmpp_2m, 10) - ref).abs().max().item()
    err_unipc = (_run(sample_unipc, 10) - ref).abs().max().item()

    assert err_euler > 0  # euler at 10 steps has real error to beat
    assert err_dpmpp < err_euler, (err_dpmpp, err_euler)
    assert err_unipc < err_euler, (err_unipc, err_euler)
