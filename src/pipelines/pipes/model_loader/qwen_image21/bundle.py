from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class QwenImage21ModelBundle:
    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
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
            except Exception:
                logger.debug("qwen_image21 bundle component eviction failed", exc_info=True)
