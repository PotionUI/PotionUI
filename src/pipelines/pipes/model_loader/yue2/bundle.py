"""The ``model`` payload produced by ``model_loader/yue2``."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class YuE2ModelBundle:
    """AR/NAR backbone plus the Oobleck VAE decoder."""

    lm: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    lm_cache_key: Optional[str] = None

    @property
    def spec(self):
        return self.lm.spec

    @property
    def tokenizer(self) -> Any:
        lm = self.lm
        return getattr(lm, "tokenizer", None) if lm is not None else None

    def unload(self) -> None:
        for component in (self.lm, self.vae):
            if component is None:
                continue
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("yue2 bundle component eviction failed", exc_info=True)
