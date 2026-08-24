"""ClipTextEncoder adapter for the native Flux-family text encoders.

Bridges a native ``NativeTextEncoder`` (Flux1 T5-XXL+CLIP-L, or Klein/Flux2
Qwen3) to the shared ``ClipTextEncoder`` ABC so the generic ``prompt_encoder``
pipe produces a ``ConditioningModel`` for Flux exactly as it does for SDXL — no
raw-prompt-in-generator-config bypass.

The native encoder already emits role-keyed conditioning dicts
(``{"context": ..., "pooled"?: ..., "attention_mask"?: ...}``); this adapter
just runs it once for the positive prompt and once for the negative and packs
the two dicts into ``ConditioningModel.embeds`` / ``.n_embeds`` unchanged. The
downstream ``generator/flux`` pipe maps those role keys onto the DiT's
``context``/``y``/``guidance`` inputs.

v1 is a plain encode: unlike ``SDXLClipTextEncoder`` it does NOT parse
A1111-style ``(word:1.3)`` attention weighting or per-chunk 77-token grouping.
Prompt-weighting for the Flux text encoders is a deliberate follow-up (it needs
per-encoder token/weight handling for T5 vs Qwen3); until then weights in the
prompt text are passed through verbatim to the tokenizer.

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
from src.platform.runtime.native.text_encoders import prompt_embed_key
from src.platform.runtime.native.text_encoders import NativeTextEncoder

logger = logging.getLogger(__name__)


class FluxClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt a native Flux-family text encoder to the ``ClipTextEncoder`` ABC.

    ``model_fingerprint`` is surfaced as ``_model_fingerprint`` so the
    ``prompt_encoder`` pipe folds the encoder identity (checkpoint + LoRA set)
    into its conditioning cache key — matching how SDXL's loader tags its clip.
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

        ``num_images_per_prompt`` is intentionally ignored: the ``generator/flux``
        seed loop produces N images from a single conditioning (unlike SDXL's
        embed tiling). ``embedding_files`` (textual inversion) has no meaning for
        the T5/Qwen3 encoders and is ignored with a debug note.
        """
        prompt = request["prompt"]
        negative_prompt = request["negative_prompt"]
        do_classifier_free_guidance = bool(request.get("do_classifier_free_guidance", True))
        embedding_files = request.get("embedding_files")
        if embedding_files:
            logger.debug(
                "FluxClipTextEncoder: textual-inversion embeddings are unsupported "
                "for the Flux text encoders; ignoring %d entr(y/ies)",
                len(embedding_files),
            )

        # Encode on the GPU (the encoder is loaded on CPU and must be moved, or the
        # T5-XXL / Qwen3 forward runs on the CPU in fp32 — a big chunk of a cold run).
        def _encode():
            pos = self.encoder.encode_weighted(prompt)
            # Embedded-guidance Flux never runs an uncond forward, and Flux prompts
            # usually leave the negative empty — skip the (expensive) negative pass
            # unless CFG is actually requested with real text.
            if do_classifier_free_guidance and negative_prompt.strip():
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
