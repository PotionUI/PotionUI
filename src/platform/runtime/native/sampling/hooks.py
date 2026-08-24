"""Step hooks for the flow-matching sampler.

A :class:`StepHook` observes the loop without steering it: progress reporting,
latent previews, per-step LoRA multipliers, step-skip caches, etc. Hooks are
dispatched in priority order (higher ``priority`` first) and are *isolated* —
a hook that raises is logged and skipped, never aborting the generation. (A
failed preview decode must not kill an otherwise-good image.)

Cancellation is deliberately NOT a hook: it is a hard control-flow signal the
sampler checks directly and turns into :class:`SamplingCancelled`.
"""

from __future__ import annotations

import logging
from typing import Protocol, Sequence, runtime_checkable

import torch

from ..errors import SamplingNumericsError

logger = logging.getLogger(__name__)

Tensor = torch.Tensor

# Default cadence for the NaN/Inf watchdog: check the latent every N steps
# (plus always after step 0 and on the final step). ``0`` disables it.
DEFAULT_NAN_CHECK_INTERVAL = 4


@runtime_checkable
class StepHook(Protocol):
    """Observer notified around and during the sampling loop.

    ``priority`` (optional, default 0) orders dispatch: higher runs first.
    """

    def on_start(self, total_steps: int) -> None: ...

    def on_step(
        self,
        step_index: int,
        total_steps: int,
        x: Tensor,
        sigma: float,
        denoised_x0: Tensor | None,
    ) -> None: ...

    def on_end(self) -> None: ...


class BaseStepHook:
    """No-op base so subclasses only override what they use."""

    priority: int = 0

    def on_start(self, total_steps: int) -> None:  # noqa: D401
        pass

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        pass

    def on_end(self) -> None:
        pass


class ProgressHook(BaseStepHook):
    """Reports fractional progress ``(step_index + 1) / total_steps`` per step.

    ``callback(fraction, step_index, total_steps)`` is invoked after each step.
    """

    def __init__(self, callback, priority: int = 100) -> None:
        self._callback = callback
        self.priority = priority
        self._total = 0

    def on_start(self, total_steps: int) -> None:
        self._total = total_steps

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        fraction = (step_index + 1) / max(total_steps, 1)
        self._callback(fraction, step_index, total_steps)


class PreviewHook(BaseStepHook):
    """Decodes the running x0 estimate to a preview every ``every_n`` steps.

    ``decode_fn(denoised_x0) -> preview`` is injected (no VAE dependency here);
    ``callback(preview, step_index)`` receives the decoded result. The FIRST and
    final steps always preview — the first so the workbench comes alive
    immediately (a rough x0 exists from step 1), the last so the final frame is
    never skipped by the modulo. Decode/callback exceptions propagate to the
    dispatcher, which isolates them.
    """

    def __init__(self, decode_fn, every_n: int, callback, priority: int = 50) -> None:
        if every_n < 1:
            raise ValueError(f"every_n must be >= 1, got {every_n}")
        self._decode_fn = decode_fn
        self._every_n = every_n
        self._callback = callback
        self.priority = priority

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        if denoised_x0 is None:
            return
        is_edge = step_index == 0 or step_index == total_steps - 1
        if not is_edge and (step_index + 1) % self._every_n != 0:
            return
        preview = self._decode_fn(denoised_x0)
        self._callback(preview, step_index)


class NumericsWatchdog(BaseStepHook):
    """Fail fast when the running latent goes NaN/Inf (silent-black-image guard).

    Checks ``torch.isfinite(x0)`` then ``torch.isfinite(x)`` every ``interval``
    steps, plus always after step 0 and on the final step (early detection
    matters — the reported bug corrupted by step ~5). ``x0`` (the model's own
    denoised estimate) is checked FIRST: a bad ``x0`` with a still-finite ``x``
    points at the model forward itself, while ``x`` alone going bad points at
    the sampler's own predictor/corrector math instead -- the error's
    ``tensor_name`` records which one tripped. ``.all()`` forces a GPU→CPU sync,
    so the every-K cadence keeps overhead negligible vs a 20-step run;
    ``interval == 0`` disables it entirely. The check is READ-ONLY (never
    mutates ``x``/``x0`` — clean runs stay byte-identical). Runs at high
    priority so it fires before preview/progress on a bad step. On detection
    raises :class:`SamplingNumericsError`, which propagates out of
    :func:`run_hooks` (see below) as a normal generation failure carrying the
    step, sampler, active attention backend, failing tensor, and — when the
    run crosses a multi-expert boundary — the active expert at that step.
    """

    priority = 1000  # before preview/progress: a corrupt latent should fail fast

    def __init__(self, sampler_name: str | None = None,
                 interval: int = DEFAULT_NAN_CHECK_INTERVAL,
                 switch_step: int | None = None) -> None:
        self.sampler_name = sampler_name
        self.interval = int(interval)
        self.switch_step = switch_step

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        if self.interval <= 0:
            return
        is_edge = step_index == 0 or step_index == total_steps - 1
        if not (is_edge or (step_index + 1) % self.interval == 0):
            return
        bad_tensor = None
        if denoised_x0 is not None and not torch.isfinite(denoised_x0).all():
            bad_tensor = "x0"
        elif not torch.isfinite(x).all():
            bad_tensor = "x"
        if bad_tensor is not None:
            backend = None
            try:  # best-effort — never let backend resolution mask the real error
                from ..attention import get_attention_backend
                backend = get_attention_backend()
            except Exception:  # noqa: BLE001
                pass
            raise SamplingNumericsError(
                step_index, self.sampler_name, backend,
                tensor_name=bad_tensor, switch_step=self.switch_step,
            )


def with_numerics_watchdog(hooks, sampler_name: str | None, sampler_options: dict | None):
    """Append a :class:`NumericsWatchdog` to ``hooks`` (the uniform install point
    both ``denoise`` and ``denoise_prenoised`` use). ``sampler_options`` may carry
    ``nan_check_interval`` to override the every-N cadence (``0`` disables) and
    ``discontinuity_steps`` (see :func:`~..denoise_loop.denoise`'s
    ``expert_boundary``) to attribute a trip to the active expert."""
    opts = sampler_options or {}
    interval = opts.get("nan_check_interval", DEFAULT_NAN_CHECK_INTERVAL)
    discontinuity_steps = opts.get("discontinuity_steps")
    switch_step = min(discontinuity_steps) if discontinuity_steps else None
    return tuple(hooks) + (NumericsWatchdog(sampler_name, interval, switch_step),)


def _ordered(hooks: Sequence[StepHook]) -> list[StepHook]:
    """Priority-descending, stable for equal priorities (input order kept)."""
    return sorted(hooks, key=lambda h: getattr(h, "priority", 0), reverse=True)


def run_hooks(hooks: Sequence[StepHook], method: str, *args) -> None:
    """Dispatch ``method`` to every hook in priority order, isolating failures.

    A hook raising in one method is logged and skipped; the remaining hooks
    still run and the sampler continues. The ONE exception is
    :class:`SamplingNumericsError` (the NaN/Inf watchdog): it is a hard
    control-flow signal like cancellation, so it propagates out to fail the
    generation rather than being swallowed.
    """
    for hook in _ordered(hooks):
        fn = getattr(hook, method, None)
        if fn is None:
            continue
        try:
            fn(*args)
        except SamplingNumericsError:
            raise
        except Exception:  # noqa: BLE001 — hook isolation is the whole point
            logger.exception("step hook %r failed in %s; continuing", hook, method)
