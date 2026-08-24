# Derived from: diffusers `src/diffusers/pipelines/minimax_music3/encoders.py`
# (Apache-2.0, "Copyright 2026 The MiniMax Team and The HuggingFace Team"), the
# per-frame AR sampling recipe (`encoders.py:330-338`'s ordering: vocab mask,
# THEN classifier-free guidance, THEN re-mask, THEN top-k, THEN
# nan_to_num+renormalize) — re-expressed here, not copied.

"""Classifier-free-guided top-k sampling shared by the semantic (LLM) and
residual (depth-decoder) AR stages.

No temperature parameter exists anywhere in this recipe (top-k + CFG only).

The vocab-mask re-application trap: the full (un-pruned) checkpoint's
``lm_head`` scores the whole 200000-wide vocabulary, of which only the
audio-code window plus the stop token are ever legal here. Masking the
conditional and unconditional branches individually with literal ``-inf``
*before* combining them is the natural-looking thing to do, and it is exactly
what breaks CFG: ``guided = uncond + (cond - uncond) * scale`` computes
``-inf - -inf`` at every excluded position, which is NaN, not a large
negative number — and `torch.softmax`'s sum-of-exp then propagates that NaN
into EVERY probability, not just the excluded ones. ``masked_fill`` does not
inspect the value it overwrites, so calling the same mask a second time on
the already-NaN-contaminated ``guided`` tensor cleans it unconditionally back
to ``-inf`` before top-k/softmax ever see it. Both calls are therefore
required: masking only "before" ships NaN silently (see the bite test in
``tests/.../test_minimax_music3_cfg_sampling.py``).
"""

from __future__ import annotations

from collections.abc import Callable

import torch

from .prompt import AUDIO_CODE_OFFSET, AUDIO_END_TOKEN_ID, SEMANTIC_VOCAB_SIZE

MaskFn = Callable[[torch.Tensor], torch.Tensor]


def full_vocab_mask(logits: torch.Tensor) -> torch.Tensor:
    """Restrict ``logits`` (``[..., 200000]``, full/un-pruned lm_head) to the
    audio-code window ``[AUDIO_CODE_OFFSET, AUDIO_CODE_OFFSET+SEMANTIC_VOCAB_SIZE)``
    plus the stop id ``AUDIO_END_TOKEN_ID`` (151670), everything else ``-inf``.

    Idempotent and NaN-tolerant by construction (``masked_fill`` overwrites
    unconditionally) — see the module docstring for why that matters.
    """
    allowed = torch.zeros_like(logits, dtype=torch.bool)
    allowed[..., AUDIO_CODE_OFFSET:AUDIO_CODE_OFFSET + SEMANTIC_VOCAB_SIZE] = True
    allowed[..., AUDIO_END_TOKEN_ID] = True
    return logits.masked_fill(~allowed, float("-inf"))


def guided_top_k_sample(
    cond_logits: torch.Tensor,
    uncond_logits: torch.Tensor,
    cfg_scale: float,
    top_k: int,
    generator: torch.Generator,
    mask_fn: MaskFn | None = None,
) -> int:
    """Sample one token id from a CFG-guided, top-k-restricted distribution.

    Thin ``.item()`` wrapper over :func:`guided_top_k_sample_id` for callers
    that need a plain python int (tests, anything outside the AR hot loop).
    See that function for the full sampling recipe and its docstring.
    """
    return int(guided_top_k_sample_id(cond_logits, uncond_logits, cfg_scale, top_k, generator, mask_fn=mask_fn).item())


def guided_top_k_sample_id(
    cond_logits: torch.Tensor,
    uncond_logits: torch.Tensor,
    cfg_scale: float,
    top_k: int,
    generator: torch.Generator,
    mask_fn: MaskFn | None = None,
) -> torch.Tensor:
    """Sample one token id from a CFG-guided, top-k-restricted distribution,
    returned as a 0-dim tensor rather than a python int.

    ``cond_logits``/``uncond_logits``: 1-D ``[vocab]`` (a single frame's two
    CFG branches -- this loop never batches multiple songs together). Top-k is
    read off the CONDITIONAL branch's own logits (diffusers' convention: the
    unconditional branch never gets to veto a token the caption asked for).

    ``mask_fn``, when given, is applied to ``cond_logits``/``uncond_logits``
    BEFORE combining and to the combined ``guided`` tensor AFTER -- pass
    :func:`full_vocab_mask` for the full-vocab lm_head; ``None`` for the
    pruned lm_head and every depth-decoder head, whose output widths are
    already exactly the legal set (no mask needed).

    No ``.item()``/``.tolist()`` anywhere in this function -- the AR loop
    (:mod:`.ar_loop`, :mod:`.depth_decoder`) calls this 8 times per frame and
    depends on it staying entirely on-device to avoid a GPU->CPU sync on
    every one of those calls; only :func:`guided_top_k_sample` (and whatever
    else needs the plain int) pays that cost, once, on its own call.
    """
    if mask_fn is not None:
        cond_logits = mask_fn(cond_logits)
        uncond_logits = mask_fn(uncond_logits)
    guided = uncond_logits + (cond_logits - uncond_logits) * cfg_scale
    if mask_fn is not None:
        guided = mask_fn(guided)

    k = min(top_k, cond_logits.shape[-1])
    threshold = torch.topk(cond_logits, k, dim=-1).values[..., -1]
    guided = guided.masked_fill(cond_logits < threshold, float("-inf"))

    probs = torch.softmax(guided.float(), dim=-1)
    probs = torch.nan_to_num(probs, nan=0.0, posinf=0.0, neginf=0.0)
    total = probs.sum(dim=-1, keepdim=True).clamp_min(1e-12)
    probs = probs / total
    return torch.multinomial(probs, 1, generator=generator).squeeze(0)
