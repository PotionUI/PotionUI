"""RF (rectified-flow) Euler-Ancestral step for flow-matching models.

Faithful port of the per-step math in diffusers' ``LTXEulerAncestralRFScheduler
.step`` (``schedulers/scheduling_ltx_euler_ancestral_rf.py``, Apache-2.0) --
the scheduler LTX-2.5's stage-1 (ancestral) sampling pass uses. It is NOT the
same parameterization as this package's own :func:`~.euler_sde.sample_euler_sde`
(``eta``-mixed implied-noise resampling): this sampler works in ``alpha = 1 -
sigma`` space and takes a classic k-diffusion-style ``sigma_down``/renoise
split, re-derived into ``alpha`` terms rather than the plain-sigma
``sigma_up``/``sigma_down`` pair k-diffusion's own ``get_ancestral_step`` uses.
At ``eta == 0`` both reduce to the same deterministic Euler step; away from
``eta == 0`` they are genuinely different trajectories, so this sampler exists
alongside ``euler_sde`` rather than replacing it.

Per step, given the velocity prediction ``v`` and the CONST clean-latent
estimate ``x0_est = x - sigma*v``:

    downstep_ratio = 1 + (sigma_next/sigma - 1) * eta
    sigma_down     = sigma_next * downstep_ratio          # < sigma_next for eta > 0
    alpha_next     = 1 - sigma_next
    alpha_down     = 1 - sigma_down

    ratio = sigma_down / sigma
    x_det = ratio * x + (1 - ratio) * x0_est               # Euler step TO sigma_down

    renoise_var = sigma_next**2 - sigma_down**2 * alpha_next**2 / alpha_down**2
    x = (alpha_next / alpha_down) * x_det + sqrt(max(renoise_var, 0)) * s_noise * noise

``eta == 0`` collapses ``sigma_down == sigma_next`` and ``renoise_var == 0``,
which makes ``x_det`` itself the Euler step to ``sigma_next`` and the whole
renoise term a no-op -- handled below as its own branch (bit-identical to
:func:`~.euler.sample_euler`, not merely allclose) rather than run through the
general formula, both for speed and to avoid a ``0/0``-shaped ``alpha_down``
edge case at ``sigma_next == 1``.

LTX-2.5 facts (native port investigation, not copied from Lightricks' source
-- FACTS ONLY per project policy on the community-licensed LTX-2 repo):
stage-1 sampling on a >=2.5 checkpoint runs this sampler at ``eta=1.0``,
``s_noise=1.0`` (this module's own defaults), with the ancestral noise drawn
from a generator seeded independently of the main per-seed latent RNG stream
(offset by :data:`ANCESTRAL_NOISE_SEED_OFFSET` from the request seed) so the
extra stochastic draws never shift what the deterministic samplers would have
drawn for the same seed. Stage 2 (the short, distilled-schedule refine pass)
stays on a deterministic sampler -- too few steps for ancestral noise to
matter -- so this module only ever runs as stage 1.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor

# Distinct from the request's main seed so the per-step ancestral noise draws
# never overlap the init-noise / FreeInit stream that seed already drives --
# see the module docstring and each LTX generator pipe's own generate_one()
# (the only place this constant is actually consumed: it derives the
# dedicated ``torch.Generator`` handed to this sampler via
# ``sampler_options['generator']``, this sampler itself never seeds anything).
ANCESTRAL_NOISE_SEED_OFFSET = 10000

_EPS = 1e-12


def _fresh_noise(x: Tensor, generator: torch.Generator | None) -> Tensor:
    if generator is None:
        return torch.randn_like(x)
    return torch.randn(x.shape, generator=generator, device=x.device, dtype=x.dtype)


@torch.no_grad()
def sample_euler_ancestral(
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
    """RF Euler-Ancestral loop. Same signature/semantics as :func:`~.euler.sample_euler`.

    ``sampler_options``:

    * ``eta`` (default ``1.0``) -- ancestral strength in ``[0, 1]``; ``0.0``
      reduces exactly to :func:`~.euler.sample_euler`, ``1.0`` matches
      LTX-2.5's stage-1 default.
    * ``s_noise`` (default ``1.0``) -- extra scale on the injected noise.
    * ``generator`` (default ``None``) -- seeded ``torch.Generator`` for
      reproducible fresh-noise draws; omitted, falls back to the global RNG.
    """
    opts = sampler_options or {}
    eta = float(opts.get("eta", 1.0))
    if not (0.0 <= eta <= 1.0):
        raise ValueError(f"eta must be in [0, 1], got {eta}")
    s_noise = float(opts.get("s_noise", 1.0))
    generator = opts.get("generator")

    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

    s_in = x.new_ones((x.shape[0],))
    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0_est = x - sigma * v

            if eta == 0.0 or sigma_next == 0:
                # Deterministic branch: bit-identical to sample_euler (also the
                # correct terminal behaviour -- no noise left to inject at 0).
                x = x + (sigma_next - sigma) * v
            else:
                downstep_ratio = 1.0 + (sigma_next / sigma - 1.0) * eta
                sigma_down = sigma_next * downstep_ratio
                alpha_next = 1.0 - sigma_next
                alpha_down = 1.0 - sigma_down

                ratio = sigma_down / sigma
                x_det = ratio * x + (1.0 - ratio) * x0_est

                if s_noise > 0.0:
                    renoise_var = (
                        sigma_next**2 - sigma_down**2 * alpha_next**2 / (alpha_down**2 + _EPS)
                    ).clamp(min=0.0)
                    noise = _fresh_noise(x, generator)
                    x = (alpha_next / (alpha_down + _EPS)) * x_det + noise * renoise_var.sqrt() * s_noise
                else:
                    x = x_det

            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0_est)
    finally:
        run_hooks(hooks, "on_end")

    return x
