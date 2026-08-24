"""LCM sampler — for distilled / consistency (LCM, TCD) flow checkpoints.

Reference: Latent Consistency Models (Luo et al., arXiv:2310.04378) and
crowsonkb/k-diffusion ``sample_lcm`` (MIT). Consistency models predict the clean
sample directly, so each step is: estimate ``x0 = x - sigma*v``, then re-noise
that clean estimate to the next noise level with FRESH noise —

    x = (1 - sigma_next) * x0 + sigma_next * noise

(the flow interpolation ``x = (1 - sigma)*x0 + sigma*eps`` re-formed at
``sigma_next`` with independent ``eps``). This deliberately re-injects noise every
step; on a normal (non-distilled) model it degrades, which is why it is an
explicit user choice. The final step (``sigma_next == 0``) returns the clean x0
with no re-noising.

Determinism rides on ``sampler_options['generator']`` exactly like the other
stochastic samplers (``euler_sde``): same seeded generator ⇒ reproducible run.
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
def sample_lcm(
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
    """LCM loop. Same signature/semantics as :func:`sample_euler`.

    ``sampler_options``:

    * ``generator`` (default ``None``) — ``torch.Generator`` for reproducible
      per-step re-noising; omitted, falls back to the global RNG.
    """
    opts = sampler_options or {}
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
            x0 = x - sigma * v

            if sigma_next == 0:
                x = x0
            else:
                noise = _fresh_noise(x, generator)
                x = (1.0 - sigma_next) * x0 + sigma_next * noise

            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
    finally:
        run_hooks(hooks, "on_end")

    return x
