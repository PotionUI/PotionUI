from __future__ import annotations

from dataclasses import dataclass

import torch

from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    AUDIO_LATENTS_PER_SECOND,
    FPS,
    FRAMES_PER_CHUNK,
    LATENTS_PER_CHUNK,
    MAX_DURATION_S,
    audio_latent_num_frames,
)

Tensor = torch.Tensor

PARTIAL_LATENTS = 2
MIN_BRIDGE_CHUNKS = 3
MIN_LOOP_CHUNKS = MIN_BRIDGE_CHUNKS
OPENING_CHUNKS = 1
BRIDGE_CONDITION_ROWS = True
SOUND_CHUNK_STEP = 3
MAX_WINDOW_CHUNKS = (int(MAX_DURATION_S * FPS) - LATENTS_PER_CHUNK) // FRAMES_PER_CHUNK
CHUNK_AUDIO_LATENTS = FRAMES_PER_CHUNK * AUDIO_LATENTS_PER_SECOND // FPS
DECODE_SEGMENT_CHUNKS = MAX_WINDOW_CHUNKS - 1
AUDIO_CONTEXT_LATENTS = 4 * AUDIO_LATENTS_PER_SECOND


class LoopRequestError(ValueError):
    pass


def audio_offset(chunks: int) -> int:
    return round(chunks * FRAMES_PER_CHUNK * AUDIO_LATENTS_PER_SECOND / FPS)


@dataclass(frozen=True)
class LoopPlan:
    loop_chunks: int
    bridge_chunks: int
    with_sound: bool

    @property
    def anchor_chunks(self) -> int:
        return self.loop_chunks + OPENING_CHUNKS

    @property
    def opening_start(self) -> int:
        return LATENTS_PER_CHUNK * OPENING_CHUNKS

    @property
    def opening_audio_start(self) -> int:
        return audio_offset(OPENING_CHUNKS)

    @property
    def head_chunk(self) -> int:
        return self.anchor_chunks - self.bridge_chunks + 1

    @property
    def loop_frames(self) -> int:
        return FRAMES_PER_CHUNK * self.loop_chunks

    @property
    def seconds(self) -> float:
        return self.loop_frames / FPS

    @property
    def anchor_frames(self) -> int:
        return FRAMES_PER_CHUNK * self.anchor_chunks + LATENTS_PER_CHUNK

    @property
    def bridge_frames(self) -> int:
        return FRAMES_PER_CHUNK * self.bridge_chunks + LATENTS_PER_CHUNK

    @property
    def anchor_latents(self) -> int:
        return LATENTS_PER_CHUNK * self.anchor_chunks + PARTIAL_LATENTS

    @property
    def bridge_latents(self) -> int:
        return LATENTS_PER_CHUNK * self.bridge_chunks + PARTIAL_LATENTS

    @property
    def head_start(self) -> int:
        return LATENTS_PER_CHUNK * self.head_chunk

    @property
    def head_latents(self) -> int:
        return LATENTS_PER_CHUNK

    @property
    def tail_start(self) -> int:
        return LATENTS_PER_CHUNK * (self.bridge_chunks - 1)

    @property
    def tail_latents(self) -> int:
        return LATENTS_PER_CHUNK + PARTIAL_LATENTS

    @property
    def anchor_audio_latents(self) -> int:
        return audio_latent_num_frames(self.anchor_frames)

    @property
    def bridge_audio_latents(self) -> int:
        return audio_latent_num_frames(self.bridge_frames)

    @property
    def head_audio_start(self) -> int:
        return audio_offset(self.head_chunk)

    @property
    def head_audio_latents(self) -> int:
        return CHUNK_AUDIO_LATENTS

    @property
    def tail_audio_start(self) -> int:
        return self.loop_audio_latents - (self.head_audio_start - self.opening_audio_start)

    @property
    def tail_audio_latents(self) -> int:
        return min(self.bridge_audio_latents - self.tail_audio_start,
                   self.anchor_audio_latents - self.opening_audio_start)

    @property
    def loop_audio_latents(self) -> int:
        return audio_offset(self.loop_chunks)

    @property
    def bridge_anchors(self) -> tuple[int, ...]:
        return tuple(range(self.head_latents)) + tuple(range(self.tail_start, self.tail_start + self.tail_latents))


