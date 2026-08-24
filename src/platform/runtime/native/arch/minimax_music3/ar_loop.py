# Derived from: diffusers `src/diffusers/pipelines/minimax_music3/
# modular_pipeline.py` / `encoders.py` (Apache-2.0, "Copyright 2026 The
# MiniMax Team and The HuggingFace Team") for the per-frame generation
# recipe (KV-cached LLM step -> lm_head -> CFG+top-k -> depth decoder ->
# feedback embedding, frame 0 discarded) — re-expressed here, not copied.

"""The per-frame AR generation loop: samples the semantic code, the 7
residual codes, and folds them back into the global LLM's own input sequence
— one frame at a time, 25 fps, until ``max_frames`` or a stop token.

Position budget: the KV cache is preallocated at ``prompt_tokens +
max_frames + 1`` (the ``+1`` is frame 0's priming step, discarded from the
output but still a real forward that consumes one cache slot — see
:func:`generate`). RoPE never errors past ``max_position_embeddings``; it
silently extrapolates, which is a quality cliff on the tail of a long song,
not a crash — :func:`position_budget_warning` is the guard that turns that
silent failure mode into a diagnosable one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import torch

from ...errors import SamplingCancelled
from ._ar_timing import ArTiming
from ._nn import module_device
from .cfg_sampling import full_vocab_mask, guided_top_k_sample_id
from .depth_decoder import generate_depth_codes
from .lm import MiniMaxMusic3AudioLM
from .prompt import AUDIO_CODE_OFFSET, AUDIO_END_TOKEN_ID, MAX_AUDIO_FRAMES, MAX_PROMPT_TOKENS

logger = logging.getLogger(__name__)

FRAME_HIDDEN_SIZE = 32_768  # 8 * hidden_size (1 LLM + 7 depth-decoder slots).


def position_budget_warning(prompt_tokens: int, max_frames: int, max_position_embeddings: int) -> str | None:
    """``None`` when the run fits inside the checkpoint's trained position
    range; otherwise a message naming the overflow, safe to log or surface to
    a progress emitter as-is. Pure — no module/tensor dependency, so both S2
    and S5 (which surfaces it through ``progress.state(...)``) can call it.
    """
    total = prompt_tokens + max_frames + 1
    if total <= max_position_embeddings:
        return None
    return (
        f"MiniMax-Music3: prompt_tokens ({prompt_tokens}) + max_frames ({max_frames}) + 1 "
        f"= {total} exceeds this checkpoint's {max_position_embeddings}-position training "
        f"range; RoPE will extrapolate past it, which can degrade quality in the later "
        f"part of the song rather than fail outright."
    )


def _sample_semantic(
    lm: MiniMaxMusic3AudioLM, hidden: torch.Tensor, cfg_scale: float, top_k: int, generator: torch.Generator,
) -> tuple[torch.Tensor | None, bool]:
    """Returns ``(code0, stopped)``. ``hidden``: ``[2, hidden]`` (both CFG rows).

    ``code0`` (when not ``None``) is a 0-dim tensor, kept on-device — see
    :mod:`.cfg_sampling`'s ``guided_top_k_sample_id``. The ONE GPU->CPU sync
    per AR frame lives here: whether to stop is control flow (:func:`generate`
    breaks the loop on it), so it has to be known on the host before the
    depth decoder runs — the ``.item()`` below reads a single 0-dim bool,
    not the sampled id itself, which stays a tensor all the way to
    :func:`_feedback_embedding`.
    """
    logits = lm.lm_head_logits(hidden)  # [2, vocab]
    mask_fn = None if lm.cfg.pruned_lm_head else full_vocab_mask
    sampled = guided_top_k_sample_id(logits[0], logits[1], cfg_scale, top_k, generator, mask_fn=mask_fn)
    if lm.cfg.pruned_lm_head:
        stop = sampled == 0
        code0 = sampled - 1
    else:
        stop = sampled == AUDIO_END_TOKEN_ID
        code0 = sampled - AUDIO_CODE_OFFSET
    if bool(stop.item()):  # the one sync
        return None, True
    return code0, False


def _feedback_embedding(lm: MiniMaxMusic3AudioLM, code0: torch.Tensor, codes: torch.Tensor) -> torch.Tensor:
    """``(embed(c0) + sum_i audio_extra_embedding(c_i + (i-1)*audio_vocab_size)) * 8**-0.5``,
    ``[1, hidden]`` — the same vector for both CFG rows (the sampled codes are
    shared; only the two rows' KV histories differ).

    ``code0``: 0-dim tensor. ``codes``: ``[7]`` long tensor (``c1..c7``, see
    :func:`.depth_decoder.generate_depth_codes`). Both stay on-device end to
    end — no ``torch.tensor([python_int], ...)`` round trip, no sync.
    """
    device = module_device(lm)
    total = lm.embed_audio_code0(code0.reshape(1).to(device)).squeeze(0)
    for i in range(codes.shape[0]):
        idx = codes[i] + i * lm.cfg.audio_vocab_size
        total = total + lm.model.audio_extra_embedding(idx.reshape(1).to(device)).squeeze(0).to(total.dtype)
    return (total * (8.0 ** -0.5)).unsqueeze(0)  # [1, hidden]


@torch.inference_mode()
def generate(
    lm: MiniMaxMusic3AudioLM,
    input_ids: torch.Tensor,
    generator: torch.Generator,
    max_frames: int,
    cfg_scale: float = 1.5,
    top_k: int = 50,
    on_frame: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> torch.Tensor:
    """Run the AR core end-to-end. ``input_ids``: ``[2, L]`` (conditional +
    unconditional rows — see ``prompt.MiniMaxMusic3Tokenizer.build_conditional_pair``).

    Returns ``frame_hiddens``, ``[1, F, FRAME_HIDDEN_SIZE]`` on CPU, ``F <=
    max_frames`` (shorter when a stop token fires early). ``on_frame(i, max)``
    is called once per KEPT frame (frame 0, which only advances the sequence
    past ``<|audio_start|>``, is not counted). ``is_cancelled()``, when given,
    is polled once per AR step (including frame 0) and raises
    :class:`SamplingCancelled`.
    """
    if max_frames <= 0:
        raise ValueError(f"max_frames must be positive, got {max_frames}")
    if max_frames > MAX_AUDIO_FRAMES:
        raise ValueError(f"max_frames ({max_frames}) exceeds the maximum of {MAX_AUDIO_FRAMES}")
    if input_ids.shape[0] != 2:
        raise ValueError(f"input_ids must carry the [conditional, unconditional] pair, got batch {input_ids.shape[0]}")
    prompt_tokens = input_ids.shape[1]
    if prompt_tokens > MAX_PROMPT_TOKENS:
        raise ValueError(f"prompt has {prompt_tokens} tokens; the maximum is {MAX_PROMPT_TOKENS}")

    warning = position_budget_warning(prompt_tokens, max_frames, lm.cfg.max_position_embeddings)
    if warning:
        logger.warning(warning)

    device = module_device(lm)
    timing = ArTiming(device)
    cache = lm.new_kv_cache(max_len=prompt_tokens + max_frames + 1, device=device)
    with timing.track("prefill"):
        hidden_all = lm.prefill(input_ids.to(device), cache)  # [2, L, hidden]
    llm_hidden = hidden_all[:, -1, :]  # [2, hidden] -- seeds frame 0

    frame_hiddens: list[torch.Tensor] = []
    for frame_idx in range(max_frames + 1):  # +1: frame 0 is discarded, never counted against max_frames
        if is_cancelled is not None and is_cancelled():
            raise SamplingCancelled(frame_idx)

        with timing.track("sampling_feedback"):
            code0, stopped = _sample_semantic(lm, llm_hidden, cfg_scale, top_k, generator)
        if stopped:
            break
        with timing.track("depth"):
            codes, depth_hidden = generate_depth_codes(lm, llm_hidden.unsqueeze(1), code0, generator, cfg_scale, top_k)

        with timing.track("sampling_feedback"):
            if frame_idx > 0:
                frame_hidden = torch.cat([llm_hidden[0:1], depth_hidden], dim=-1)  # [1, FRAME_HIDDEN_SIZE]
                frame_hiddens.append(frame_hidden)
                if on_frame is not None:
                    on_frame(frame_idx, max_frames)

            feedback = _feedback_embedding(lm, code0, codes).to(device)  # [1, hidden]
        with timing.track("lm_step"):
            llm_hidden = lm.step(feedback.unsqueeze(0).expand(2, 1, -1), cache).squeeze(1)  # [2, hidden]

    timing.emit(len(frame_hiddens))
    if not frame_hiddens:
        return torch.zeros(1, 0, 8 * lm.cfg.hidden_size)
    return torch.cat(frame_hiddens, dim=0).unsqueeze(0).cpu()  # [1, F, FRAME_HIDDEN_SIZE]
