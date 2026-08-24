"""Shared ``encode_prompts`` for the six run_text_encode-based CLIP adapters.

Flux/Wan/Krea2/Qwen/Anima/Z-Image (``model_loader/*/*_clip.py``) each move a
multi-billion-parameter text encoder CPU<->GPU once per ``encode_prompt``
call, via ``run_text_encode``. ``ClipTextEncoder.encode_prompts``' base
implementation (see ``platform/runtime/primitives/clip.py``) calls
``encode_prompt`` once per request, so an N-image storyboard paid for N full
residency cycles of that encoder before this — exactly the cost LTX's own
``encode_prompts`` override (``ltx_clip.py``) already avoids for its ~20GB
Gemma3-12B.

This is the LOW-RISK fix for the other six families: still one
``encoder.encode_weighted``/``encode`` call per request (no padding/attention-
mask batched forward — that's a separate, higher-risk follow-up), but every
request that MISSES the prompt-embed cache now shares ONE GPU-resident window
(``run_text_encode_batch``) instead of one window each. A request that HITS
the cache never touches the encoder or the GPU, exactly as it wouldn't have
before this change.

A family adapter subclasses :class:`SequentialWindowClipTextEncoder` and
implements two hooks — the same two pieces of logic that used to live inline
in its own ``encode_prompt``:

  - ``_encode_fn_and_key(request)``: build the zero-arg encode closure and the
    cache key for one request.
  - ``_pack(request, result)``: wrap one encode result into a
    ``ConditioningModel``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch

from src.platform.runtime.native.memory import run_text_encode_batch
from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel


class SequentialWindowClipTextEncoder(ClipTextEncoder):
    """``ClipTextEncoder`` whose ``encode_prompts`` runs every miss under ONE
    shared GPU-resident window instead of one window per request.

    Subclasses must set ``self.encoder``/``self.device`` (as every existing
    family adapter already does) and implement ``_encode_fn_and_key``/``_pack``.
    """

    encoder: Any
    device: str

    @abstractmethod
    def _encode_fn_and_key(self, request: Dict[str, Any]) -> Tuple[Callable[[], Any], Optional[str]]:
        """Build ``(encode_fn, cache_key)`` for one request — exactly the pair
        that used to be constructed inline inside ``encode_prompt`` and handed
        straight to ``run_text_encode``."""
        raise NotImplementedError

    @abstractmethod
    def _pack(self, request: Dict[str, Any], result: Any) -> ConditioningModel:
        """Wrap one encode result (freshly encoded, or an embed-cache hit)
        into a ``ConditioningModel``."""
        raise NotImplementedError

    @torch.inference_mode()
    def encode_prompt(
        self,
        prompt: str,
        negative_prompt: str,
        num_images_per_prompt: int = 1,
        do_classifier_free_guidance: bool = True,
        embedding_files: Optional[Dict[str, str]] = None,
        **extra: Any,
    ) -> ConditioningModel:
        """Thin single-request wrapper over :meth:`encode_prompts` — kept so
        callers that don't batch still work (mirrors ``LTXClipTextEncoder``).
        ``**extra`` forwards anything a specific family needs (e.g. Qwen's
        ``images``) straight into the request dict its own
        ``_encode_fn_and_key`` reads back out."""
        request: Dict[str, Any] = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_images_per_prompt=num_images_per_prompt,
            do_classifier_free_guidance=do_classifier_free_guidance,
            embedding_files=embedding_files,
        )
        request.update(extra)
        return self.encode_prompts([request])[0]

    @torch.inference_mode()
    def encode_prompts(self, requests: List[Dict[str, Any]]) -> List[ConditioningModel]:
        """Build every request's encode closure + cache key up front (each
        family's ``_encode_fn_and_key``), then run the whole batch through
        ``run_text_encode_batch`` — cache hits never invoke their closure or
        touch the GPU; misses share ONE window (see the module docstring)."""
        encode_fns: List[Callable[[], Any]] = []
        cache_keys: List[Optional[str]] = []
        for request in requests:
            encode_fn, cache_key = self._encode_fn_and_key(request)
            encode_fns.append(encode_fn)
            cache_keys.append(cache_key)

        results = run_text_encode_batch(self.encoder, self.device, encode_fns, cache_keys=cache_keys)
        return [self._pack(request, result) for request, result in zip(requests, results)]
