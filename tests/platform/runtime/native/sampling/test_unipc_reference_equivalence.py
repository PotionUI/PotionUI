"""UniPC / anchored-mu agreement with their Apache-2.0 reference implementations.

Pins :func:`sample_unipc` to ``diffusers.UniPCMultistepScheduler`` configured for
flow matching (``use_flow_sigmas=True``, ``prediction_type="flow_prediction"``,
``predict_x0=True``, ``solver_type="bh2"``, ``lower_order_final=True``) and
:func:`_anchored_mu` to ``diffusers.pipelines.krea2.pipeline_krea2.calculate_shift``.
Both references are the upstream this code is attributed to, so a drift here is
either a numerics regression or an attribution that stopped being true.

Both loops consume the *same* sigma array (read back off the scheduler after
``set_timesteps``, so its own ``sigmas[0] -= 1e-6`` nudge applies to both) in
float64, isolating the comparison to solver expression rather than schedule
construction or fp32 rounding.

Order 1 agrees bit-exactly. Orders 2/3 agree to a few e-9, all of it from
:func:`_build_R_b` solving the UniPC linear system in float32 on purpose (see its
docstring) while the reference solves it in the sigma dtype: forcing that one
system to float64 drops the residual to ~1e-15. At the production dtype (float32
sigmas and latents) the two agree to 2-5 float32 ULPs, four orders of magnitude
below bfloat16's eps.
"""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms import sample_unipc
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.flow_schedule import _anchored_mu, build_sigmas


def _velocity(x: torch.Tensor, sigma: float) -> torch.Tensor:
    """Deterministic velocity field, nonlinear in both x and sigma.

    Nonlinearity is the point: a constant-velocity field makes every predictor
    difference and the corrector term vanish, which would let an order-1 solver
    pass a test meant to pin order-2/3 behaviour.
    """
    return 0.35 * x + math.sin(3.0 * sigma + 0.5) + 0.2 * torch.cos(x)


def _reference_scheduler(sigmas: torch.Tensor, solver_order: int):
    from diffusers import UniPCMultistepScheduler

    scheduler = UniPCMultistepScheduler(
        solver_order=solver_order,
        prediction_type="flow_prediction",
        use_flow_sigmas=True,
        flow_shift=1.0,
        predict_x0=True,
        solver_type="bh2",
        lower_order_final=True,
        final_sigmas_type="zero",
    )
    # The reference derives its own terminal, so it takes the schedule without it.
    scheduler.set_timesteps(sigmas=sigmas[:-1].to(torch.float64).numpy())
    scheduler.sigmas = scheduler.sigmas.to(torch.float64)
    return scheduler


def _run_reference(scheduler, x_init: torch.Tensor) -> torch.Tensor:
    x = x_init.clone()
    for i, timestep in enumerate(scheduler.timesteps):
        v = _velocity(x, float(scheduler.sigmas[i]))
        x = scheduler.step(v, timestep, x).prev_sample
    return x


def _run_ours(sigmas: torch.Tensor, x_init: torch.Tensor, solver_order: int) -> torch.Tensor:
    def model_fn(x, sigma, cond):
        return _velocity(x, float(sigma[0]))

    return sample_unipc(
        model_fn, x_init.clone(), sigmas, NoCFG(), {}, None, solver_order=solver_order
    )


@pytest.mark.parametrize("solver_order", [1, 2, 3])
@pytest.mark.parametrize("shift", [1.0, 2.02, 5.0])
def test_matches_diffusers_flow_unipc(solver_order, shift):
    pytest.importorskip("diffusers", reason="reference implementation not installed")

    sigmas = build_sigmas(8, shift=shift).to(torch.float64)
    scheduler = _reference_scheduler(sigmas, solver_order)
    shared_sigmas = scheduler.sigmas.clone()

    torch.manual_seed(0)
    x_init = torch.randn(2, 4, 3, 3, dtype=torch.float64)

    expected = _run_reference(scheduler, x_init)
    actual = _run_ours(shared_sigmas, x_init, solver_order)

    assert torch.allclose(actual, expected, rtol=0, atol=1e-8), (
        f"max|delta|={(actual - expected).abs().max().item():.3e}"
    )


def test_matches_diffusers_flow_unipc_truncated_schedule():
    # denoise < 1 starts below sigma 1.0, so neither side takes the reference's
    # sigmas[0] nudge branch -- the img2img/refine regime.
    pytest.importorskip("diffusers", reason="reference implementation not installed")

    sigmas = build_sigmas(6, shift=3.0, denoise=0.6).to(torch.float64)
    assert float(sigmas[0]) < 1.0
    scheduler = _reference_scheduler(sigmas, solver_order=2)
    shared_sigmas = scheduler.sigmas.clone()

    torch.manual_seed(1)
    x_init = torch.randn(1, 4, 4, 4, dtype=torch.float64)

    expected = _run_reference(scheduler, x_init)
    actual = _run_ours(shared_sigmas, x_init, solver_order=2)

    assert torch.allclose(actual, expected, rtol=0, atol=1e-8), (
        f"max|delta|={(actual - expected).abs().max().item():.3e}"
    )


@pytest.mark.parametrize("px", [512, 768, 1024, 1280, 1536])
def test_anchored_mu_matches_krea2_calculate_shift(px):
    pytest.importorskip("diffusers", reason="reference implementation not installed")
    from diffusers.pipelines.krea2.pipeline_krea2 import calculate_shift

    # Krea-2 base/midtrain anchors, as documented on diffusers' Krea2Pipeline:
    # base_image_seq_len=256 == (256/16)^2, max_image_seq_len=6400 == (1280/16)^2.
    dynamic_shift = {"align": 16, "x1_px": 256, "x2_px": 1280, "y1": 0.5, "y2": 1.15}
    image_seq_len = (px // 16) ** 2

    expected = calculate_shift(image_seq_len, 256, 6400, 0.5, 1.15)
    assert _anchored_mu(dynamic_shift, image_seq_len) == pytest.approx(expected, abs=1e-12)
