import numpy as np

from src.platform.imaging.filters.lut import apply_cube
from src.platform.imaging.filters.spatial import apply_spatial


def apply(batch, params, derived):
    cube = derived.get("look")
    look = derived.get("@look")
    amount = derived["amount"]
    if cube is None or look is None or amount <= 0:
        return np.array(batch.frames, copy=True)
    return np.stack([
        apply_spatial(apply_cube(frame, cube), list(look.steps), amount * 100, look.ops, look.impls)
        for frame in batch.frames
    ])
