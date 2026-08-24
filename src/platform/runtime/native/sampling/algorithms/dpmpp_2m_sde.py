"""DPM-Solver++(2M) SDE for flow-matching (CONST-prediction) models.

Reference: crowsonkb/k-diffusion ``sample_dpmpp_2m_sde`` (MIT) and the
DPM-Solver++ paper (Lu et al., arXiv:2211.01095). Derived from the MIT source and
the paper — NOT from ComfyUI (GPL). Same sigma-space flow adaptation as
:mod:`~.dpmpp_flow` (our :func:`~.dpmpp_flow.sample_dpmpp_2m`): the model returns
a velocity ``v`` and the x0 estimate is ``x - sigma*v``; the SDE update is written
in sigma-space so the terminal ``sigma_next == 0`` transition never touches the
``t = -log(sigma)`` singularity.

Sigma-space form
----------------
With ``ratio = sigma_next / sigma`` (``== exp(-h)`` for ``h = log(sigma/sigma_next)``)
the k-diffusion 2M-SDE step

    x = (sigma_next/sigma) e^{-eta*h} x + (1 - e^{-(1+eta)h}) x0            (drift)
    x += 0.5 (1 - e^{-(1+eta)h}) (1/r) (x0 - x0_prev)          (midpoint 2M term)
    x += noise * sigma_next * sqrt(1 - e^{-2 eta h}) * s_noise            (diffusion)

becomes, since ``e^{-h} = ratio``,

    coeff = 1 - ratio**(1+eta)
    x = ratio**(1+eta) * x + coeff * x0
    x += 0.5 * coeff * (1/r) * (x0 - x0_prev)
    x += noise * sigma_next * sqrt(1 - ratio**(2*eta)) * s_noise

``solver_type`` is fixed to k-diffusion's default **midpoint** (the ``0.5 * ... *
1/r`` correction). ``r = h_last / h`` is the same step-ratio the deterministic 2M
uses.

eta=0 relationship (verified analytically, contra the "not identical" caveat)
-----------------------------------------------------------------------------
At ``eta = 0`` the drift is ``ratio*x + (1-ratio)*x0``, the diffusion coefficient
``sqrt(1 - ratio**0) = 0`` vanishes, and the midpoint term is
``0.5*(1-ratio)*(1/r)*(x0 - x0_prev)``. Plain :func:`~.dpmpp_flow.sample_dpmpp_2m`
computes ``ratio*x + (1-ratio)*[(1+1/(2r))*x0 - (1/(2r))*x0_prev]`` which expands
to EXACTLY the same value. So eta=0 (midpoint) is *algebraically identical* to
``dpmpp_2m`` — they differ only in floating-point rounding because the correction
is added as a separate term here rather than folded into ``denoised_d``. The
tests assert ``allclose`` (float-order) on a varying stub and exact equality on a
constant-velocity model (where the correction is exactly zero).
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


def _fresh_noise(x: Tensor, generator: torch.Generator | None) -> Tensor:
    if generator is None:
        return torch.randn_like(x)
    return torch.randn(x.shape, generator=generator, device=x.device, dtype=x.dtype)


@torch.no_grad()
def sample_dpmpp_2m_sde(
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
    """DPM-Solver++(2M) SDE loop. Same signature/semantics as :func:`sample_euler`.

    ``sampler_options``:

    * ``eta`` (default ``1.0``) — noise level in ``[0, 1]``. ``0.0`` is the
      deterministic 2M solver (see the module docstring); ``1.0`` is the full SDE.
    * ``s_noise`` (default ``1.0``) — extra scale on the injected noise.
    * ``generator`` (default ``None``) — ``torch.Generator`` for reproducible
      fresh noise; omitted, falls back to the global RNG.
    * ``discontinuity_steps`` — an iterable of step indices, set by
      :func:`~..denoise_loop.denoise` from its ``expert_boundary`` param: at
      each listed step, the 2M history (``old_x0``) is cleared before that
      step runs, as if it were a fresh start -- see
      :func:`~.unipc.sample_unipc`'s docstring for why a multi-expert
      ``model_fn`` needs this.
    """
    opts = sampler_options or {}
    eta = float(opts.get("eta", 1.0))
    if not (0.0 <= eta <= 1.0):
        raise ValueError(f"eta must be in [0, 1], got {eta}")
    s_noise = float(opts.get("s_noise", 1.0))
    generator = opts.get("generator")
    discontinuity_steps = frozenset(opts.get("discontinuity_steps") or ())

    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

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
                coeff = 1.0 - ratio ** (1.0 + eta)
                x = ratio ** (1.0 + eta) * x + coeff * x0
                if old_x0 is not None:
                    h = torch.log(sigma / sigma_next)
                    h_last = torch.log(sigmas[i - 1] / sigma)
                    r = h_last / h
                    x = x + 0.5 * coeff * (1.0 / r) * (x0 - old_x0)
                if eta > 0:
                    var = (1.0 - ratio ** (2.0 * eta)).clamp(min=0.0)
                    x = x + _fresh_noise(x, generator) * sigma_next * var.sqrt() * s_noise

            old_x0 = x0
            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
    finally:
        run_hooks(hooks, "on_end")

    return x