def max_loop_chunks(*, with_sound: bool) -> int:
    step = SOUND_CHUNK_STEP if with_sound else 1
    return step * ((MAX_WINDOW_CHUNKS - OPENING_CHUNKS) // step)


def max_loop_seconds(*, with_sound: bool) -> float:
    return FRAMES_PER_CHUNK * max_loop_chunks(with_sound=with_sound) / FPS


def plan_loop(requested_frames: int, *, with_sound: bool) -> LoopPlan:
    if requested_frames < 1:
        raise LoopRequestError(f"A loop needs a length above zero, got {requested_frames} frame(s).")
    step = SOUND_CHUNK_STEP if with_sound else 1
    minimum = -(-MIN_LOOP_CHUNKS // step) * step
    chunks = max(step * round(requested_frames / (FRAMES_PER_CHUNK * step)), minimum)
    if chunks > max_loop_chunks(with_sound=with_sound):
        raise LoopRequestError(
            f"A perfect loop can be at most {max_loop_seconds(with_sound=False):.1f} seconds long "
            f"({max_loop_seconds(with_sound=True):.1f} with sound), because its first pass renders the whole "
            f"loop and the moment before it, and one pass is at most {MAX_DURATION_S:g} seconds. {requested_frames / FPS:.1f} seconds "
            f"were asked for."
        )
    bridge = min(max(MIN_BRIDGE_CHUNKS, (chunks + 2) // 2) + OPENING_CHUNKS, chunks)
    return LoopPlan(loop_chunks=chunks, bridge_chunks=bridge, with_sound=with_sound)


def bridge_head(anchor_latent: Tensor, plan: LoopPlan) -> Tensor:
    return anchor_latent[:, :, plan.head_start: plan.head_start + plan.head_latents]


def bridge_tail(anchor_latent: Tensor, plan: LoopPlan) -> Tensor:
    return anchor_latent[:, :, plan.opening_start: plan.opening_start + plan.tail_latents]


def bridge_head_audio(anchor_audio: Tensor, plan: LoopPlan) -> Tensor:
    return anchor_audio[..., plan.head_audio_start: plan.head_audio_start + plan.head_audio_latents]


def bridge_tail_audio(anchor_audio: Tensor, plan: LoopPlan) -> Tensor:
    return anchor_audio[..., plan.opening_audio_start: plan.opening_audio_start + plan.tail_audio_latents]


def assemble_loop_latent(anchor_latent: Tensor, bridge_latent: Tensor, plan: LoopPlan) -> Tensor:
    return torch.cat(
        [anchor_latent[:, :, plan.opening_start: plan.head_start], bridge_latent[:, :, : plan.tail_start]], dim=2,
    )


def assemble_loop_audio(anchor_audio: Tensor, bridge_audio: Tensor, plan: LoopPlan) -> Tensor:
    return torch.cat(
        [anchor_audio[..., plan.opening_audio_start: plan.head_audio_start], bridge_audio[..., : plan.tail_audio_start]],
        dim=-1,
    )


def cyclic_decode_segments(loop_chunks: int, *, segment_chunks: int = DECODE_SEGMENT_CHUNKS) -> list[tuple[int, int]]:
    if loop_chunks < 1 or segment_chunks < 1:
        raise ValueError(f"need at least one chunk and one chunk per segment, got {loop_chunks}, {segment_chunks}")
    return [(start, min(start + segment_chunks, loop_chunks)) for start in range(0, loop_chunks, segment_chunks)]


def cyclic_segment_latent(loop_latent: Tensor, start: int, end: int) -> Tensor:
    chunks = loop_latent.shape[2] // LATENTS_PER_CHUNK
    if loop_latent.shape[2] != chunks * LATENTS_PER_CHUNK or not 0 <= start < end <= chunks:
        raise ValueError(
            f"a cyclic segment [{start}, {end}) needs a whole-chunk loop latent, got {loop_latent.shape[2]} frames"
        )

    def chunk(index: int) -> Tensor:
        index %= chunks
        return loop_latent[:, :, index * LATENTS_PER_CHUNK: (index + 1) * LATENTS_PER_CHUNK]

    return torch.cat(
        [chunk(start - 1)] + [chunk(index) for index in range(start, end)] + [chunk(end)[:, :, :PARTIAL_LATENTS]],
        dim=2,
    )


def cyclic_segment_frames(start: int, end: int) -> slice:
    return slice(FRAMES_PER_CHUNK, FRAMES_PER_CHUNK * (1 + end - start))


def cyclic_audio_latents(loop_audio: Tensor, *, context: int = AUDIO_CONTEXT_LATENTS) -> tuple[Tensor, int]:
    count = loop_audio.shape[-1]
    context = max(0, min(context, count))
    if not context:
        return loop_audio, 0
    return torch.cat([loop_audio[..., count - context:], loop_audio, loop_audio[..., :context]], dim=-1), context
