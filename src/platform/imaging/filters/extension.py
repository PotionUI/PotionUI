from __future__ import annotations

from typing import Any, Mapping

import numpy as np


class ColourOp:
    def map(self, rgb: np.ndarray, params: Mapping[str, Any]) -> np.ndarray:
        raise NotImplementedError


class SpatialOp:
    def apply(self, image: np.ndarray, params: Mapping[str, Any], amount: float) -> np.ndarray:
        raise NotImplementedError
