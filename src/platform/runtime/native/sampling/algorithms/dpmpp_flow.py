"""DPM-Solver++(2M) for flow-matching (CONST-prediction) models.

Ported from ComfyUI ``comfy/k_diffusion/sampling.py::sample_dpmpp_2m`` and
specialised to the CONST/flow contract used across the native engine. ComfyUI's
sampler operates on the model's *denoised* (x0) prediction; for a flow model the
x0 estimate is ``x - sigma*v`` where ``v`` is the predicted velocity, so this
port keeps the exact ``model_fn(x, sigma, cond) -> velocity`` +
``GuidanceStrategy`` contract and derives the x0 prediction internally.

Written in sigma-space (rather than ComfyUI's ``t = -log(sigma)`` space) so the
terminal ``sigma_next == 0`` transition is a plain ``ratio == 0`` case instead
of an ``inf`` in the log parametrisation — numerically identical, but no ``inf``
ever materialises. The two are equivalent because
``sigma_fn(t_next)/sigma_fn(t) == sigma_next/sigma`` and
``(-h).expm1() == sigma_next/sigma - 1``.

For a constant-velocity model the x0 estimate is exact and constant, so the
multistep correction term vanishes and every step collapses to the Euler update
``x += (sigma_next - sigma)*v`` — i.e. this integrates a linear flow ODE
identically to :func:`sample_euler`.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


@torch.no_grad()
def sample_dpmpp_2m(
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
    """DPM-Solver++(2M) loop. Same signature/semantics as :func:`sample_euler`.

    Hooks fire once per step (after the step, with the x0 estimate);
    ``is_cancelled()`` is polled each step and raises :class:`SamplingCancelled`.

    ``sampler_options['discontinuity_steps']`` (an iterable of step indices,
    set by :func:`~..denoise_loop.denoise` from its ``expert_boundary`` param):
    at each listed step, the 2M history (``old_x0``) is cleared before that
    step runs, as if it were a fresh start -- see
    :func:`~.unipc.sample_unipc`'s docstring for why a multi-expert
    ``model_fn`` needs this. Otherwise this solver has no options of its own.
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
            x0 = x - sigma * v  # CONST denoised / x0 estimate

            if sigma_next == 0:
                # Terminal step: DPM++(2M) and Euler both collapse to the x0
                # estimate (ComfyUI's sigmas[i+1]==0 first-order branch).
                x = x0
            else:
                ratio = sigma_next / sigma
                if old_x0 is None:
                    x0_d = x0
                else:
                    # r = h_last / h with h = log(sigma/sigma_next) (== t_next - t
                    # in ComfyUI's -log(sigma) space); the 2M midpoint blend.
                    h = torch.log(sigma / sigma_next)
                    h_last = torch.log(sigmas[i - 1] / sigma)
                    r = h_last / h
                    x0_d = (1 + 1 / (2 * r)) * x0 - (1 / (2 * r)) * old_x0
                x = ratio * x - (ratio - 1) * x0_d

            old_x0 = x0
            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
    finally:
        run_hooks(hooks, "on_end")

    return x
