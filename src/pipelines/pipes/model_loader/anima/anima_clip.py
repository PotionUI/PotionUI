"""ClipTextEncoder adapter for the native Anima text encoder (Qwen3-0.6B).

Bridges the native ``AnimaTextEncoder`` to the shared ``ClipTextEncoder`` ABC so
the generic ``prompt_encoder`` pipe produces a ``ConditioningModel`` for Anima
exactly as it does for SDXL / Flux / Qwen — no raw-prompt-in-generator bypass.

The Anima encoder emits a FOUR-key conditioning dict: ``{"context": [B,S,1024],
"attention_mask": [B,S], "t5xxl_ids": [1,S_t5], "t5xxl_weights": [1,S_t5]}``. This
adapter packs that into ``ConditioningModel.embeds`` / ``.n_embeds`` unchanged;
the downstream ``generator/anima`` pipe passes those keys into the Anima DiT,
whose in-model LLMAdapter fuses ``context`` + ``t5xxl_ids`` into the actual
cross-attention context.

Anima uses true classifier-free guidance (spec guidance ``"cfg"``), so the
negative prompt IS encoded (the generator runs a cond + uncond pass). Mirrors
``QwenClipTextEncoder``.

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


class AnimaClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt the native Anima (Qwen3-0.6B) encoder to the ``ClipTextEncoder`` ABC.

    ``model_fingerprint`` is surfaced as ``_model_fingerprint`` so the
    ``prompt_encoder`` pipe folds the encoder identity (checkpoint + LoRA set)
    into its conditioning cache key — matching how SDXL/Flux/Qwen tag their clip.
    """

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

        ``num_images_per_prompt`` is ignored (the ``generator/anima`` seed loop
        produces N images from one conditioning); ``embedding_files`` (textual
        inversion) has no meaning for this encoder. The negative is always encoded
        when CFG is requested — Anima's true-CFG sampler needs the uncond pass.
        """
        prompt = request["prompt"]
        negative_prompt = request["negative_prompt"]
        do_classifier_free_guidance = bool(request.get("do_classifier_free_guidance", True))
        embedding_files = request.get("embedding_files")
        if embedding_files:
            logger.debug(
                "AnimaClipTextEncoder: textual-inversion embeddings are unsupported; ignoring %d",
                len(embedding_files),
            )

        # Encode on the GPU (the encoder is loaded on CPU and must be moved, or the
        # Qwen3-0.6B forward runs on the CPU in fp32 — a big chunk of a cold run).
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
