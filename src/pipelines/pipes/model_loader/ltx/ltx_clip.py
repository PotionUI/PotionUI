
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.errors import retry_after_cuda_oom
from src.platform.runtime.native.memory import (
    free_vram_gb,
    minimum_inference_memory_gb,
    run_text_encode,
)
from src.platform.runtime.native.text_encoders import prompt_embed_key
from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache
from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024 ** 3


class LTXClipTextEncoder(ClipTextEncoder):
    """Adapt the native Gemma3 encoder + DiT projection chain to the
    ``ClipTextEncoder`` ABC."""

    # Weak views over the raw modules to avoid retaining them. Class-level
    # descriptors (not instance attributes) so `self._resolved_te = ...` routes
    # through `WeakModelRef.__set__`. `_resolved_te` holds the encoder only
    # once it has actually been acquired -- `te_encoder` is the property below,
    # which triggers the deferred `te_factory` on first read.
    _resolved_te = WeakModelRef()
    dit_module = WeakModelRef()

    def __init__(
        self,
        te_encoder: Optional[Any],
        dit_module: Any,
        projections: Dict[str, torch.Tensor],
        *,
        device: str = "cuda",
        model_fingerprint: Optional[str] = None,
        te_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._resolved_te = te_encoder
        self._te_factory = te_factory
        self.dit_module = dit_module
        self.projections = projections
        self.device = device
        self._model_fingerprint = model_fingerprint

    @property
    def te_encoder(self) -> Any:
        """The Gemma3/Gemma4 encoder module, acquiring it on first read when
        the loader deferred it (see the module docstring).

        The factory is deliberately NOT cleared after a successful call: a
        weak view can legitimately go dead mid-generation (the cache entry
        this mirrors was evicted), and re-resolving is what every other read
        of this property already expects. A raising factory leaves
        ``_resolved_te`` unset, so the next read retries from scratch rather
        than handing back a half-acquired encoder.
        """
        resolved = self._resolved_te
        if resolved is None and self._te_factory is not None:
            resolved = self._te_factory()
            self._resolved_te = resolved
        return resolved

    @torch.inference_mode()
    def encode_prompt(
        self,
        prompt: str,
        negative_prompt: str,
        num_images_per_prompt: int = 1,
        do_classifier_free_guidance: bool = True,
        embedding_files: Optional[Dict[str, str]] = None,
    ) -> ConditioningModel:
        """Encode one prompt/negative pair. Thin single-request wrapper over
        :meth:`encode_prompts` — kept so callers that don't batch still work."""
        return self.encode_prompts([{
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "num_images_per_prompt": num_images_per_prompt,
            "do_classifier_free_guidance": do_classifier_free_guidance,
            "embedding_files": embedding_files,
        }])[0]

    @torch.inference_mode()
    def encode_prompts(self, requests: List[Dict[str, Any]]) -> List[ConditioningModel]:
        """Encode a BATCH of prompt/negative pairs in ONE GPU-resident window.

        Each request's cache key covers the FINAL projected conditioning (not
        just the raw Gemma3 output, as an earlier cut of this cache did) — a
        cache hit skips the GPU move, the encode, AND the projection/connector
        chain entirely. Only requests that miss are encoded, and they're
        encoded and projected together as one batch (see
        :meth:`_encode_and_project_batch`), so an N-image generation pays for
        the ~20GB Gemma3-12B move once, not N times.
        """
        for request in requests:
            if request.get("embedding_files"):
                logger.debug("LTXClipTextEncoder: textual-inversion embeddings ignored (Gemma3)")

        # A static tag, not `self.te_encoder.role`: reading the live encoder
        # here would resolve the deferred `te_factory` on every request, hit
        # or miss (see the module docstring's "Deferred TE acquisition").
        keys = [
            prompt_embed_key(
                self._model_fingerprint, "ltx_te",
                request["prompt"], request["negative_prompt"],
                bool(request.get("do_classifier_free_guidance", True)),
            )
            for request in requests
        ]

        cache = get_prompt_embed_cache()
        results: List[Optional[ConditioningModel]] = [None] * len(requests)
        miss_indices: List[int] = []
        for i, key in enumerate(keys):
            cached = cache.get(key) if key is not None else None
            if cached is not None:
                results[i] = self._conditioning_from_projected(requests[i], cached)
            else:
                miss_indices.append(i)

        if miss_indices:
            pairs = [
                (
                    requests[i]["prompt"], requests[i]["negative_prompt"],
                    bool(requests[i].get("do_classifier_free_guidance", True)),
                )
                for i in miss_indices
            ]
            projected_batch = self._encode_and_project_batch(pairs)
            for idx, projected in zip(miss_indices, projected_batch):
                results[idx] = self._conditioning_from_projected(requests[idx], projected)
                key = keys[idx]
                if key is not None:
                    cache.put(key, projected)

        return results  # type: ignore[return-value]

    @staticmethod
    def _conditioning_from_projected(request: Dict[str, Any], projected: Dict[str, Any]) -> ConditioningModel:
        n_embeds: Dict[str, Any] = {}
        if projected.get("n_context") is not None:
            n_embeds = {"context": projected["n_context"]}
        return ConditioningModel(
            p_prompt=request["prompt"],
            n_prompt=request["negative_prompt"],
            embeds={"context": projected["context"]},
            n_embeds=n_embeds,
        )

    def _projection_budget_gb(self, raw: Dict[str, torch.Tensor]) -> float:
        """Honest VRAM estimate (GB) for running ``apply_text_conditioning``
        over ``raw`` — the projection weights + embeddings-connector chain,
        PLUS the transients ``LTXAVModel.apply_text_conditioning`` (model.py)
        takes on unconditionally:

          - ``out = gemma_output.float()`` upcasts the raw Gemma3 stack itself
            to fp32 — a full extra copy at ``raw["context"]``'s fp32 size when
            it's stored as bf16/fp16.
          - ``video_projection_weight.to(flat)`` / ``audio_projection_weight
            .to(flat)`` upcast EACH projection weight to fp32 for the matmul —
            the original (bf16) weight and its fp32 cast are both live at once.
          - the embeddings connectors pad every sequence to
            ``max(1024, seq_len)`` regardless of prompt length (see
            ``Embeddings1DConnector.forward``), so their 8-layer activation
            cost is dominated by batch size, not prompt length.

        The previous cut of this budget (``_connector_budget_gb``) counted
        only the static (bf16) weight bytes and none of the above — a "fits"
        verdict from it would then go on to OOM inside the matmul itself
        (the raw encode succeeded on the GPU, but the
        projection phase that followed did not, and the resulting OOM was
        indistinguishable from a real encode-phase OOM to the caller).
        """
        total = 0
        context = raw.get("context")
        if isinstance(context, torch.Tensor):
            if context.dtype in (torch.float16, torch.bfloat16):
                total += context.numel() * 4  # gemma_output.float() transient
            else:
                total += context.numel() * context.element_size()
            batch = int(context.shape[0])
            seq = max(int(context.shape[1]), 1024) if context.dim() > 1 else 1024
        else:
            batch, seq = 1, 1024

        connectors = (
            getattr(self.dit_module, "video_embeddings_connector", None),
            getattr(self.dit_module, "audio_embeddings_connector", None),
        )
        for value in self.projections.values():
            if not isinstance(value, torch.Tensor):
                continue
            total += value.numel() * value.element_size()  # resident weight
            if value.dtype in (torch.float16, torch.bfloat16):
                total += value.numel() * 4  # weight.to(flat) fp32 cast transient
        for connector in connectors:
            if connector is None:
                continue
            for tensor in list(connector.parameters()) + list(connector.buffers()):
                if tensor is not None:
                    total += tensor.numel() * tensor.element_size()

        # Connector activation allowance: an 8-gated-block 1D-attention stack
        # per connector over [batch, seq, inner_dim] (q/k/v/attn-out/mlp(4x)
        # intermediates) — conservative estimate, not exact.
        inner_dims = [
            getattr(connector, "inner", None) for connector in connectors if connector is not None
        ]
        inner_dims = [d for d in inner_dims if isinstance(d, int)] or [4096, 2048]
        per_token_bytes = sum(dim * 2 for dim in inner_dims) * 8  # bf16, ~8x/layer, both connectors
        total += batch * seq * per_token_bytes

        return total / _BYTES_PER_GB

    def _move_projection_chain(self, device: str) -> None:
        """Move the embeddings-connector modules (real ``nn.Module``s owned by
        the shared DiT module) and the projection tensors to ``device``. Safe
        to call with the DiT's own module even though it's shared with the
        generator pipe: this only touches the two connector submodules, and
        always moves them back to CPU before ``encode_prompts`` returns, so
        the DiT's own residency bookkeeping (still "cpu" throughout) stays
        accurate for whoever loads it next.

        Mutates each projection tensor's storage IN PLACE (``tensor.data =
        tensor.data.to(device)``) rather than rebinding ``self.projections`` to
        a new dict. ``self.projections`` is the SAME dict object the model
        loader also stashed on the bundle (``LTXModelBundle.projections`` —
        see ``model_loader/ltx/main.py``); reassigning ``self.projections``
        here only rebound this instance's own reference, leaving the bundle's
        (and the ``MODELS`` cache's) copy pointing at the stale, un-moved
        tensors. The round trip to GPU and back then produced a SECOND,
        permanently-retained CPU allocation every call — the "video/audio
        projection weight present twice in the CPU census" leak. Moving
        each tensor's storage in place keeps every alias
        (this instance's dict, the bundle's dict, the cache's dict — all the
        same object) looking at the one tensor, on whichever device it
        actually lives on right now.
        """
        video_connector = getattr(self.dit_module, "video_embeddings_connector", None)
        audio_connector = getattr(self.dit_module, "audio_embeddings_connector", None)
        if video_connector is not None:
            video_connector.to(device)
        if audio_connector is not None:
            audio_connector.to(device)
        for tensor in self.projections.values():
            if isinstance(tensor, torch.Tensor):
                tensor.data = tensor.data.to(device)

    def _encode_and_project_batch(self, pairs: List[Tuple[str, str, bool]]) -> List[Dict[str, Any]]:
        """Encode + project a batch of (prompt, negative, do_cfg) pairs.

        Split into two INDEPENDENTLY-placed phases: the raw
        Gemma3 encode, then the projection/connector chain. Bundling both
        under one ``run_text_encode`` window (the previous cut) meant a
        projection-phase OOM was caught by the SAME co-resident/evict/
        cpu-fallback ladder as the raw encode — so a successful ~22GB GPU
        encode followed by an OOM'ing projection cascaded into redoing the
        WHOLE thing (encode included) on the CPU, discarding the already-good
        GPU work. Splitting them means:

          1. ``run_text_encode`` places the raw encode using only the TE's own
             footprint — unaffected by the projection chain's needs. Its own
             ``finally`` offloads the TE back to CPU the instant the raw
             encode returns, freeing its ~20GB before the projection phase
             even starts.
          2. ``_project_with_ladder`` then decides where to run the (much
             smaller) projection/connector chain against an HONEST budget
             (:meth:`_projection_budget_gb`) — almost always fitting on the
             GPU alone now that the TE is gone, falling back to CPU only as a
             last resort (a small, cheap phase — not a full re-encode).
        """
        texts: List[str] = []
        spans: List[Tuple[int, Optional[int]]] = []
        for prompt, negative, do_cfg in pairs:
            pos_idx = len(texts)
            texts.append(prompt)
            neg_idx: Optional[int] = None
            if do_cfg:
                neg_idx = len(texts)
                texts.append(negative)
            spans.append((pos_idx, neg_idx))

        # One property read (one deferred acquisition at most) held as a local
        # for the length of the encode window: `self.te_encoder` is a weak
        # view, and the placement call below must not be able to see it go
        # dead between the two arguments.
        te_encoder = self.te_encoder
        raw = run_text_encode(
            te_encoder, self.device, lambda: te_encoder.encode(texts),
            reserve_gb=minimum_inference_memory_gb(),
        )
        get_profiler().mark("ltx.raw_encode", device=str(raw["context"].device), batch=len(texts))

        projected_all = self._project_with_ladder(raw)
        get_profiler().mark(
            "ltx.project", device=str(projected_all.device), batch=len(texts)
        )

        out: List[Dict[str, Any]] = []
        for pos_idx, neg_idx in spans:
            context = projected_all[pos_idx : pos_idx + 1]
            n_context = projected_all[neg_idx : neg_idx + 1] if neg_idx is not None else None
            out.append({"context": context, "n_context": n_context})
        return out

    def _project_with_ladder(self, raw: Dict[str, torch.Tensor]) -> torch.Tensor:
        """Run the projection/connector chain, choosing where based on what's
        actually free right now — never by cascading back into a full
        CPU re-encode (split failure domains).

        ``raw`` may already be on the GPU (co-resident/after-evict — the
        common case: by the time this runs, ``run_text_encode``'s own cleanup
        has already moved the TE back off the GPU, so the projection budget
        almost always fits alone) or on the CPU (a genuine raw-encode OOM, or
        no CUDA at all — nothing to gain by moving the connectors anywhere).
        A GPU projection attempt that still OOMs despite the budget check
        (e.g. another process claimed the freed VRAM) falls back to CPU
        projection — cheap, unlike the old all-or-nothing cliff that
        re-ran the entire (expensive) raw encode on CPU too.
        """
        raw_device = str(raw["context"].device)
        if not raw_device.startswith("cuda") or not torch.cuda.is_available():
            return self._project_on(raw, "cpu")

        budget_gb = self._projection_budget_gb(raw) + minimum_inference_memory_gb()
        free_gb = free_vram_gb(raw_device)
        if free_gb is not None and free_gb < budget_gb:
            torch.cuda.empty_cache()
            free_gb = free_vram_gb(raw_device)
        if free_gb is not None and free_gb < budget_gb:
            logger.warning(
                "ltx projection: only %.1fGB free on %s (need ~%.1fGB); "
                "projecting on CPU instead of risking an OOM", free_gb, raw_device, budget_gb,
            )
            return self._project_on(raw, "cpu")

        try:
            return self._project_on(raw, raw_device)
        except RuntimeError as exc:
            if not retry_after_cuda_oom(exc):
                raise
            logger.warning(
                "ltx projection: OOM on %s despite the budget check; falling back to CPU "
                "(raw encode is kept, not redone)", raw_device,
            )
            torch.cuda.empty_cache()
            return self._project_on(raw, "cpu")

    def _project_on(self, raw: Dict[str, torch.Tensor], device: str) -> torch.Tensor:
        """Move ``raw`` + the projection chain to ``device``, project, then
        always move the (shared, DiT-owned) projection chain back to CPU —
        regardless of whether the projection itself raised — so the DiT's own
        residency bookkeeping stays accurate for whoever loads it next.

        LTX RAM-ratchet follow-up: every call that actually ran on
        ``cuda`` round-trips the connector modules + projection weight
        matrices (video/audio, ~1.4GB + ~0.7GB bf16 -- see
        :meth:`_projection_budget_gb`'s own accounting) CPU->GPU->CPU. The
        landing-back-on-CPU tensors from that final ``.to("cpu")`` are fresh
        anonymous allocations (not the original mmap-backed checkpoint
        tensors), and this file never called ``trim_host_allocator()`` --
        unlike every other big CPU<->GPU move in the native engine
        (``NativeModel.move_to``/``offload``), so glibc kept every one of
        these round-trips' freed heap in its arenas instead of returning it.
        Measured at +11.9GB RSS at the ``ltx.project`` mark for a single
        short-prompt call. Gated on actually having moved
        anything to a real device (the CPU-only fallback path never touches
        the GPU, so there's nothing to reclaim) and coarse (once per batched
        ``encode_prompts`` call, not per-token/per-layer).
        """
        if str(raw["context"].device) != device:
            raw = {k: (v.to(device) if isinstance(v, torch.Tensor) else v) for k, v in raw.items()}
        self._move_projection_chain(device)
        try:
            return self._project(raw)
        finally:
            self._move_projection_chain("cpu")
            if device.startswith("cuda"):
                from src.platform.runtime.model_lifecycle.lifecycle import trim_host_allocator

                trim_host_allocator()

    def _project(self, raw: Dict[str, torch.Tensor]) -> torch.Tensor:
        """Run the DiT's own text conditioning (projection + connectors) over
        the WHOLE batch at once — returns one context tensor per batch row,
        index-aligned with the texts that were encoded."""
        return self.dit_module.apply_text_conditioning(
            raw["context"], attention_mask=raw.get("attention_mask"), **self.projections
        )
