# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/protocol.py (Apache-2.0)

"""YuE2's checkpoint-native prompt contract: special-token ids, sampling
defaults, prompt-id assembly, and the acoustic chunk-range split.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

EOD = 151643
ABC_START, ABC_END = 151847, 151848
MUSIC_START, MUSIC_END = 151851, 151852
CODEC_OFFSET, CODEC_SIZE = 151853, 32768
LATENT_START, LATENT_END, LATENT_PAD = 184621, 184622, 184623
VOCAB_SIZE, CONTEXT = 184704, 24576

INSTRUCTIONS = {
    "off": "Generate music with codec tokens from the given conditions.",
    "melody": "Generate a melody-only ABC transcription without chord symbols, then generate music with codec tokens from the given conditions.",
    "full": "Generate a chord-annotated ABC transcription, then generate music with codec tokens from the given conditions.",
}


@dataclass(frozen=True)
class Sampling:
    """One phase's (abc/semantic) sampling parameters."""

    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 100
    repetition_penalty: float = 1.2
    penalty_window: int = 50
    min_tokens: int = 200
    max_tokens: int = 9000

    def __post_init__(self) -> None:
        if any(type(x) is not int for x in (self.top_k, self.penalty_window, self.min_tokens, self.max_tokens)):
            raise ValueError("Sampling counts must be integers")
        if not all(math.isfinite(x) for x in (self.temperature, self.top_p, self.repetition_penalty)):
            raise ValueError("Sampling numbers must be finite")
        if not 0 <= self.temperature <= 5 or not 0 < self.top_p <= 1 or self.top_k < 1:
            raise ValueError("Invalid sampling temperature/top_p/top_k")
        if self.repetition_penalty <= 0 or not 1 <= self.penalty_window <= 100:
            raise ValueError("Invalid repetition penalty/window")
        if not 0 <= self.min_tokens <= self.max_tokens or self.max_tokens < 1:
            raise ValueError("Require 0 <= min_tokens <= max_tokens")


ABC_SAMPLING_DEFAULTS = Sampling(
    temperature=0.7, top_p=0.9, top_k=30, repetition_penalty=1.005,
    penalty_window=100, min_tokens=32, max_tokens=4096,
)
SEMANTIC_SAMPLING_DEFAULTS = Sampling()


def guidance_scale(cot: str, cfg_scale: float | None = None) -> float:
    """The semantic-stage CFG default: 1.01 for ``cot="off"``, else 1.0 — overridable."""
    if cot not in INSTRUCTIONS:
        raise ValueError("cot must be off, melody or full")
    if cfg_scale is not None:
        return cfg_scale
    return 1.01 if cot == "off" else 1.0


def _validate_abc_ids(abc_ids: Sequence[int]) -> list[int]:
    ids = list(abc_ids)
    if any(type(token) is not int or not 0 <= token < EOD for token in ids):
        raise ValueError("ABC token IDs must stay inside the ordinary text vocabulary")
    return ids


def build_prompt_ids(
    encode: Callable[[str], Sequence[int]],
    instruction: str,
    tags: str,
    lyrics: str,
    abc: str | None = None,
    cot: str = "full",
) -> list[int]:
    """Assemble the positive-branch prompt: EOD, instruction, [Tags]/style,
    [Lyrics]/lyrics, then ABC_START[/abc ids/ABC_END], MUSIC_START.
    """
    if cot not in INSTRUCTIONS:
        raise ValueError("cot must be off, melody or full")
    text = f"{instruction}\n[Tags]\n{tags}\n[Lyrics]\n{lyrics}\n"
    ids = [EOD, *encode(text)]
    if cot == "off":
        return ids + [ABC_START, ABC_END, MUSIC_START]
    if abc is None:
        return ids + [ABC_START]
    return ids + [ABC_START, *_validate_abc_ids(encode(abc)), ABC_END, MUSIC_START]


def build_negative_prompt_ids(
    encode: Callable[[str], Sequence[int]],
    cot: str,
    abc_ids: Sequence[int] | None = None,
) -> list[int]:
    """Assemble the CFG unconditional branch: EOD, bare instruction, then
    ABC_START[/the exact positive-branch abc ids/ABC_END], MUSIC_START.
    """
    if cot not in INSTRUCTIONS:
        raise ValueError("cot must be off, melody or full")
    ids = [EOD, *encode(INSTRUCTIONS[cot])]
    if cot == "off":
        return ids + [MUSIC_START]
    if abc_ids is None:
        raise ValueError("CFG negative prefix requires the exact positive-branch ABC token IDs")
    return ids + [ABC_START, *_validate_abc_ids(abc_ids), ABC_END, MUSIC_START]


def ensure_prompt_fits(prompt_ids: Sequence[int], budget: int, context: int = CONTEXT) -> None:
    """Raise when ``prompt_ids`` plus a ``budget``-token generation would exceed ``context``."""
    total = len(prompt_ids) + budget
    if total > context:
        raise ValueError(
            f"yue2 prompt ({len(prompt_ids)} tokens) + generation budget ({budget}) "
            f"= {total} exceeds the {context}-token context"
        )


def chunk_ranges(frames: int, prefix_tokens: int, context: int = CONTEXT) -> list[tuple[int, int]]:
    """Codec-frame ``[start, end)`` ranges tiling one acoustic-stage window each."""
    size = min((context - prefix_tokens - 3) // 2, CONTEXT)
    if frames < 1 or size < 1:
        raise ValueError("Empty codec or prefix leaves no acoustic context")
    return [(a, min(a + size, frames)) for a in range(0, frames, size)]
