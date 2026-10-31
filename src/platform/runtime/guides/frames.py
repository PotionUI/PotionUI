from __future__ import annotations

import cv2
import numpy as np

from src.platform.runtime.pose.geometry import fit_transform


def fit_frame(frame: np.ndarray, width: int, height: int, mode: str = "contain") -> np.ndarray:
    source_height, source_width = frame.shape[:2]
    if (source_width, source_height) == (width, height):
        return frame
    scale, _, _ = fit_transform(source_width, source_height, width, height, mode)
    inner_w = max(1, int(round(source_width * scale)))
    inner_h = max(1, int(round(source_height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(frame, (inner_w, inner_h), interpolation=interpolation)
    canvas = np.zeros((height, width) + frame.shape[2:], dtype=frame.dtype)
    src_left = max(0, (inner_w - width) // 2)
    src_top = max(0, (inner_h - height) // 2)
    dst_left = max(0, (width - inner_w) // 2)
    dst_top = max(0, (height - inner_h) // 2)
    copy_w = min(width, inner_w)
    copy_h = min(height, inner_h)
    canvas[dst_top:dst_top + copy_h, dst_left:dst_left + copy_w] = resized[src_top:src_top + copy_h, src_left:src_left + copy_w]
    return canvas
