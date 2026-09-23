from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import torch

from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.pipelines.pipes._shared.imaging.alpha import flatten_onto
from src.platform.runtime.primitives.clip import ConditioningModel
from src.platform.runtime.native.text_encoders import (
    NativeTextEncoder,
    image_content_fingerprint,
    prompt_embed_key,
)

logger = logging.getLogger(__name__)


def _to_image_tensor(image: Any) -> torch.Tensor:
    """PIL/array/tensor -> ``[H, W, 3]`` float32 in ``[0, 1]`` (mirrors
    ``model_loader/qwen/qwen_clip.py``'s helper of the same name)."""
    if isinstance(image, torch.Tensor):
        return image
    if isinstance(image, np.ndarray):
        return torch.from_numpy(image.astype(np.float32) / 255.0 if image.dtype == np.uint8 else image.astype(np.float32))
    arr = np.asarray(flatten_onto(image), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)


class QwenImage21ClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt the native Qwen3-VL-8B encoder to the ``ClipTextEncoder`` ABC.

    Image-conditioned encode (Qwen-Image-2.1 edit): when ``encode_prompt``
    receives ``images``, both the positive AND negative pass are encoded WITH
    the full reference set (``QwenImage21TextEncoder.encode(..., images=...)``)
    — same rationale as ``QwenClipTextEncoder`` (1.0): an asymmetric cond/uncond
    would make "no image" part of what CFG contrasts against, not just "no
    instruction". The cache key folds in ``image_content_fingerprint`` per
    image so two different source sets with the same prompt text never alias.
    """

    forwards_full_image_batch = True

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
        prompt = request["prompt"]
        negative_prompt = request["negative_prompt"]
        do_classifier_free_guidance = bool(request.get("do_classifier_free_guidance", True))
        embedding_files = request.get("embedding_files")
        images = request.get("images")
        if embedding_files:
            logger.debug(
                "QwenImage21ClipTextEncoder: textual-inversion embeddings are unsupported "
                "for the Qwen3-VL-8B encoder; ignoring %d entr(y/ies)",
                len(embedding_files),
            )

        image_tensors = [_to_image_tensor(img) for img in images] if images else None

        def _encode():
            if image_tensors:
                pos = self.encoder.encode([prompt], images=image_tensors)
                neg = self.encoder.encode([negative_prompt], images=image_tensors) if do_classifier_free_guidance else {}
            else:
                pos = self.encoder.encode_weighted(prompt)
                neg = self.encoder.encode_weighted(negative_prompt) if do_classifier_free_guidance else {}
            return pos, neg

        key_parts: list = [prompt, negative_prompt, do_classifier_free_guidance]
        if image_tensors:
            key_parts.extend(image_content_fingerprint(img) for img in image_tensors)
        cache_key = prompt_embed_key(self._model_fingerprint, "qwen_image21_te", *key_parts)
        return _encode, cache_key

    def _pack(self, request: Dict[str, Any], result: Any) -> ConditioningModel:
        embeds, n_embeds = result
        return ConditioningModel(
            p_prompt=request["prompt"],
            n_prompt=request["negative_prompt"],
            embeds=embeds,
            n_embeds=n_embeds,
        )
