"""Anima text encoder (Qwen3-0.6B language model + T5 token ids).

Anima's conditioning is a two-part payload, unlike every other native family:

  * ``context`` — the Qwen3-0.6B **last hidden state**, passed through the model's
    final RMS norm (ComfyUI `SDClipModel` ``layer="last"`` returns the normed
    output; ``layer_norm_hidden_state=False`` only affects the intermediate path).
    This is the LLMAdapter's cross-attention *source*.
  * ``t5xxl_ids`` / ``t5xxl_weights`` — the T5 tokenization of the same prompt.
    The DiT's in-model ``LLMAdapter`` embeds these ids (its own ``Embedding``) as
    a target sequence and cross-attends them to ``context`` to build the actual
    DiT cross-attention context — so the fusion runs inside the DiT, and the
    generator only has to pass these three tensors through the conditioning dict.

There is no T5 *model* here (the adapter owns the T5-vocab embedding), so no extra
checkpoint download beyond the Qwen3-0.6B encoder. This module reuses the generic
:class:`~src.platform.runtime.native.text_encoders.qwen3.Qwen3Model` arch (it is an ordinary
Qwen3, only the extraction contract differs).
"""

from __future__ import annotations

import logging

import torch

from .base import NativeTextEncoder, _module_to, _module_unload
from .prompt_weights import parse_a1111, strip_syntax
from .qwen3 import Qwen3Model

logger = logging.getLogger(__name__)


class AnimaTextEncoder(NativeTextEncoder):
    """Qwen3-0.6B encoder producing Anima's ``{context, t5xxl_ids, t5xxl_weights}``.

    ``encode`` returns::

        {"context": [B, S_qwen, 1024], "attention_mask": [B, S_qwen],
         "t5xxl_ids": [1, S_t5] long, "t5xxl_weights": [1, S_t5] float}

    The Qwen side uses the A1111-stripped prompt (weights are applied on the T5
    side, mirroring ComfyUI which forces the Qwen weights to 1.0). Batch is
    normally 1 — the generator encodes one prompt per seed.
    """

    role = "qwen3_06b"

    def __init__(self, module: Qwen3Model, qwen_tokenizer, t5_tokenizer,
                 variant: str = "qwen3_06b", device: str | torch.device = "cpu") -> None:
        self.module = module
        self.qwen_tok = qwen_tokenizer
        self.t5_tok = t5_tokenizer
        self.role = variant
        self._device = torch.device(device)
        # Anima takes the LAST decoder layer's output (no final norm).
        self._last_layer = module.cfg.num_hidden_layers - 1

    def to(self, device: str | torch.device) -> "AnimaTextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        stripped = [strip_syntax(parse_a1111(t)) for t in texts]
        ids, mask = self.qwen_tok(stripped, device=self._device)
        # ComfyUI's Anima TE uses SDClipModel layer="last", which returns the
        # model's `x` output — i.e. the last decoder layer's hidden state passed
        # THROUGH the model's final RMS norm (Qwen3 `final_norm=True`).
        # (`layer_norm_hidden_state=False` only suppresses the extra CLIP-style
        # norm on the *intermediate* path, not this one.) So capture the last
        # layer's output and apply the final norm.
        stacked = self.module(ids, attention_mask=mask, layers_to_extract=[self._last_layer], capture="output")
        context = self.module.model.norm(stacked[:, 0])

        t5_ids, _t5_mask, t5_weights = self.t5_tok(texts[0], device=self._device)
        return {
            "context": context,
            "attention_mask": mask,
            "t5xxl_ids": t5_ids,
            "t5xxl_weights": t5_weights,
        }

    @torch.inference_mode()
    def encode_weighted(self, prompt: str) -> dict[str, torch.Tensor]:
        """A1111 weights are carried on the T5 side inside :meth:`encode`, so the
        weighted path is just a plain encode of the single prompt (no context-vs-
        baseline scaling like the other encoders)."""
        return self.encode([prompt])
