from typing import Sequence, Tuple

import numpy as np
from scipy import ndimage

from src.features.editor_tasks.regions import LANDED

MAX_PARTS = 16

Box = Tuple[int, int, int, int]


class Components:
    def __init__(self, region: np.ndarray):
        labels, count = ndimage.label(region >= LANDED)
        found = ndimage.find_objects(labels)
        ordered = sorted(range(count), key=lambda index: (found[index][0].start, found[index][1].start, index))
        self._labels = labels
        self._order = [index + 1 for index in ordered]
        self._found = [found[index] for index in ordered]

    @property
    def count(self) -> int:
        return len(self._order)

    def mask(self, index: int) -> np.ndarray:
        return np.where(self._labels == self._order[index], 255, 0).astype(np.uint8)

    def box(self, index: int) -> Box:
        rows, columns = self._found[index]
        return rows.start, rows.stop, columns.start, columns.stop


def union_box(boxes: Sequence[Box]) -> Box:
    return (
        min(box[0] for box in boxes),
        max(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def compose_patches(placed: Sequence[Tuple[Box, np.ndarray]], union: Box) -> np.ndarray:
    top, bottom, left, right = union
    colour = np.zeros((bottom - top, right - left, 3), dtype=np.float32)
    alpha = np.zeros((bottom - top, right - left), dtype=np.float32)
    for (p_top, p_bottom, p_left, p_right), patch in placed:
        window = (slice(p_top - top, p_bottom - top), slice(p_left - left, p_right - left))
        fresh = patch[..., 3].astype(np.float32) / 255.0
        rgb = patch[..., :3].astype(np.float32)
        held = alpha[window]
        combined = fresh + held * (1.0 - fresh)
        safe = np.maximum(combined, 1e-6)
        colour[window] = (rgb * fresh[..., None] + colour[window] * (held * (1.0 - fresh))[..., None]) / safe[..., None]
        alpha[window] = combined
    out = np.empty((bottom - top, right - left, 4), dtype=np.uint8)
    out[..., :3] = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    return out
