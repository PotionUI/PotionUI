from __future__ import annotations

import cv2
import numpy as np


def canny_guide(frame_rgb: np.ndarray, low: float = 100.0, high: float = 200.0) -> np.ndarray:
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, float(low), float(high))
    return np.repeat(edges[:, :, None], 3, axis=2)
