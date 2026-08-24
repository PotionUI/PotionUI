"""The `model` payload produced by `model_loader/seedvr2`.

SeedVR2 is the first native family with NO live text encoder: it conditions on a
*fixed* precomputed positive prompt embedding (``models/clip/seedvr2_pos_emb.pt``,
a ``(seq, 5120)`` tensor) fed straight into the DiT's ``txt_in`` projection. So
this bundle carries only the NaDiT (33->16ch restoration transformer) + the
self-normalizing causal-video VAE + that fixed embedding tensor — no TE, no LoRAs.

Each heavy component is an independently MODELS-cached ``NativeModel``, so the
bundle is a lightweight *view* over two independently-evictable modules (plus the
tiny embedding tensor); ``unload()`` delegates to each (idempotent), matching the
evictable shape the lifecycle manager expects.

The ``te_encoder`` property returns ``None`` on purpose: the shared
``build_native_generator`` factory calls ``cls(bundle.dit, bundle.te_encoder,
bundle.vae, ...)`` and ``NativeGenerator`` tolerates a ``None`` text encoder (its
placement/offload paths duck-type it) — SeedVR2 never encodes a prompt.

``dit``/``vae`` are held via ``WeakModelRef`` (see that module's docstring):
holding onto a bundle instance can never keep a stale, evicted
component resident. ``prompt_embedding`` stays a plain strong field (a tiny
fixed tensor, not independently MODELS-cached) - given a default here only so
it can follow the other now-defaulted fields in declaration order; every real
call site passes it explicitly by keyword.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import torch

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class SeedVR2ModelBundle:
    """NaDiT + self-normalizing causal-video VAE + fixed positive prompt embedding."""

    dit: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    prompt_embedding: Optional[torch.Tensor] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (family/variant, latent format, sampling settings)."""
        return self.dit.spec

    @property
    def te_encoder(self):
        """SeedVR2 has no live text encoder — conditioning is the fixed embedding."""
        return None

    def unload(self) -> None:
        """Evict every heavy component (idempotent)."""
        for component in (self.dit, self.vae):
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("seedvr2 bundle component eviction failed", exc_info=True)
