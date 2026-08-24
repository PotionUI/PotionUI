"""ClipTextEncoder adapter for the native Wan UMT5-XXL text encoder.

Bridges the native ``UMT5TextEncoder`` to the shared ``ClipTextEncoder`` ABC so
the generic ``prompt_encoder`` pipe produces a ``ConditioningModel`` for Wan.
UMT5 emits ``{"context": [B,S,4096], "attention_mask": [B,S]}``; this adapter
packs that into ``ConditioningModel.embeds`` / ``.n_embeds`` unchanged, and the
``generator/txt2vid/wan22`` pipe maps those role keys onto the Wan DiT's
``context`` cross-attention input.

Wan uses true classifier-free guidance, so the negative prompt IS encoded (the
generator runs a cond + uncond pass) — unlike the embedded-guidance Flux path.

``encode_prompts``: inherited from ``SequentialWindowClipTextEncoder``
— every request that misses the prompt-embed cache is encoded under ONE shared
GPU-resident window instead of one window per request. See that class's
docstring for the full rationale.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.platform.runtime.primitives.clip import ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder, prompt_embed_key

logger = logging.getLogger(__name__)


class WanClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt the native UMT5-XXL encoder to the ``ClipTextEncoder`` ABC."""

    def __init__(
        self,
        encoder: NativeTextEncoder,
        *,
        device: str = "cuda",
        model_fingerprint: Optional[str] = None,
    ) -> None:
        self.encoder = encoder
        self.device = device
        self._model_fingerprint = model_fingerprint

    def _encode_fn_and_key(self, request: Dict[str, Any]) -> Tuple[Callable[[], Any], Optional[str]]:
        """Build the encode closure + cache key for one request.

        ``num_images_per_prompt`` is ignored (the generator seed loop produces the
        batch). ``embedding_files`` (textual inversion) has no meaning for UMT5.
        The negative is always encoded when CFG is requested — Wan's true-CFG
        sampler needs the uncond pass.
        """
        prompt = request["prompt"]
        negative_prompt = request["negative_prompt"]
        do_classifier_free_guidance = bool(request.get("do_classifier_free_guidance", True))
        if request.get("embedding_files"):
            logger.debug("WanClipTextEncoder: textual-inversion embeddings ignored (UMT5)")

        # Encode on the GPU (the encoder is loaded on CPU and must be moved, or the
        # UMT5-XXL forward runs on the CPU in fp32 — a big chunk of a cold run).
        def _encode():
            pos = self.encoder.encode_weighted(prompt)
            if do_classifier_free_guidance:
                neg = self.encoder.encode_weighted(negative_prompt)
            else:
                neg: Dict[str, Any] = {}
            return pos, neg

        cache_key = prompt_embed_key(
            self._model_fingerprint, getattr(self.encoder, "role", None),
            prompt, negative_prompt, do_classifier_free_guidance,
        )
        return _encode, cache_key

    def _pack(self, request: Dict[str, Any], result: Any) -> ConditioningModel:
        embeds, n_embeds = result
        return ConditioningModel(
            p_prompt=request["prompt"],
            n_prompt=request["negative_prompt"],
            embeds=embeds,
            n_embeds=n_embeds,
        )
