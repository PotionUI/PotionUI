"""The `model` payload produced by `model_loader/krea2`, consumed by `generator/krea2`.

Same lightweight three-component view as the Flux bundle (DiT + TE + VAE), each a
``NativeModel`` cached under its own ``MODELS`` key. ``unload()`` delegates to each
(idempotent), matching the evictable shape the lifecycle manager expects.

Components are held via ``WeakModelRef`` (see that module's docstring): a bundle
is a VIEW over the cache's own components, never an ownership
root, so holding onto a bundle instance (by anyone, for any reason) can never
keep a stale, evicted DiT/TE/VAE resident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class Krea2ModelBundle:
    """DiT + Qwen3-VL text encoder + Qwen-Image (causal-3d) VAE for one Krea-2 set."""

    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    # The MODELS cache key `te` was acquired under (``f"native/te/{te_path}"``,
    # see model_loader/krea2/main.py) -- a plain str lookup key, not a
    # MODELS-cached object: `generator/krea2` and the krea2-edit plugin release
    # the TE explicitly once prompt_encoder is done with it (same
    # ``bundle.te_cache_key`` + ``models.evict_dead_weight`` mechanism the qwen
    # and LTX idle-TE paths use). ``None`` for a bundle built without the MODELS
    # cache (isolated pipe tests).
    te_cache_key: Optional[str] = None

    @property
    def spec(self):
        return self.dit.spec

    @property
    def te_encoder(self):
        te = self.te
        return te.module if te is not None else None

    def unload(self) -> None:
        for component in (self.dit, self.te, self.vae):
            try:
                component.unload()
            except Exception:  # pragma: no cover
                logger.debug("krea2 bundle component eviction failed", exc_info=True)
