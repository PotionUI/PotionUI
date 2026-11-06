import numpy as np

from src.platform.imaging.filters.lut import apply_cube


def apply(batch, params, derived):
    cube = derived.get("lut")
    if cube is None or derived["amount"] <= 0:
        return np.array(batch.frames, copy=True)
    return np.stack([apply_cube(frame, cube) for frame in batch.frames])
