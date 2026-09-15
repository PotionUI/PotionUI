# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/nar.py (Apache-2.0)

"""The acoustic (NAR) stage: split the generated codec tokens into
non-overlapping windows sized to the model's context, then solve each
window's flow-matching ODE (32-step midpoint by default) against its own
cached AR prefix.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import torch

from ...errors import SamplingCancelled
from .model import YuE2Model
from .protocol import CODEC_OFFSET, CODEC_SIZE, CONTEXT, MUSIC_END, chunk_ranges


@dataclass
class Chunk:
    """One acoustic window: the full text+codec prefix to re-embed, and this window's noise slice."""

    ar_tokens: list[int]
    noise: torch.Tensor


def song_chunks(prefix_ids: list[int], codec_ids: list[int], seed: int, context: int = CONTEXT) -> list[Chunk]:
    """Split ``codec_ids`` into windows and draw the whole song's noise once,
    so window boundaries never perturb the initial noise of a later window.
    """
    if not prefix_ids or not codec_ids:
        raise ValueError("prefix and codec token sequences must be nonempty")
    if min(codec_ids) < 0 or max(codec_ids) >= CODEC_SIZE:
        raise ValueError("codec token IDs are outside the acoustic codebook")
    ranges = chunk_ranges(len(codec_ids), len(prefix_ids), context)
    generator = torch.Generator(device="cpu").manual_seed(seed)
    noise = torch.randn((len(codec_ids), 64), dtype=torch.float32, device="cpu", generator=generator)
    return [
        Chunk(prefix_ids + [value + CODEC_OFFSET for value in codec_ids[a:b]] + [MUSIC_END], noise[a:b])
        for a, b in ranges
    ]


@torch.inference_mode()
def synthesize(
    model: YuE2Model,
    prefix_ids: list[int],
    codec_ids: list[int],
    seed: int,
    steps: int = 32,
    context: int = CONTEXT,
    is_cancelled: Callable[[], bool] | None = None,
    on_step: Callable[[int, int], None] | None = None,
) -> torch.Tensor:
    """Solve every window serially, returning ``[1, T, 64]`` CPU fp32 latents."""
    if steps < 1:
        raise ValueError("steps must be a positive integer")
    chunks = song_chunks(prefix_ids, codec_ids, seed, context)
    device = next(model.parameters()).device
    dtype = next(model.parameters()).dtype
    total_steps = steps * len(chunks)
    outputs: list[torch.Tensor] = []
    for chunk_index, chunk in enumerate(chunks):
        if is_cancelled is not None and is_cancelled():
            raise SamplingCancelled(chunk_index * steps)
        ar_ids = torch.tensor([chunk.ar_tokens], device=device, dtype=torch.long)
        nar_context = model.new_nar_context(ar_ids, num_latent_frames=chunk.noise.shape[0])
        state = chunk.noise.to(device=device, dtype=dtype)
        dt = 1.0 / steps
        for step in range(steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(chunk_index * steps + step)
            t = 1.0 - step * dt
            raw = torch.logit(torch.tensor(t, dtype=torch.float64)).clamp(-20, 20).item()
            first = model.nar_velocity(nar_context, state, raw)
            mid = state - first * (dt / 2)
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(chunk_index * steps + step)
            raw_mid = torch.logit(torch.tensor(t - dt / 2, dtype=torch.float64)).clamp(-20, 20).item()
            state = state - model.nar_velocity(nar_context, mid, raw_mid) * dt
            if on_step is not None:
                on_step(chunk_index * steps + step + 1, total_steps)
        result = state.float().cpu()
        if not torch.isfinite(result).all():
            raise FloatingPointError("yue2 acoustic flow matching produced non-finite latents")
        outputs.append(result)
    return torch.cat(outputs, dim=0).unsqueeze(0)


__all__ = ["Chunk", "song_chunks", "synthesize"]
