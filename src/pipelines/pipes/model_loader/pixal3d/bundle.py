from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.pipelines.pipes.model_loader.trellis2.bundle import Trellis2ModelBundle
from src.platform.runtime.native.arch.pixal3d.config import SINGLE_VIEW
from src.platform.runtime.native.arch.pixal3d.image_to_mesh import Pixal3DComponents
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class Pixal3DModelBundle(Trellis2ModelBundle):
    naf: NativeModel = field(default=WeakModelRef())
    variant: str = SINGLE_VIEW

    def components(self) -> Pixal3DComponents:
        base = super().components()
        naf = self.naf
        if naf is None:
            raise ValueError(
                "the NAF upsampler was evicted from the model cache before this generation could use it; "
                "re-run to load it again"
            )
        return Pixal3DComponents(**{item.name: getattr(base, item.name) for item in fields(base)}, naf=naf.module)

    def unload(self) -> None:
        super().unload()
        unload = getattr(self.naf, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception:
                logger.debug("pixal3d NAF eviction failed", exc_info=True)
