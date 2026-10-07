from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from typing import Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.pipelines.pipes.model_loader.trellis2.bundle import Trellis2ModelBundle
from src.platform.runtime.native.arch.pixal3d.config import SINGLE_VIEW
from src.platform.runtime.native.arch.pixal3d.image_to_mesh import Pixal3DComponents
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class Pixal3DModelBundle(Trellis2ModelBundle):
    naf: NativeModel = field(default=WeakModelRef())
    camera_estimator: Optional[NativeModel] = field(default=WeakModelRef())
    camera_estimator_selected: bool = False
    variant: str = SINGLE_VIEW

    def components(self) -> Pixal3DComponents:
        base = super().components()
        naf = self.naf
        if naf is None:
            raise ValueError(
                "the NAF upsampler was evicted from the model cache before this generation could use it; "
                "re-run to load it again"
            )
        estimator = self.camera_estimator
        if estimator is None and self.camera_estimator_selected:
            raise ValueError(
                "the MoGe-2 camera estimator was evicted from the model cache before this generation could "
                "use it; re-run to load it again"
            )
        return Pixal3DComponents(
            **{item.name: getattr(base, item.name) for item in fields(base)},
            naf=naf.module,
            camera_estimator=estimator.module if estimator is not None else None,
        )

    def unload(self) -> None:
        super().unload()
        for name in ("naf", "camera_estimator"):
            unload = getattr(getattr(self, name), "unload", None)
            if callable(unload):
                try:
                    unload()
                except Exception:
                    logger.debug("pixal3d %s eviction failed", name, exc_info=True)
