"""Common interface for native text encoders.

A ``NativeTextEncoder`` turns a list of prompt strings into a dict of role-keyed
conditioning tensors that the native generator's conditioning assembly consumes.

Output dict schema (the role keys — a subset appears per encoder):

    "context"         [B, S, C]  float  cross-attention sequence conditioning
                                        (the DiT ``txt_in`` input).
    "pooled"          [B, D]     float  pooled vector conditioning (the DiT
                                        ``vector_in`` / ``y`` input). Present
                                        only for encoders that produce one
                                        (Flux1's CLIP-L); ABSENT for Klein/Flux2,
                                        whose DiT has no ``vector_in``.
    "attention_mask"  [B, S]     long   1 = real token, 0 = pad. Present when the
                                        encoder attended with a padding mask
                                        (Klein/Qwen3); ABSENT when the encoder
                                        attends over every position unmasked
                                        (Flux1's T5-XXL, matching ComfyUI's
                                        ``t5_attention_mask=False`` default).

Per-encoder concrete schemas:

    Qwen3TextEncoder (Klein / Flux2):
        {"context": [B, S, 3*hidden], "attention_mask": [B, S]}
        3*hidden = 12288 for the 8B (hidden 4096), 7680 for the 4B (hidden 2560).
        Context is the concatenation of the hidden states from layers 9/18/27,
        NOT passed through the final norm (ComfyUI ``layer_norm_hidden_state=False``).
        No pooled — Flux2's DiT has no vector input.

    T5XXLTextEncoder (Flux1):
        {"context": [B, S, 4096]}   (last hidden state, after final layer norm)

    CLIPLTextEncoder (Flux1):
        {"pooled": [B, 768]}        (raw pooled at the EOS token, pre-projection)

    FluxTextEncoder (Flux1 composite = T5-XXL + CLIP-L):
        {"context": [B, S, 4096], "pooled": [B, 768]}
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

# Roles already warned about not supporting prompt weighting - a preset that
# uses weight syntax with an unsupported encoder would otherwise re-log the
# same warning on every single prompt encode.
_warned_unweighted_roles: set[str] = set()


class NativeTextEncoder(ABC):
    """Base class for a single-role or composite text encoder.

    Concrete encoders own an ``nn.Module`` (the vendored arch) plus a tokenizer.
    ``encode`` must run under ``torch.inference_mode`` and return CPU-or-device
    tensors keyed by the roles documented in this module.
    """

    #: Human-readable role, e.g. ``"qwen3_8b"`` / ``"t5xxl"`` / ``"clip_l"``.
    role: str = "text_encoder"

    @abstractmethod
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        """Encode a batch of prompts into role-keyed conditioning tensors."""
        raise NotImplementedError

    # -- A1111 prompt weighting (template method) --------------------------

    def _tokenize_weighted(self, prompt: str):
        """Hook: return ``(ids, mask, weights)`` with ``weights`` aligned 1:1 to the
        sequence axis of :meth:`_encode_ids`'s ``"context"`` output (i.e. already
        template-prefix-stripped where the encoder strips). Return ``None`` if the
        encoder does not support weighting."""
        return None

    def _encode_ids(self, ids, mask) -> dict[str, "torch.Tensor"]:
        """Hook: encode pre-tokenized ids -> the same role dict as :meth:`encode`.
        Return ``None`` if the encoder does not support weighting."""
        return None

    @torch.inference_mode()
    def encode_weighted(self, prompt: str) -> dict[str, torch.Tensor]:
        """Encode one prompt with A1111 ``(word:1.3)`` / ``[word]`` weighting.

        No-op fast path: a prompt with no weight syntax goes straight through
        :meth:`encode` (bit-identical). Weighted path (ComfyUI method): encode the
        real prompt + an empty-prompt baseline of the same length, then scale each
        token's ``"context"`` embedding toward the baseline by its weight. Encoders
        without the ``_tokenize_weighted``/``_encode_ids`` hooks fall back to an
        unweighted encode with a warning (weights ignored, never mis-applied).
        """
        from .prompt_weights import apply_token_weights, has_weights, parse_a1111, strip_syntax

        segments = parse_a1111(prompt)
        if not has_weights(segments):
            return self.encode([prompt])

        weighted = self._tokenize_weighted(prompt)
        real = self._encode_ids(*weighted[:2]) if weighted is not None else None
        if weighted is None or real is None:
            if self.role not in _warned_unweighted_roles:
                _warned_unweighted_roles.add(self.role)
                logger.warning("prompt weighting unsupported for text encoder %r; ignoring weights", self.role)
            return self.encode([strip_syntax(segments)])

        ids, _mask, weights = weighted
        raw_len = ids.shape[1]  # align on the RAW token length so both strip equally

        e_ids, e_mask, _ew = self._tokenize_weighted("")
        pad_token = int(e_ids.flatten()[-1])
        e_ids, e_mask = _align_len(e_ids, e_mask, raw_len, pad_token)
        baseline = self._encode_ids(e_ids, e_mask)

        seq = real["context"].shape[1]  # post-strip sequence length (weights already stripped to match)
        real["context"] = apply_token_weights(real["context"], weights[:, :seq], baseline["context"])
        return self._post_encode(real)

    def _post_encode(self, result: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        """Final hook applied to every encode result (plain and weighted).

        Runs AFTER the weighted subtraction, so an override may change the
        sequence length (e.g. Krea-2 trims the min-512 padding tail) without
        desyncing the real/baseline length alignment above.
        """
        return result

    def to(self, device: str | torch.device) -> "NativeTextEncoder":
        """Move the underlying module(s) to ``device``. Default: no-op."""
        return self

    def unload(self) -> None:
        """Release GPU/CPU memory held by the underlying module(s)."""
        return None


def _align_len(ids: torch.Tensor, mask: torch.Tensor, seq: int, pad_token: int):
    """Right-pad (with ``pad_token``, mask 0) or truncate ``ids``/``mask`` to ``seq``.

    Used to make the empty-prompt baseline the same sequence length as the real
    prompt so :func:`apply_token_weights` can subtract per-position.
    """
    cur = ids.shape[1]
    if cur == seq:
        return ids, mask
    if cur > seq:
        return ids[:, :seq], mask[:, :seq]
    pad_ids = torch.full((ids.shape[0], seq - cur), pad_token, dtype=ids.dtype, device=ids.device)
    pad_mask = torch.zeros((mask.shape[0], seq - cur), dtype=mask.dtype, device=mask.device)
    return torch.cat([ids, pad_ids], dim=1), torch.cat([mask, pad_mask], dim=1)


def _module_to(module: nn.Module, device: str | torch.device) -> None:
    module.to(device)


def _module_unload(module: nn.Module | None) -> None:
    if module is None:
        return
    try:
        module.to("cpu")
    except Exception:  # pragma: no cover - best-effort eviction
        logger.debug("text-encoder module eviction to cpu failed", exc_info=True)
