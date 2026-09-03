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

Deferred TE acquisition: ``model_loader/wan22/main.py`` hands this adapter a
``te_loader`` thunk instead of an already-resolved module (mirroring
``Krea2ClipTextEncoder``) -- the UMT5-XXL checkpoint is only loaded/acquired
the first time ``self.encoder`` is actually read, i.e. the first request in a
batch that misses the prompt-embed cache. A batch served entirely from that
cache never touches ``MODELS.acquire()`` for the TE.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.platform.runtime.primitives.clip import ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder, prompt_embed_key

logger = logging.getLogger(__name__)


class WanClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt the native UMT5-XXL encoder to the ``ClipTextEncoder`` ABC.

    Pass ``encoder=None`` together with ``te_loader`` to defer the underlying
    ``MODELS.acquire()`` -- see the module docstring's "Deferred TE
    acquisition".
    """

    def __init__(
        self,
        encoder: Optional[NativeTextEncoder] = None,
        *,
        device: str = "cuda",
        model_fingerprint: Optional[str] = None,
        te_loader: Optional[Callable[[], NativeTextEncoder]] = None,
    ) -> None:
        self._encoder = encoder
        self._te_loader = te_loader
        self.device = device
        self._model_fingerprint = model_fingerprint

    @property
    def encoder(self) -> NativeTextEncoder:
        if self._encoder is None and self._te_loader is not None:
            self._encoder = self._te_loader()
            self._te_loader = None
        return self._encoder

    @encoder.setter
    def encoder(self, value: NativeTextEncoder) -> None:
        self._encoder = value

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

        # A static tag, not `self.encoder.role`: reading the live encoder here
        # would force the deferred `te_loader` to resolve on EVERY request,
        # hit or miss. `self._model_fingerprint` already encodes the checkpoint
        # identity that determines the detected variant.
        cache_key = prompt_embed_key(
            self._model_fingerprint, "wan_te",
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
