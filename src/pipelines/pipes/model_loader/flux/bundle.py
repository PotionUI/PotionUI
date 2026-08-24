"""The `model` payload produced by `model_loader/flux` and consumed by
`generator/flux`.

Each component (DiT / TE / VAE) is a ``NativeModel`` acquired under its own
``MODELS`` cache key, so the bundle is a lightweight *view* over three
independently-cached, independently-evictable modules — not an owner. Its
``unload()`` delegates to each component (idempotent: ``NativeModel.unload`` is
safe to call twice), matching the evictable shape the lifecycle manager expects.

Components are held via ``WeakModelRef`` (see that module's docstring):
holding onto a bundle instance can never keep a stale, evicted
component resident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class FluxModelBundle:
    """DiT + text encoder + VAE for one loaded Flux-family checkpoint set."""

    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    # The MODELS cache key `te` was acquired under -- a plain str lookup key,
    # not a MODELS-cached object: `generator/flux` releases the TE explicitly
    # once prompt_encoder is done with it (same `bundle.te_cache_key` +
    # `models.evict_dead_weight` mechanism model_loader/krea2's bundle uses).
    # `None` for a bundle built without the MODELS cache (isolated pipe tests).
    te_cache_key: Optional[str] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings, latent format, family/variant)."""
        return self.dit.spec

    @property
    def te_encoder(self):
        """The raw ``NativeTextEncoder`` that ``NativeGenerator`` consumes."""
        te = self.te
        return te.module if te is not None else None

    def unload(self) -> None:
        """Evict every component (idempotent)."""
        for component in (self.dit, self.te, self.vae):
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("flux bundle component eviction failed", exc_info=True)
