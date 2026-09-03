"""ClipTextEncoder adapter for the native Krea-2 text encoder (Qwen3-VL-4B).

Bridges the native ``Qwen3VLTextEncoder`` to the shared ``ClipTextEncoder`` ABC
so the generic ``prompt_encoder`` pipe produces a ``ConditioningModel`` for
Krea-2 exactly as for SDXL/Flux — no raw-prompt-in-generator bypass.

Krea-2's TE emits ``{"context": (B, S, 12, 2560), "attention_mask": (B, S)}`` —
the 12-layer axis is kept separate (position 2) because the DiT's ``txtfusion``
attends across the layers before collapsing them (unlike Klein, which
concatenates layers on the feature axis). This adapter packs that dict verbatim
into ``ConditioningModel.embeds``; the ``generator/krea2`` pipe / the Krea2 flat
forward map it onto the DiT ``context`` input.

Krea-2's registry guidance is ``"cfg"`` (turbo's cfg_scale=1.0 collapses TrueCFG
to a single conditional-only forward, a raw/base checkpoint drives cfg_scale>1).
The negative pass is gated on ``do_classifier_free_guidance`` alone -- an empty
negative prompt still gets encoded, matching qwen/z_image/anima, because an
empty string is a legitimate uncond target for a real CFG family and skipping
it would silently leave ``n_embeds`` empty (CFG inert) while the run still
records a negative prompt as applied.

Image-conditioned encode (Krea-2 edit mode): when ``encode_prompt``
receives ``images``, both the positive AND negative pass are encoded WITH the
image (``Qwen3VLTextEncoder.encode(..., images=...)``) instead of the plain-
text ``encode_weighted`` path — prompt-weighting is unsupported together with
``images`` (the underlying encoder has no weighted+image path), matching
``QwenClipTextEncoder``'s identical restriction for Qwen-Image-Edit. Both
passes see the SAME source image(s): an asymmetric cond/uncond would make "no
image" part of what CFG contrasts against, not just "no instruction". The
cache key folds in ``image_content_fingerprint`` per image plus
``grounding_px`` (a different cap changes the vision-tower output for the
same image) — a key built from the prompt text alone would silently alias two
different source images (or two different caps on the same image).

``encode_prompts``: inherited from ``SequentialWindowClipTextEncoder``
— every request that misses the prompt-embed cache is encoded under ONE shared
GPU-resident window instead of one window per request. See that class's
docstring for the full rationale.

Deferred TE acquisition: ``model_loader/krea2/main.py`` no longer acquires the
TE from ``MODELS`` eagerly — it hands this adapter a ``te_loader`` thunk
instead of an already-resolved module, so the multi-GB Qwen3-VL checkpoint is
only loaded/acquired the first time ``self.encoder`` is actually read (see the
``encoder`` property below and ``clip_batch.py``'s ``encoder_provider``). A
generation whose whole batch is served from the prompt-embed cache never
touches ``MODELS.acquire()`` for the TE at all — previously it was acquired
unconditionally in ``model_loader`` and then unloaded, unused, by
``generator/krea2``'s ``_release_idle_te``.
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
    tower's contract — see ``qwen3_vl_vision.py``'s ``preprocess_qwen3_vl_image``
    docstring), matching ``QwenClipTextEncoder``'s identical helper."""
    if isinstance(image, torch.Tensor):
        return image
    if isinstance(image, np.ndarray):
        return torch.from_numpy(image.astype(np.float32) / 255.0 if image.dtype == np.uint8 else image.astype(np.float32))
    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)


class Krea2ClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt the native Krea-2 Qwen3-VL text encoder to ``ClipTextEncoder``.

    ``encoder`` is normally the already-resolved TE module, exactly as
    before. Pass ``encoder=None`` together with ``te_loader`` instead to defer
    the underlying ``MODELS.acquire()`` (see ``model_loader/krea2/main.py``):
    the loader runs at most once, the first time ``self.encoder`` is actually
    read — which ``SequentialWindowClipTextEncoder.encode_prompts`` only does
    once it already knows a request in the batch misses the prompt-embed
    cache (``clip_batch.py``'s ``encoder_provider``). A batch that's all
    embed-cache hits never resolves it at all, so the multi-GB Qwen3-VL TE is
    never loaded/acquired for a run that never needed it on the GPU.
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
        prompt = request["prompt"]
        negative_prompt = request["negative_prompt"]
        do_classifier_free_guidance = bool(request.get("do_classifier_free_guidance", True))
        embedding_files = request.get("embedding_files")
        images = request.get("images")
        grounding_px = int(request.get("grounding_px", 768))
        system_prompt = request.get("system_prompt")
        if embedding_files:
            logger.debug("Krea2ClipTextEncoder: textual-inversion unsupported; ignoring %d entr(y/ies)",
                         len(embedding_files))

        image_tensors = [_to_image_tensor(img) for img in images] if images else None

        # Encode on the GPU (the encoder is loaded on CPU and must be moved, or the
        # 4B Qwen3-VL forward runs in fp32 on the CPU — a big chunk of a cold run).
        def _encode():
            if image_tensors:
                pos = self.encoder.encode(
                    [prompt], images=image_tensors, grounding_px=grounding_px, system_prompt=system_prompt,
                )
                neg: Dict[str, Any] = (
                    self.encoder.encode(
                        [negative_prompt], images=image_tensors, grounding_px=grounding_px, system_prompt=system_prompt,
                    )
                    if do_classifier_free_guidance
                    else {}
                )
            else:
                pos = self.encoder.encode_weighted(prompt)
                if do_classifier_free_guidance:
                    neg = self.encoder.encode_weighted(negative_prompt)
                else:
                    neg = {}
            return pos, neg

        key_parts: list = [prompt, negative_prompt, do_classifier_free_guidance]
        if image_tensors:
            # Two different source images (or the same image at a different
            # grounding_px cap) with the same prompt text must NOT alias to the
            # same cached embedding — see embed_cache.py's
            # image_content_fingerprint docstring for the full hazard.
            key_parts.append(grounding_px)
            key_parts.append(system_prompt or "")
            key_parts.extend(image_content_fingerprint(img) for img in image_tensors)
        # A static tag, not `self.encoder.role`: reading the live encoder's
        # `.role` here would force the deferred `te_loader` to resolve on
        # EVERY request, hit or miss, defeating the whole point of the
        # `encoder` property above. `role` only ever namespaces this key
        # against a different encoder TYPE sharing the same fingerprint
        # string by coincidence — `self._model_fingerprint` already encodes
        # this encoder's own checkpoint path + dtype + vision flag, which is
        # what actually determines the checkpoint's detected variant, so a
        # fixed per-adapter tag is exactly as collision-safe.
        cache_key = prompt_embed_key(self._model_fingerprint, "krea2_te", *key_parts)
        return _encode, cache_key

    def _pack(self, request: Dict[str, Any], result: Any) -> ConditioningModel:
        embeds, n_embeds = result
        return ConditioningModel(
            p_prompt=request["prompt"], n_prompt=request["negative_prompt"],
            embeds=embeds, n_embeds=n_embeds,
        )
