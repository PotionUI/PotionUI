# Derived from: diffusers `schedulers/scheduling_minimax_h3.py` (Apache-2.0,
# "Copyright 2025 The MiniMax authors and The HuggingFace Team") --
# `MiniMaxH3Scheduler.set_timesteps`/`.scale_noise`/`.step` are ported as
# pure functions (this pipe runs a bespoke per-step loop, see
# `main.py`'s module docstring for why, rather than the stateful
# scheduler-object contract diffusers uses).
"""MiniMax-H3's rectified-flow Euler scheduler math.

Three things make this incompatible with the engine's shared flow-match
scheduler (`sampling/flow_schedule.py`), which is why it is reproduced here
rather than reused (dossier "MiniMaxH3Scheduler"):

1. **The velocity sign is reversed.** The DiT predicts a DATA-WARD velocity:
   `x0 = x_t + (1 - t) * v`, the `+` is not a typo -- the opposite of the
   usual flow-match `x0 = x_t - sigma * v`.
2. **`t = 1 - sigma`, and `t = 1` is CLEAN** -- inverted from the usual
   flow-match convention where the timestep IS the sigma.
3. **The sigma grid is `linspace(1, 0, num_inference_steps + 1)`** pushed
   through an exponential shift, with float32 collisions collapsed by
   `torch.unique_consecutive`. A step is a model EVALUATION (an NFE), so a
   run of `n` steps needs `n + 1` grid values: the `n` knots it is
   conditioned on plus the terminal `0` it lands on.

MiniMax-H3 runs TWO such schedules per request (`shift=12.0` video,
`shift=3.0` audio), same step count, inside ONE transformer call per step --
see `layout.build_row_timesteps` for how the per-row timestep vector is built
from a `(video_timestep, audio_timestep)` pair at each step.

**Scheduler vs shift.** The underlying `t` grid (`build_t_grid`) is chosen
once per request and BOTH streams are derived from that one grid through
their own shift. That is what keeps the dual clock paired under any
scheduler: at knot `i` the video and audio sigmas are two images of the same
`t_i`, which is the property `build_row_timesteps` relies on to pack one
step's two timesteps into one forward. `simple` is the reference grid above;
`beta` reshapes only `t`.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch

Tensor = torch.Tensor

VIDEO_SHIFT = 12.0
AUDIO_SHIFT = 3.0
KEYFRAME_NOISE_AUG = 0.999

SIMPLE_SCHEDULER = "simple"
BETA_SCHEDULER = "beta"
SCHEDULERS = (SIMPLE_SCHEDULER, BETA_SCHEDULER)

# The beta scheduler's shape parameters. Both below 1 makes the density
# U-shaped, so its quantile function clusters knots at BOTH ends of the
# trajectory: more steps spent on layout (high sigma) and on the final
# detail (low sigma), fewer in the middle.
BETA_ALPHA = 0.6
BETA_BETA = 0.6


@dataclass(frozen=True)
class MiniMaxH3Sigmas:
    """One modality's sigma grid + the timesteps the DiT is conditioned on.

    `sigmas` holds `num_inference_steps + 1` values (collisions from the
    shift already collapsed), descending to exactly `0.0`; `timesteps = 1 -
    sigmas[:-1]` drives `len(sigmas) - 1 == num_inference_steps` model
    evaluations.
    """

    sigmas: Tensor
    timesteps: Tensor


def build_t_grid(num_inference_steps: int, scheduler: str = SIMPLE_SCHEDULER) -> Tensor:
    """The underlying knot grid, descending from exactly 1.0 to exactly 0.0,
    BEFORE either stream's shift is applied. `n` steps produce `n + 1` knots.

    - ``simple``: `linspace(1, 0, n + 1)` -- the reference scheduler's own
      grid, i.e. `q_i = (n - i)/n` for `i = 0..n-1` plus the terminal `0`.
    - ``beta``: the same uniform quantiles pushed through the Beta(0.6, 0.6)
      quantile function. `ppf` is monotonic with `ppf(0) = 0` and
      `ppf(1) = 1`, so the endpoints stay exact and only the interior knots
      move -- toward both ends, per :data:`BETA_ALPHA`/:data:`BETA_BETA`.

    Computed in float64 and cast down once, so the beta grid does not inherit
    the quantile solver's error at float32 resolution.

    `num_inference_steps` counts model EVALUATIONS (NFE), which is what the
    reference means by a step: ModelTC's Minimax-H3-Turbo README fixes the
    unshifted grid at `q_i = (N - i)/N` for NFE = N, so NFE = 4 must give
    `[1, 0.75, 0.5, 0.25, 0]` (video sigmas `[1, .973, .923, .8] -> 0` at
    shift 12). Changed here 2026-08-11 -- it previously read the argument as
    a GRID size and silently ran one evaluation fewer on the wrong knots.
    """
    if num_inference_steps < 1:
        raise ValueError(f"num_inference_steps must be >= 1, got {num_inference_steps}")
    grid_points = int(num_inference_steps) + 1
    if scheduler == SIMPLE_SCHEDULER:
        return torch.linspace(1.0, 0.0, grid_points, dtype=torch.float32)
    if scheduler == BETA_SCHEDULER:
        from scipy.stats import beta as beta_distribution

        quantiles = torch.linspace(1.0, 0.0, grid_points, dtype=torch.float64)
        knots = beta_distribution.ppf(quantiles.numpy(), BETA_ALPHA, BETA_BETA)
        return torch.from_numpy(knots).to(torch.float32)
    raise ValueError(f"scheduler must be one of {SCHEDULERS}, got {scheduler!r}")


def build_sigma_schedule(
    num_inference_steps: int, shift: float, scheduler: str = SIMPLE_SCHEDULER,
) -> MiniMaxH3Sigmas:
    """The scheduler's `t` grid (`num_inference_steps + 1` knots for
    `num_inference_steps` model evaluations) pushed through the exponential
    shift `sigma' = shift*sigma / (1 + (shift-1)*sigma)`, float32 collisions
    from the shift collapsed with `unique_consecutive` (NOT a general
    dedup -- collisions only ever occur consecutively here, since the input
    is monotonic and the shift map is monotonic)."""
    base = build_t_grid(num_inference_steps, scheduler)
    sigmas = shift * base / (1 + (shift - 1) * base)
    sigmas = torch.unique_consecutive(sigmas)
    timesteps = 1.0 - sigmas[:-1]
    return MiniMaxH3Sigmas(sigmas=sigmas, timesteps=timesteps)


def parse_manual_sigmas(raw: str, *, label: str = "manual sigmas") -> MiniMaxH3Sigmas:
    """A user-authored sigma grid, in the same comma-separated textbox format
    the Wan/LTX families' `manual_sigmas` knob uses.

    Validated rather than clamped (unlike `flow_schedule._manual_sigmas`,
    which silently pins its endpoints to 1.0/0.0): H3's loop reads
    `sigmas[i+1]` as the Euler target of step `i`, so a list that does not
    END at exactly 0.0 leaves the final latent partially noised with no
    signal that anything went wrong. Descending is likewise strict -- an
    equal pair would make `euler_step`'s `sigma_next/sigma` ratio 1.0, a step
    that costs a full transformer call and moves nothing.

    Length is the SIGMA GRID size, not the model-evaluation count: N values
    drive N-1 steps. That is the grid `build_sigma_schedule(N - 1, ...)`
    produces -- the manual textbox spells out sigmas, while `steps` counts
    evaluations, so the two knobs are one apart by construction.
    """
    tokens = [tok for tok in raw.replace(",", " ").split() if tok]
    try:
        values = [float(tok) for tok in tokens]
    except ValueError as exc:
        raise ValueError(f"{label}: expected comma-separated numbers, got {raw!r}") from exc
    if len(values) < 2:
        raise ValueError(
            f"{label}: needs at least 2 values (a starting sigma and the terminal 0.0), got {len(values)}"
        )
    if any(not 0.0 <= value <= 1.0 for value in values):
        raise ValueError(f"{label}: every value must lie in [0, 1], got {values}")
    for current, following in zip(values, values[1:]):
        if following >= current:
            raise ValueError(f"{label}: values must be strictly decreasing, got {values}")
    if values[-1] != 0.0:
        raise ValueError(f"{label}: the last value must be exactly 0.0, got {values[-1]}")
    sigmas = torch.tensor(values, dtype=torch.float32)
    return MiniMaxH3Sigmas(sigmas=sigmas, timesteps=1.0 - sigmas[:-1])


def resolve_schedules(
    num_inference_steps: int, manual_video: str = "", manual_audio: str = "",
    scheduler: str = SIMPLE_SCHEDULER,
) -> tuple[MiniMaxH3Sigmas, MiniMaxH3Sigmas]:
    """The pair of schedules one generation runs on, either computed from
    `num_inference_steps` under `scheduler` (both empty strings -- the
    default) or taken from a user-authored list.

    A manual list on ONE stream fills the other in at its EVALUATION count
    (`timesteps`, not `sigmas` -- an N-value manual grid runs N-1 steps and
    must be met by an N-value computed grid), so the two still advance in
    lockstep: they are consumed by ONE transformer call
    per step (`layout.build_row_timesteps`), which has no way to represent a
    stream that has already finished. A length disagreement is refused here
    rather than silently truncating the audio trajectory.

    A manual list together with a non-`simple` scheduler is REFUSED rather
    than resolved by precedence: the two knobs are both answers to "where do
    the knots go", and a silent winner would have the scheduler picker read
    as if it were doing something on a run it has no say in.
    """
    if scheduler not in SCHEDULERS:
        raise ValueError(f"scheduler must be one of {SCHEDULERS}, got {scheduler!r}")
    if scheduler != SIMPLE_SCHEDULER and (manual_video.strip() or manual_audio.strip()):
        raise ValueError(
            f"manual sigmas and the '{scheduler}' scheduler cannot be combined -- a manual grid IS the "
            f"schedule. Clear the manual list, or set the scheduler back to '{SIMPLE_SCHEDULER}'"
        )

    video = parse_manual_sigmas(manual_video, label="'manual_sigmas'") if manual_video.strip() else None
    audio = parse_manual_sigmas(manual_audio, label="'manual_audio_sigmas'") if manual_audio.strip() else None

    if video is None and audio is None:
        video = build_sigma_schedule(num_inference_steps, VIDEO_SHIFT, scheduler)
        audio = build_sigma_schedule(num_inference_steps, AUDIO_SHIFT, scheduler)
    elif video is None:
        video = build_sigma_schedule(int(audio.timesteps.numel()), VIDEO_SHIFT, scheduler)
    elif audio is None:
        audio = build_sigma_schedule(int(video.timesteps.numel()), AUDIO_SHIFT, scheduler)

    if video.timesteps.numel() != audio.timesteps.numel():
        raise ValueError(
            f"video and audio schedules must drive the same number of steps, got "
            f"{int(video.timesteps.numel())} (video) vs {int(audio.timesteps.numel())} (audio)"
        )
    return video, audio


def scale_noise(sample: Tensor, timestep: float | Tensor, noise: Tensor) -> Tensor:
    """Rectified-flow forward process in H3's `t` convention:
    `x_t = t*x_0 + (1-t)*noise`. Used to noise conditioning anchors (`t =
    KEYFRAME_NOISE_AUG`), NOT looked up in any schedule -- `timestep` is
    taken at face value."""
    if not isinstance(timestep, Tensor):
        timestep = torch.tensor(timestep, dtype=sample.dtype, device=sample.device)
    timestep = timestep.to(device=sample.device, dtype=sample.dtype)
    while timestep.ndim < sample.ndim:
        timestep = timestep.unsqueeze(-1)
    return timestep * sample + (1.0 - timestep) * noise


def data_estimate(model_output: Tensor, timestep: float | Tensor, sample: Tensor) -> Tensor:
    """`x0 = x_t + (1 - timestep) * v` -- the data-ward velocity convention,
    the `+` per the module docstring (opposite of the usual flow-match
    `x0 = x_t - sigma * v`). Shared by :func:`euler_step` and any preview
    hook that wants a cheap running x0 estimate without duplicating this
    arithmetic."""
    if not isinstance(timestep, Tensor):
        timestep = torch.tensor(timestep, dtype=sample.dtype, device=sample.device)
    sigma_from_timestep = 1.0 - timestep.to(device=sample.device, dtype=sample.dtype)
    while sigma_from_timestep.ndim < sample.ndim:
        sigma_from_timestep = sigma_from_timestep.unsqueeze(-1)
    return sample + sigma_from_timestep * model_output


def euler_step(
    model_output: Tensor, timestep: float | Tensor, sample: Tensor,
    sigma: float | Tensor, sigma_next: float | Tensor,
) -> Tensor:
    """One Euler (`eta=0`) update, written as the `x_t`/`x0` blend.

    `x_next = ratio*x_t + (1-ratio)*x0` with `ratio = sigma_next / sigma`,
    evaluated in float32 for half-precision samples. `sigma` here is
    DELIBERATELY the schedule's own grid value, not `1 - timestep`
    recomputed -- for `sigma < 0.5` that float32 round trip is not exact, and
    the reference keeps the two sources apart (dossier trap 3 under
    "MiniMaxH3Scheduler")."""
    denoised = data_estimate(model_output, timestep, sample)

    compute_dtype = torch.float32 if sample.dtype in (torch.float16, torch.bfloat16) else sample.dtype
    sigma_t = torch.as_tensor(sigma, device=sample.device, dtype=compute_dtype)
    sigma_next_t = torch.as_tensor(sigma_next, device=sample.device, dtype=compute_dtype)
    ratio = sigma_next_t / sigma_t
    prev_sample = ratio * sample.to(dtype=compute_dtype) + (1.0 - ratio) * denoised.to(dtype=compute_dtype)
    return prev_sample.to(dtype=sample.dtype)
