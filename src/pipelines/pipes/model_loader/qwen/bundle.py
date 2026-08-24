"""The `model` payload produced by `model_loader/qwen`.

A Qwen-Image generation needs the MMDiT + Qwen2.5-VL text encoder + the Wan-2.1
causal-3D VAE. Each component is an independently MODELS-cached ``NativeModel``,
so the bundle is a lightweight *view* over three independently-evictable
modules — not an owner. Its ``unload()`` delegates to each component
(idempotent: ``NativeModel.unload`` is safe to call twice), matching the
evictable shape the lifecycle manager expects.

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
class QwenModelBundle:
    """DiT + Qwen2.5-VL text encoder + causal-3D VAE for one loaded Qwen-Image set."""

    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    # The MODELS cache key `te` was acquired under (``f"native/te/{te_path}"``,
    # see model_loader/qwen/main.py) — a plain str, not a weak view: it's not a
    # MODELS-cached OBJECT itself, just the lookup key `generator/qwen` needs to
    # release the TE explicitly once prompt_encoder is done with it (mirrors
    # `latent_upscaler/ltx/main.py`'s `_unload_idle_te` /
    # `LTXModelBundle.te_cache_key`). `None` for any bundle built without going
    # through the MODELS cache (e.g. isolated pipe tests).
    te_cache_key: Optional[str] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings incl. cfg guidance, latent format)."""
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
                logger.debug("qwen bundle component eviction failed", exc_info=True)
