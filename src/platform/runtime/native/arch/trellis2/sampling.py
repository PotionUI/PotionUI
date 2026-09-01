# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/pipelines/samplers/flow_euler.py
# (FlowEulerSampler), classifier_free_guidance_mixin.py and guidance_interval_mixin.py
# (FlowEulerGuidanceIntervalSampler's two mixins).
"""The flow-Euler sampler TRELLIS.2 runs for all three of its stages.

All three stages use one sampler class with different parameters, over two
different state types: the sparse-structure stage denoises a dense
``[B, 8, 16, 16, 16]`` tensor, the shape and texture stages a
:class:`~...sparse3d.SparseTensor` whose rows are active voxels. The loop here
is written against the operators both support (``+``, ``-``, scalar ``*``), so
one implementation covers both.

**Why this is not the generic ``sampling.denoise_loop`` + ``TrueCFG`` pair.**
Two blockers, both structural rather than stylistic:

* ``sampling.algorithms.euler.sample_euler`` builds its per-sample sigma with
  ``x.new_ones((x.shape[0],))`` and hands hooks a dense ``x0`` estimate.
  ``SparseTensor`` has no ``new_ones`` — the sparse state is ragged, so a
  per-batch broadcast is a segment operation, not a view.
* ``TrueCFG``'s ``guidance_rescale`` rescales in **velocity** space
  (``guided * std(cond_v)/std(guided)``). TRELLIS.2 rescales in **x0** space,
  and the two are not the same correction. Writing the blend factor as
  ``k`` and upstream's clean-latent map as ``x0 = A - B*v`` (``A`` carrying the
  current state ``x_t``), upstream lands on ``v' = A*(1 - k)/B + k*v`` where
  velocity-space rescaling gives ``v' = k*v``: upstream's version also shifts
  the prediction toward ``x_t``, and its ``k`` is computed from x0 stds rather
  than velocity stds. At this family's rescale strengths (0.7 for the
  sparse-structure stage, 0.5 for shape) that difference is not a rounding
  detail, so the correction is reproduced here as upstream writes it.

The ratio of two stds is invariant to Bessel's correction, so upstream's
population std over a sparse stage and torch's default unbiased std over a
dense one agree exactly here — :func:`_std_per_sample` uses one for both.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

import torch

from ...sampling.flow_schedule import _constant_shift_sigmas
from ...sparse3d import SparseTensor
from .config import StageSampling

__all__ = ["StageProgress", "sample_flow_stage", "stage_timesteps"]

#: ``model(x, t, cond)`` wants ``t`` in the 1000-scaled space the DiTs were
#: trained on, while the schedule and the guidance interval are both in the
#: normalised ``1 -> 0`` space. The scaling happens at the single call site
#: below so the two never get confused.
_TIMESTEP_SCALE = 1000.0

#: Progress callback: ``(step_index, total_steps)``.
StageProgress = Callable[[int, int], None]


def stage_timesteps(steps: int, rescale_t: float) -> List[float]:
    """The stage's ``t`` grid, descending ``1 -> 0``, length ``steps + 1``.

    ``rescale_t`` is a constant sigma shift — upstream spells it
    ``rescale_t * t / (1 + (rescale_t - 1) * t)``, which is exactly ComfyUI's
    ``time_snr_shift`` and therefore :func:`_constant_shift_sigmas`.
    """
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    grid = torch.linspace(1.0, 0.0, steps + 1, dtype=torch.float64)
    return _constant_shift_sigmas(grid, float(rescale_t)).tolist()


def _std_per_sample(value: Any) -> torch.Tensor:
    """``[B]`` std over every non-batch element of each batch element."""
    if isinstance(value, SparseTensor):
        return torch.stack([value.feats[rows].float().std() for rows in value.layout])
    return value.reshape(value.shape[0], -1).float().std(dim=-1)


def _scale_per_sample(value: Any, scale: torch.Tensor) -> Any:
    """``value`` with each batch element multiplied by its entry of ``scale``."""
    if isinstance(value, SparseTensor):
        per_row = torch.cat(
            [scale[i].expand(rows.stop - rows.start) for i, rows in enumerate(value.layout)]
        ).unsqueeze(-1)
        return value.replace(value.feats * per_row.to(value.dtype))
    return value * scale.reshape(-1, *([1] * (value.ndim - 1))).to(value.dtype)


class _FlowParametrisation:
    """The ``x_t``/``t``/velocity <-> clean-latent maps, shared by the Euler
    step and the CFG rescale (upstream's ``_pred_to_xstart`` /
    ``_xstart_to_pred``)."""

    def __init__(self, sigma_min: float) -> None:
        self.sigma_min = sigma_min

    def _coefficient(self, t: float) -> float:
        return self.sigma_min + (1.0 - self.sigma_min) * t

    def to_xstart(self, x_t: Any, t: float, pred: Any) -> Any:
        return (1.0 - self.sigma_min) * x_t - self._coefficient(t) * pred

    def from_xstart(self, x_t: Any, t: float, x_0: Any) -> Any:
        return ((1.0 - self.sigma_min) * x_t - x_0) * (1.0 / self._coefficient(t))


def _forward(model, x: Any, t: float, cond: torch.Tensor, forward_kwargs: dict) -> Any:
    timestep = torch.full(
        (x.shape[0],), _TIMESTEP_SCALE * t, device=x.device, dtype=torch.float32
    )
    return model(x, timestep, cond, **forward_kwargs)


def _guided_velocity(
    model,
    x: Any,
    t: float,
    cond: torch.Tensor,
    neg_cond: Optional[torch.Tensor],
    settings: StageSampling,
    flow: _FlowParametrisation,
    forward_kwargs: dict,
) -> Any:
    """One guided model evaluation: interval gating, CFG, then x0-space rescale.

    Outside ``guidance_interval`` the strength collapses to 1.0, which is the
    single-forward conditional path — the same short-circuit upstream's
    ``GuidanceIntervalSamplerMixin`` performs by delegating with
    ``guidance_strength=1``.
    """
    low, high = settings.guidance_interval
    strength = settings.guidance_strength if low <= t <= high else 1.0

    if strength == 1.0 or neg_cond is None:
        return _forward(model, x, t, cond, forward_kwargs)
    if strength == 0.0:
        return _forward(model, x, t, neg_cond, forward_kwargs)

    positive = _forward(model, x, t, cond, forward_kwargs)
    negative = _forward(model, x, t, neg_cond, forward_kwargs)
    guided = strength * positive + (1.0 - strength) * negative

    rescale = settings.guidance_rescale
    if rescale <= 0.0:
        return guided

    x0_positive = flow.to_xstart(x, t, positive)
    x0_guided = flow.to_xstart(x, t, guided)
    ratio = _std_per_sample(x0_positive) / _std_per_sample(x0_guided).clamp_min(1e-8)
    x0_blended = rescale * _scale_per_sample(x0_guided, ratio) + (1.0 - rescale) * x0_guided
    return flow.from_xstart(x, t, x0_blended)


@torch.no_grad()
def sample_flow_stage(
    model,
    noise: Any,
    cond: torch.Tensor,
    neg_cond: Optional[torch.Tensor],
    settings: StageSampling,
    *,
    forward_kwargs: Optional[dict] = None,
    on_step: Optional[StageProgress] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Any:
    """Denoise ``noise`` with ``model`` under ``settings`` and return the sample.

    ``noise`` is a dense tensor (sparse-structure stage) or a ``SparseTensor``
    (shape/texture stages); ``forward_kwargs`` carries the texture stage's
    ``concat_cond``. ``neg_cond`` of ``None`` runs every step conditional-only.
    """
    flow = _FlowParametrisation(settings.sigma_min)
    kwargs = dict(forward_kwargs or {})
    timesteps = stage_timesteps(settings.steps, settings.rescale_t)
    total = len(timesteps) - 1

    x = noise
    for index, (t, t_next) in enumerate(zip(timesteps[:-1], timesteps[1:])):
        if is_cancelled is not None and is_cancelled():
            break
        velocity = _guided_velocity(model, x, t, cond, neg_cond, settings, flow, kwargs)
        x = x - (t - t_next) * velocity
        if on_step is not None:
            on_step(index, total)
    return x
