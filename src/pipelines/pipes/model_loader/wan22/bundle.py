"""The `model` payload produced by `model_loader/wan22`.

A Wan generation needs the DiT(s) + UMT5 TE + causal-3D VAE. Wan 2.2 14B ships
as a HIGH-noise / LOW-noise expert PAIR (two DiT files); the bundle carries both
(the generator's expert router switches between them at the sampling boundary).
Wan 2.1 and the 5B ti2v are single-DiT — ``low_dit`` is then ``None`` and the
router degrades to the one expert.

Each component is an independently MODELS-cached ``NativeModel``; the bundle is a
lightweight view whose ``unload()`` delegates to each (idempotent).

Components are held via ``WeakModelRef`` (see that module's docstring):
holding onto a bundle instance can never keep a stale, evicted
component resident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class WanModelBundle:
    """High(-noise) DiT + optional low-noise expert + UMT5 TE + causal-3D VAE.

    ``loras_high``/``loras_low`` are the (already-``active_loras``-filtered)
    preset-level base LoRA stacks this bundle's experts were acquired with --
    carried alongside the patched DiTs so a downstream consumer that re-acquires
    an expert with a DIFFERENT LoRA stack (e.g. `generator/chain_video_wan22`'s
    per-segment override) can compose its override on top of these base stacks
    instead of silently replacing them.
    """

    high_dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    low_dit: Optional[NativeModel] = field(default=WeakModelRef())
    loras_high: List[Dict[str, Any]] = field(default_factory=list)
    loras_low: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings incl. expert_boundary, latent format)."""
        return self.high_dit.spec

    @property
    def te_encoder(self):
        te = self.te
        return te.module if te is not None else None

    @property
    def is_dual_expert(self) -> bool:
        return self.low_dit is not None

    def unload(self) -> None:
        for component in (self.high_dit, self.low_dit, self.te, self.vae):
            if component is None:
                continue
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("wan bundle component eviction failed", exc_info=True)
