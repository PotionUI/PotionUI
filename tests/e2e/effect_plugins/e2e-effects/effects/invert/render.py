import numpy as np


def invert(batch, params, derived):
    values = batch.frames.astype(np.float32)
    k = np.float32(derived["k"])
    return np.floor(values + k * (np.float32(255) - np.float32(2) * values) + np.float32(0.5)).astype(np.uint8)
