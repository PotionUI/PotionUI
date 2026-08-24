"""RES second-order exponential multistep solver for flow-matching models.

Reference: "Improved Order Analysis and Design of Exponential Integrator for
Diffusion Models Sampling" (RES, arXiv:2308.02157). The paper's thesis is that
popular high-order exponential integrators use *degenerate* coefficients that
miss the exact order conditions; RES uses the coefficients that satisfy them.
This is a from-first-principles flow adaptation of the second-order refined
solver — derived from the exponential-integrator integrals below, NOT ported from
any implementation (ComfyUI's ``res_multistep`` is GPL and was not consulted).

Derivation (sigma → lambda mapping)
-----------------------------------
Work in ``lambda = -log(sigma)`` where the data-prediction ODE is semilinear;
over one step ``h = lambda_{n+1} - lambda_n = log(sigma_n/sigma_{n+1})`` the exact
solution with ``ratio = sigma_{n+1}/sigma_n = e^{-h}`` and the model's x0 estimate
``x0 = x - sigma*v`` is

    x_{n+1} = e^{-h} x_n + integral_0^h e^{-(h-tau)} x0(lambda_n + tau) d tau .

Extrapolate ``x0`` linearly through the last two estimates (slope
``(x0_n - x0_{n-1})/h_prev``, ``h_prev = log(sigma_{n-1}/sigma_n)``) and integrate
EXACTLY (this is the refinement — no truncation):

    integral_0^h e^{-(h-tau)} d tau       = 1 - e^{-h} = 1 - ratio
    integral_0^h e^{-(h-tau)} tau d tau   = h - (1 - e^{-h}) = h - (1 - ratio)

giving

    x_{n+1} = ratio * x_n + (1 - ratio) * x0_n
              + ((h - (1 - ratio)) / h_prev) * (x0_n - x0_{n-1}) .

The correction weight ``b = h - (1 - ratio)`` is the exact integral; DPM-Solver++
(2M) instead uses ``(1 - ratio) * h / (2 h_prev)`` — the two agree to O(h^2) (both
~ h^2/2 for small h) but differ at higher order, which is precisely the RES
refinement. A constant-velocity model has ``x0_n == x0_{n-1}`` so the correction
vanishes and every step collapses to the first-order exponential (Euler) update.

First step is first-order (no history); a terminal ``sigma_next == 0`` collapses to
the x0 estimate. Deterministic: no noise, no options.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


@torch.no_grad()
def sample_res_multistep(
    model_fn,
    x: Tensor,
    sigmas: Tensor,
    guidance: GuidanceStrategy,
    cond: dict,
    uncond: dict | None = None,
    hooks=(),
    is_cancelled=None,
    sampler_options: dict | None = None,
) -> Tensor:
    """RES(2M) loop. Same signature/semantics as :func:`sample_euler`.

    Deterministic second-order multistep solver whose only option is
    ``sampler_options['discontinuity_steps']``: an iterable of step indices,
    set by :func:`~..denoise_loop.denoise` from its ``expert_boundary`` param.
    At each listed step, the extrapolation history (``old_x0``) is cleared
    before that step runs, as if it were a fresh start -- see
    :func:`~.unipc.sample_unipc`'s docstring for why a multi-expert
    ``model_fn`` needs this.
    """
    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)
    discontinuity_steps = frozenset((sampler_options or {}).get("discontinuity_steps") or ())

    s_in = x.new_ones((x.shape[0],))
    old_x0 = None
    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            if i in discontinuity_steps:
                old_x0 = None

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0 = x - sigma * v

            if sigma_next == 0:
                x = x0
            else:
                ratio = sigma_next / sigma
                x = ratio * x + (1.0 - ratio) * x0
                if old_x0 is not None:
                    h = torch.log(sigma / sigma_next)
                    h_prev = torch.log(sigmas[i - 1] / sigma)
                    b = h - (1.0 - ratio)          # exact 2nd-order integral weight
                    x = x + (b / h_prev) * (x0 - old_x0)

            old_x0 = x0
            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
    finally:
        run_hooks(hooks, "on_end")

    return x
