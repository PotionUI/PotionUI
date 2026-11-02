from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

import numpy as np

from src.platform.imaging.color_fix import ColorFix
from src.platform.imaging.color_fix import estimate_fix as estimate_color_fix
from src.platform.imaging.filters.spatial import lowbias32


@dataclass(frozen=True)
class FrameBatch:
    frames: np.ndarray
    indices: tuple[int, ...]
    times: tuple[float, ...]
    fps: float
    duration: Optional[float]
    tensor: Any = None


class AnalysisContext(Protocol):
    def sample_frames(self, *, width: int, height: int, rate: float, max_frames: int) -> np.ndarray: ...

    def is_cancelled(self) -> bool: ...


FrameRender = Callable[[FrameBatch, dict, dict], Any]
Analysis = Callable[[AnalysisContext, dict], dict]

__all__ = [
    "Analysis",
    "AnalysisContext",
    "ColorFix",
    "FrameBatch",
    "FrameRender",
    "estimate_color_fix",
    "lowbias32",
]
