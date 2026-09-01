"""The `model` payload produced by `model_loader/trellis2`.

A TRELLIS.2 run needs eight models, and unlike every other family they do not
map one-to-one onto files: four flow DiTs share one checkpoint under four key
prefixes and two decoders share the shape VAE. Each is still cached under its
OWN ``MODELS`` key, so switching the resolution tier re-acquires only the one
flow model the tier changes and leaves the other seven warm.

Like every family's bundle this is a lightweight *view* over independently
evictable components, not an owner — components are held through
``WeakModelRef`` so holding a bundle can never keep an evicted component
resident. Dereference through :meth:`components` once per generation rather
than repeatedly deep inside a loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.arch.trellis2.image_to_mesh import Trellis2Components
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)

#: Bundle field -> the component name a failure message should name. Ordered
#: the way a run consumes them.
_REQUIRED = {
    "conditioner": "DINOv3 image encoder",
    "ss_flow": "sparse-structure flow",
    "ss_vae": "sparse-structure decoder",
    "shape_flow_lr": "shape flow",
    "shape_decoder": "shape decoder",
    "tex_flow": "texture flow",
    "tex_decoder": "texture decoder",
}


@dataclass
class Trellis2ModelBundle:
    """Every model one loaded TRELLIS.2 set needs, plus the tier it was built for.

    ``shape_flow_hr`` is ``None`` for the single-pass 512 tier, ``matting`` is
    ``None`` unless a checkpoint was selected for background removal.
    """

    conditioner: NativeModel = field(default=WeakModelRef())
    ss_flow: NativeModel = field(default=WeakModelRef())
    ss_vae: NativeModel = field(default=WeakModelRef())
    shape_flow_lr: NativeModel = field(default=WeakModelRef())
    shape_flow_hr: Optional[NativeModel] = field(default=WeakModelRef())
    shape_decoder: NativeModel = field(default=WeakModelRef())
    tex_flow: NativeModel = field(default=WeakModelRef())
    tex_decoder: NativeModel = field(default=WeakModelRef())
    matting: Optional[object] = field(default=WeakModelRef())
    tier: str = "1024"
    device: str = "cuda"

    def components(self) -> Trellis2Components:
        """Dereference into the module-level view the run consumes.

        A component the cache has evicted since this bundle was built reads
        back as ``None``; that is a lost race between generations rather than a
        configuration error, so it is named rather than surfacing as an
        ``AttributeError`` on ``None`` several stages into sampling.
        """
        resolved = {}
        for attribute, label in _REQUIRED.items():
            model = getattr(self, attribute)
            if model is None:
                raise ValueError(
                    f"the {label} was evicted from the model cache before this "
                    "generation could use it; re-run to load it again"
                )
            resolved[attribute] = model.module

        return Trellis2Components(
            shape_flow_hr=self.shape_flow_hr.module if self.shape_flow_hr is not None else None,
            matting=self.matting,
            **resolved,
        )

    def unload(self) -> None:
        """Evict every component (idempotent)."""
        for attribute in (*_REQUIRED, "shape_flow_hr"):
            component = getattr(self, attribute)
            unload = getattr(component, "unload", None)
            if not callable(unload):
                continue
            try:
                unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("trellis2 bundle component eviction failed", exc_info=True)
