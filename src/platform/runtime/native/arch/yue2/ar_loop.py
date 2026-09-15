# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/sampling.py (Apache-2.0)

"""The per-token AR generation loop shared by the optional ABC-score stage
and the 25 Hz semantic (codec) stage: prefill, then one incrementally
KV-cached step per token, CFG-blended when a negative prefix is given.
"""

from __future__ import annotations

from collections.abc import Callable

import torch

from ...errors import SamplingCancelled
from .model import YuE2Model
from .protocol import Sampling, ensure_prompt_fits
from .sampling import END_TOKEN, guided_scores, sample_id


def generate(
    model: YuE2Model,
    prefix_ids: list[int],
    sampling: Sampling,
    seed: int,
    phase: str,
    negative_ids: list[int] | None = None,
    cfg_scale: float = 1.0,
    legacy_off: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
    on_frame: Callable[[int, int], None] | None = None,
) -> tuple[list[int], bool]:
    """Generate up to ``sampling.max_tokens`` ids, stopping at the phase's end
    token. Returns ``(token_ids, stopped)`` — ``token_ids`` excludes the end
    token; ``stopped`` is ``False`` when generation was truncated by the
    token budget instead of reaching the end token.
    """
    if phase not in END_TOKEN:
        raise ValueError(f"phase must be 'abc' or 'semantic', got {phase!r}")
    if cfg_scale != 1.0 and negative_ids is None:
        raise ValueError("CFG requires a negative prefix")
    ensure_prompt_fits(prefix_ids, sampling.max_tokens, model.cfg.max_position_embeddings)
    if negative_ids is not None:
        ensure_prompt_fits(negative_ids, sampling.max_tokens, model.cfg.max_position_embeddings)
    if is_cancelled is not None and is_cancelled():
        raise SamplingCancelled(0)

    device = next(model.parameters()).device
    dtype = next(model.parameters()).dtype
    rng_device = device if device.type in ("cpu", "cuda") else torch.device("cpu")
    generator = torch.Generator(device=rng_device).manual_seed(seed)

    positive_cache = model.new_kv_cache(
        max_len=len(prefix_ids) + sampling.max_tokens, batch=1, device=device, dtype=dtype,
    )
    prefix_tensor = torch.tensor([prefix_ids], device=device, dtype=torch.long)
    conditional = model.lm_head_logits(model.prefill(prefix_tensor, positive_cache)[:, -1, :])

    negative_cache = None
    unconditional = None
    if cfg_scale != 1.0:
        negative_cache = model.new_kv_cache(
            max_len=len(negative_ids) + sampling.max_tokens, batch=1, device=device, dtype=dtype,
        )
        negative_tensor = torch.tensor([negative_ids], device=device, dtype=torch.long)
        unconditional = model.lm_head_logits(model.prefill(negative_tensor, negative_cache)[:, -1, :])

    end_token = END_TOKEN[phase]
    history: list[int] = []
    stopped = False
    for step in range(sampling.max_tokens):
        if is_cancelled is not None and is_cancelled():
            raise SamplingCancelled(step)
        scores = guided_scores(conditional, unconditional, cfg_scale, sampling, history, step, phase, legacy_off)
        next_id = sample_id(scores, sampling, generator)
        token = int(next_id.item())
        if on_frame is not None:
            on_frame(step, sampling.max_tokens)
        if token == end_token:
            stopped = True
            break
        history.append(token)
        if step + 1 < sampling.max_tokens:
            next_id = next_id.to(device=device, dtype=torch.long)
            conditional = model.lm_head_logits(model.step(next_id, positive_cache)[:, -1, :])
            if negative_cache is not None:
                unconditional = model.lm_head_logits(model.step(next_id, negative_cache)[:, -1, :])

    return history, stopped


__all__ = ["generate"]
