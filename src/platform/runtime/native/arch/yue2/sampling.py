# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/sampling.py (Apache-2.0)

"""CFG-blended, phase-restricted top-k/top-p/temperature sampling for the
YuE2 semantic (and optional ABC-score) AR loop, plus its repetition penalty.
"""

from __future__ import annotations

import torch

from .protocol import ABC_END, CODEC_OFFSET, CODEC_SIZE, EOD, MUSIC_END, Sampling

END_TOKEN = {"abc": ABC_END, "semantic": MUSIC_END}


def repetition_penalty(scores: torch.Tensor, recent_ids: list[int], penalty: float) -> torch.Tensor:
    """Scale down (positive scores) / up (negative scores) recently emitted ids by ``penalty**count``."""
    if penalty == 1.0 or not recent_ids:
        return scores
    recent = torch.as_tensor(recent_ids, dtype=torch.long, device=scores.device).reshape(1, -1)
    freq = torch.zeros_like(scores)
    freq.scatter_add_(-1, recent, torch.ones_like(recent, dtype=scores.dtype))
    alpha = penalty ** freq
    return torch.where(scores < 0, scores * alpha, scores / alpha)


def _phase_mask(scores: torch.Tensor, phase: str) -> torch.Tensor:
    if phase not in END_TOKEN:
        raise ValueError(f"phase must be 'abc' or 'semantic', got {phase!r}")
    allowed = torch.full_like(scores, float("-inf"))
    if phase == "abc":
        allowed[..., :EOD] = 0
    else:
        allowed[..., CODEC_OFFSET:CODEC_OFFSET + CODEC_SIZE] = 0
    allowed[..., END_TOKEN[phase]] = 0
    return scores + allowed


def guided_scores(
    conditional_logits: torch.Tensor,
    unconditional_logits: torch.Tensor | None,
    cfg_scale: float,
    sampling: Sampling,
    history: list[int],
    step: int,
    phase: str,
    legacy_off: bool = False,
) -> torch.Tensor:
    """CFG-blend, then phase-vocab mask, min-tokens gate, repetition penalty, temperature, top-k, top-p."""
    if cfg_scale != 1.0 and unconditional_logits is None:
        raise ValueError("CFG requires the unconditional branch logits")
    logits = conditional_logits if cfg_scale == 1.0 else (
        unconditional_logits + cfg_scale * (conditional_logits - unconditional_logits)
    )
    scores = logits.clone() if legacy_off else logits.float().clone()
    scores = _phase_mask(scores, phase)
    if step < sampling.min_tokens:
        scores[..., END_TOKEN[phase]] = float("-inf")
    scores = repetition_penalty(scores, history[-sampling.penalty_window:], sampling.repetition_penalty)
    if sampling.temperature == 0:
        return scores
    if sampling.temperature != 1:
        scores = scores / sampling.temperature
    k = min(sampling.top_k, scores.shape[-1])
    threshold = scores.topk(k, dim=-1).values[..., -1, None]
    scores = scores.masked_fill(scores < threshold, float("-inf"))
    if sampling.top_p < 1:
        values, indices = scores.sort(descending=True)
        probabilities = values.softmax(-1)
        removed = probabilities.cumsum(-1) - probabilities > sampling.top_p
        removed[..., :3 if legacy_off else 1] = False
        values = values.masked_fill(removed, float("-inf"))
        scores = values.scatter(-1, indices, values)
    return scores


def sample_id(scores: torch.Tensor, sampling: Sampling, generator: torch.Generator) -> torch.Tensor:
    """Draw one token id, shaped ``[1, 1]``, from already-:func:`guided_scores`'d ``scores``."""
    if sampling.temperature == 0:
        return scores.argmax(-1, keepdim=True)
    probabilities = scores.softmax(-1)
    return torch.multinomial(probabilities, 1, generator=generator)


__all__ = ["END_TOKEN", "repetition_penalty", "guided_scores", "sample_id"]
