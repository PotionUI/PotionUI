# Derived from: diffusers `src/diffusers/modular_pipelines/minimax_music3/{before_denoise,
# denoise,decoders}.py` (Apache-2.0, "Copyright 2026 The MiniMax Team and The HuggingFace
# Team") for the windowing/splice-and-blend scheme (200-frame windows, 100-frame hop,
# per-step overlap pinning toward the previous window's trailing latents, the 86/258
# crop-latent constants) and for `FlowMatchEulerDiscreteScheduler(num_train_timesteps=1,
# shift=1.0, invert_sigmas=True)`'s exact `set_timesteps`/`step` arithmetic (re-derived
# below as a standalone closed form, not imported — see `_flow_sigmas`'s docstring).
# Deliberately NOT ComfyUI's overlap-add windowing (see PORT_PLAN.md's reference-source
# rules): the two engines' audio differs at window boundaries by construction, which is
# expected, not a bug.

"""MiniMax-Music3's bespoke windowed Euler flow-matching loop.

Doesn't reuse this engine's shared `denoise_prenoised`/`sample_euler` machinery: the
per-step overlap pinning (blending the shared boundary toward the previous window's
latents at every Euler step, not just at the seam) has no hook point in the generic
sampler loop, and the flow-matching time convention here is inverted from this engine's
usual descending-sigma one (`t=0` is noise, `t=1` is data — see `_flow_sigmas`). A song
longer than one window (200 AR frames, ~689 latents) is denoised as a sequence of
overlapping windows that each start from the previous window's trailing latents so the
song stays coherent across window boundaries; :func:`chunk_starts` computes where each
window begins, :func:`denoise_windowed` runs the whole sequence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import torch
from torch import Tensor

from ...errors import SamplingCancelled
from .model import MiniMaxMusic3Model, latent_length

# Autoregressive-frame windowing (diffusers `before_denoise.py`'s `_CHUNK_FRAMES`/`_CHUNK_HOP`).
CHUNK_FRAMES = 200
CHUNK_HOP = 100

# Decode-side crop constants (diffusers `decoders.py`): neighbouring windows overlap by
# ~344 latent frames; every window after the first drops its leading 86, every window
# before the last drops its trailing 344-86=258, so the kept spans tile the song. Latent
# units — a consumer decoding to samples multiplies by the vocoder's hop length (512).
CROP_LEFT_LATENT = 86
CROP_RIGHT_LATENT = 344 - 86

# Latent frames carried from one window into the next as the overlap-blend prompt
# (diffusers `denoise.py`'s `_OVERLAP_LATENT_LENGTH`) — half of the ~344-latent overlap.
_CARRY_LATENT_LENGTH = 172


def chunk_starts(num_frames: int) -> list[int]:
    """Frame index at which each 200-frame denoising window starts."""
    if num_frames <= CHUNK_FRAMES:
        return [0]
    return list(range(0, num_frames - CHUNK_HOP, CHUNK_HOP))


def crop_bounds(window_index: int, num_windows: int) -> tuple[int, int]:
    """``(left, right)`` latent frames to drop from one window's decoded span before
    concatenating windows — 0 at the left edge for the first window, 0 at the right
    edge for the last, so the kept spans tile the song without gap or duplication."""
    left = 0 if window_index == 0 else CROP_LEFT_LATENT
    right = 0 if window_index == num_windows - 1 else CROP_RIGHT_LATENT
    return left, right


def _flow_sigmas(steps: int) -> Tensor:
    """The exact per-step flow-matching time values for
    ``FlowMatchEulerDiscreteScheduler(num_train_timesteps=1, shift=1.0,
    invert_sigmas=True)`` fed ``sigmas=linspace(1.0, 1/steps, steps)``, re-derived
    (not imported — see the module's `# Derived from:` note) from the scheduler's own
    ``set_timesteps``:

      1. ``shift=1.0`` makes the resolution-shift step (``shift*s/(1+(shift-1)*s)``) an
         identity, so the input ``sigmas`` pass through unchanged.
      2. ``invert_sigmas=True`` flips them: ``sigma' = 1 - sigma``, and because
         ``num_train_timesteps=1``, ``timesteps == sigma'`` — the DiT's Fourier
         embedding consumes this value directly as flow time in ``[0, 1]``.
      3. A terminal ``1.0`` (pure data) is appended, reached only by the last step's
         ``dt``, never passed to the model.

    ``linspace(1.0, 1/steps, steps)`` inverted gives ``t`` running ``0, ..., 1 - 1/steps``
    (0 = noise, matching the family's documented convention); returns ``steps + 1``
    values — ``result[i]`` is the model timestep for Euler step ``i < steps``, and
    ``result[i + 1] - result[i]`` is that step's ``dt``.
    """
    raw = torch.linspace(1.0, 1.0 / steps, steps, dtype=torch.float64)
    t = 1.0 - raw
    return torch.cat((t, torch.ones(1, dtype=torch.float64))).to(torch.float32)


@dataclass
class _WindowCarry:
    latent: Tensor | None = None
    condition: Tensor | None = None


def denoise_windowed(
    model: MiniMaxMusic3Model,
    frame_hiddens: Tensor,
    *,
    steps: int,
    cfg_scale: float,
    generator: torch.Generator,
    device: torch.device,
    dtype: torch.dtype,
    on_step: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[Tensor]:
    """Denoise one song's ``frame_hiddens`` across its windows.

    ``frame_hiddens``: ``(1, F, num_condition_layers * condition_hidden_dim)`` CPU or
    device tensor from the autoregressive stage. Returns the list of per-window
    denoised latent tensors, UNCROPPED — a caller vocodes and crops each with
    :func:`crop_bounds` (S4/S5) to stitch the final waveform.

    Classifier-free guidance: two DiT passes per step, conditional and
    (checkpoint-specific) zeroed condition, combined as
    ``uncond + cfg_scale * (cond - uncond)``. One ``generator`` drives every window's
    initial noise, so a fixed seed reproduces the whole song.
    """
    num_frames = frame_hiddens.shape[1]
    starts = chunk_starts(num_frames)
    num_windows = len(starts)
    total_steps = num_windows * steps
    sigmas = _flow_sigmas(steps)

    carry = _WindowCarry()
    latent_chunks: list[Tensor] = []

    for k, start in enumerate(starts):
        end = min(start + CHUNK_FRAMES, num_frames)
        condition = model.encode_condition(frame_hiddens[:, start:end].to(device)).to(dtype)

        overlap = 0
        if carry.latent is not None:
            overlap = min(carry.latent.shape[-1], condition.shape[1])
            condition = condition.clone()
            condition[:, :overlap] = carry.condition[:, :overlap]

        t_lat = condition.shape[1]
        latents = torch.randn(
            (1, model.config.in_channels, t_lat), generator=generator, device=device, dtype=dtype,
        )
        noise_prompt = latents[..., :overlap].clone() if overlap > 0 else None
        zero_condition = torch.zeros_like(condition)

        for i in range(steps):
            global_step = k * steps + i
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=global_step)

            current_t = sigmas[i]
            next_t = sigmas[i + 1]

            if overlap > 0:
                time_value = current_t.to(latents.dtype)
                latents = latents.clone()
                latents[..., :overlap] = (
                    (1.0 - (1.0 - 1e-6) * time_value) * noise_prompt
                    + time_value * carry.latent[..., :overlap]
                )

            timestep = current_t.expand(latents.shape[0]).to(device=device, dtype=latents.dtype)
            with torch.no_grad():
                velocity_cond = model(latents, timestep, condition)
                velocity_uncond = model(latents, timestep, zero_condition)
            velocity = velocity_uncond + cfg_scale * (velocity_cond - velocity_uncond)

            dt = (next_t - current_t).to(latents.dtype)
            latents = latents + dt * velocity

            if on_step is not None:
                on_step(global_step, total_steps)

        if overlap > 0:
            latents = latents.clone()
            latents[..., :overlap] = carry.latent[..., :overlap]

        overlap_start = max(0, t_lat - 2 * _CARRY_LATENT_LENGTH)
        overlap_end = max(overlap_start, t_lat - _CARRY_LATENT_LENGTH)
        carry = _WindowCarry(latent=latents[..., overlap_start:overlap_end], condition=condition[:, overlap_start:overlap_end])

        latent_chunks.append(latents)

    return latent_chunks


__all__ = [
    "CHUNK_FRAMES",
    "CHUNK_HOP",
    "CROP_LEFT_LATENT",
    "CROP_RIGHT_LATENT",
    "chunk_starts",
    "crop_bounds",
    "denoise_windowed",
    "latent_length",
]
