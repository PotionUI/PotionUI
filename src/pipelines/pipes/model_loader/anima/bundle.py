"""The `model` payload produced by `model_loader/anima`.

An Anima generation needs the MiniTrainDIT (with its in-model LLMAdapter) + the
Qwen3-0.6B text encoder + the Wan-2.1 causal-3D VAE (16ch, shared with Qwen-Image
and Krea-2). Each component is an independently MODELS-cached ``NativeModel``, so
the bundle is a lightweight *view* over three independently-evictable modules —
not an owner. ``unload()`` delegates to each (idempotent), matching the evictable
shape the lifecycle manager expects.

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
class AnimaModelBundle:
    """DiT (+ LLMAdapter) + Qwen3-0.6B text encoder + causal-3D VAE for one Anima set."""

    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    # The MODELS cache key `te` was acquired under (see model_loader/anima/
    # main.py) -- a plain str lookup key, not a MODELS-cached object:
    # `generator/anima` releases the TE explicitly once prompt_encoder is done
    # with it (same ``bundle.te_cache_key`` + ``models.evict_dead_weight``
    # mechanism the qwen/krea2 idle-TE paths use). ``None`` for a bundle built
    # without the MODELS cache (isolated pipe tests).
    te_cache_key: Optional[str] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings incl. cfg guidance, latent format)."""
        return self.dit.spec

    @property
    def te_encoder(self):
        """The raw ``NativeTextEncoder`` (``AnimaTextEncoder``) the generator consumes."""
        te = self.te
        return te.module if te is not None else None

    def unload(self) -> None:
        """Evict every component (idempotent)."""
        for component in (self.dit, self.te, self.vae):
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("anima bundle component eviction failed", exc_info=True)
