"""DPM-Solver++(3M) — deterministic third-order multistep — for flow models.

Reference: crowsonkb/k-diffusion ``sample_dpmpp_3m_sde`` (MIT) specialised to the
deterministic ``eta = 0`` case, plus the DPM-Solver++ paper (Lu et al.,
arXiv:2211.01095). Derived from the MIT source + paper, NOT from ComfyUI (GPL).
Same sigma-space flow adaptation as :mod:`~.dpmpp_flow`: the model returns a
velocity ``v`` and the x0 estimate is ``x - sigma*v``.

Third-order multistep: each step fits a quadratic through the last three x0
estimates and applies the exponential-integrator ``phi`` corrections. With
``ratio = sigma_next/sigma`` and ``h = log(sigma/sigma_next)`` (so
``exp(-h) == ratio``) the k-diffusion 3M update at ``eta = 0`` is

    x   = ratio * x + (1 - ratio) * x0                         (first-order drift)
    phi2 = (ratio - 1)/h + 1                                   (== (-h).expm1()/h + 1)
    phi3 = phi2/h - 0.5
    # r0 = h_1/h, r1 = h_2/h from the two previous step sizes
    d1_0 = (x0 - x0_1)/r0 ;  d1_1 = (x0_1 - x0_2)/r1
    d1   = d1_0 + (d1_0 - d1_1) * r0/(r0 + r1)
    d2   = (d1_0 - d1_1) / (r0 + r1)
    x    += phi2 * d1 - phi3 * d2

The first two steps degenerate to lower order (standard multistep warm-up): step 0
is first-order (no history), step 1 is second-order (``phi2*d1_0`` only), step 2+
is full third-order. A terminal ``sigma_next == 0`` collapses to the x0 estimate.
Deterministic: no noise, no ``eta``, no ``sampler_options``.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


@torch.no_grad()
def sample_dpmpp_3m(
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
    """DPM-Solver++(3M) loop. Same signature/semantics as :func:`sample_euler`.

    Deterministic multistep solver whose only option is
    ``sampler_options['discontinuity_steps']``: an iterable of step indices,
    set by :func:`~..denoise_loop.denoise` from its ``expert_boundary`` param.
    At each listed step, the 3M history (``x0_1``/``x0_2``/``h_1``/``h_2``) is
    cleared before that step runs, dropping this order-3 solver back to its
    order-1 warmup as if it were a fresh start -- see
    :func:`~.unipc.sample_unipc`'s docstring for why a multi-expert
    ``model_fn`` needs this.
    """
    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)
    discontinuity_steps = frozenset((sampler_options or {}).get("discontinuity_steps") or ())

    s_in = x.new_ones((x.shape[0],))
    x0_1 = x0_2 = None       # previous / second-previous x0 estimates
    h_1 = h_2 = None         # previous / second-previous step sizes (in log-sigma)
    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            if i in discontinuity_steps:
                x0_1 = x0_2 = None
                h_1 = h_2 = None

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0 = x - sigma * v

            if sigma_next == 0:
                x = x0
                h = None
            else:
                ratio = sigma_next / sigma
                h = torch.log(sigma / sigma_next)
                x = ratio * x + (1.0 - ratio) * x0
                phi_2 = (ratio - 1.0) / h + 1.0
                if h_2 is not None:
                    r0 = h_1 / h
                    r1 = h_2 / h
                    d1_0 = (x0 - x0_1) / r0
                    d1_1 = (x0_1 - x0_2) / r1
                    d1 = d1_0 + (d1_0 - d1_1) * r0 / (r0 + r1)
                    d2 = (d1_0 - d1_1) / (r0 + r1)
                    phi_3 = phi_2 / h - 0.5
                    x = x + phi_2 * d1 - phi_3 * d2
                elif h_1 is not None:
                    d1_0 = (x0 - x0_1) / (h_1 / h)
                    x = x + phi_2 * d1_0

            x0_2, x0_1 = x0_1, x0
            h_2, h_1 = h_1, h
            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
    finally:
        run_hooks(hooks, "on_end")

    return x
