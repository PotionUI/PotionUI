"""ClipTextEncoder adapter for the native Qwen-Image text encoder (Qwen2.5-VL).

Bridges the native ``Qwen25VLTextEncoder`` to the shared ``ClipTextEncoder`` ABC
so the generic ``prompt_encoder`` pipe produces a ``ConditioningModel`` for
Qwen-Image exactly as it does for SDXL / Flux / Wan — no raw-prompt-in-generator
bypass. The Qwen2.5-VL encoder emits ``{"context": [B,S,3584], "attention_mask":
[B,S]}`` (a single text encoder, NO pooled vector); this adapter packs that into
``ConditioningModel.embeds`` / ``.n_embeds`` unchanged, and the downstream
``generator/qwen`` pipe maps those role keys onto the Qwen-Image DiT's context
cross-attention input.

Qwen-Image uses true classifier-free guidance (spec guidance ``"cfg"``), so the
negative prompt IS encoded (the generator runs a cond + uncond pass) — unlike the
embedded-guidance Flux path. This mirrors ``WanClipTextEncoder``.

Image-conditioned encode (Qwen-Image-Edit): when ``encode_prompt``
receives ``images`` (only ``model_loader/qwen``'s ``edit`` mode wires this — see
that pipe's ``vision`` config), both the positive AND negative pass are encoded
WITH the image (``Qwen25VLTextEncoder.encode(..., images=...)``, prompt-weighting
unsupported on that path — the underlying encoder does not support both at once),
instead of the plain-text ``encode_weighted`` path. The cache key folds in
``image_content_fingerprint`` per image (see ``embed_cache.py``): a key built from
the prompt text alone would silently alias two different source images.

``encode_prompts``: inherited from ``SequentialWindowClipTextEncoder``
— every request that misses the prompt-embed cache is encoded under ONE shared
GPU-resident window instead of one window per request. See that class's
docstring for the full rationale.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import torch

from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.platform.runtime.primitives.clip import ConditioningModel
from src.platform.runtime.native.text_encoders import (
    NativeTextEncoder,
    image_content_fingerprint,
    prompt_embed_key,
)

logger = logging.getLogger(__name__)


def _to_image_tensor(image: Any) -> torch.Tensor:
    """PIL/array/tensor -> ``[H, W, 3]`` float32 in ``[0, 1]`` (the vision
    tower's contract — see ``qwen_vl_vision.py``'s ``preprocess_qwen_vl_image``
    docstring). ``media_loader`` hands the pipe PIL images (matching every
    other family's img2img source-image convention); a bare tensor/array
    passes through unchanged (tests build one directly)."""
    if isinstance(image, torch.Tensor):
        return image
    if isinstance(image, np.ndarray):
        return torch.from_numpy(image.astype(np.float32) / 255.0 if image.dtype == np.uint8 else image.astype(np.float32))
    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)


class QwenClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt the native Qwen2.5-VL encoder to the ``ClipTextEncoder`` ABC.

    ``model_fingerprint`` is surfaced as ``_model_fingerprint`` so the
    ``prompt_encoder`` pipe folds the encoder identity (checkpoint + LoRA set)
    into its conditioning cache key — matching how SDXL/Flux/Wan tag their clip.
    """

    # Qwen-Image-Edit's reference set (up to 3 images) is shared, unindexed,
    # across every output of a batch — joint conditioning, not one image per
    # output — same idiom as MiniMax-H3's fl2va keyframe pair (see
    # model_loader/minimax_h3/clip.py). Without this, prompt_encoder's
    # per-index `images[_]` pick would vision-ground each batch output on
    # only ONE of the N references, decoupled from the full ref_latents set
    # generator/qwen's DiT call receives.
    forwards_full_image_batch = True

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

        ``num_images_per_prompt`` is intentionally ignored: the ``generator/qwen``
        seed loop produces N images from a single conditioning. ``embedding_files``
        (textual inversion) has no meaning for the Qwen2.5-VL encoder and is
        ignored with a debug note. The negative is always encoded when CFG is
        requested — Qwen-Image's true-CFG sampler needs the uncond pass.

        ``images`` (Qwen-Image-Edit): when given, BOTH the
        positive and negative pass are vision-conditioned on the same source
        image(s) — the model was trained with the image present in both, and
        an asymmetric cond/uncond would make "no image" part of what CFG
        contrasts against, not just "no instruction". Requires a vision-enabled
        encoder (``self.encoder._has_vision``); raises via
        ``Qwen25VLTextEncoder.encode`` otherwise. A1111 prompt weighting is
        unsupported together with ``images`` — the underlying encoder has no
        weighted+image path — so this branch calls ``encode()`` directly,
        never ``encode_weighted()``.
        """
        prompt = request["prompt"]
        negative_prompt = request["negative_prompt"]
        do_classifier_free_guidance = bool(request.get("do_classifier_free_guidance", True))
        embedding_files = request.get("embedding_files")
        images = request.get("images")
        if embedding_files:
            logger.debug(
                "QwenClipTextEncoder: textual-inversion embeddings are unsupported "
                "for the Qwen2.5-VL encoder; ignoring %d entr(y/ies)",
                len(embedding_files),
            )

        image_tensors = [_to_image_tensor(img) for img in images] if images else None

        # Encode on the GPU (the encoder is loaded on CPU and must be moved, or the
        # 7B Qwen2.5-VL forward runs on the CPU in fp32 — a big chunk of a cold run).
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
            # Two different source images with the same prompt text must NOT
            # alias to the same cached embedding — see embed_cache.py's
            # image_content_fingerprint docstring for the full hazard.
            key_parts.extend(image_content_fingerprint(img) for img in image_tensors)
        cache_key = prompt_embed_key(
            self._model_fingerprint, getattr(self.encoder, "role", None), *key_parts,
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
