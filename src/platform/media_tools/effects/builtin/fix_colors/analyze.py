from __future__ import annotations

import numpy as np

from src.platform.media_tools.effects.api import AnalysisContext, FrameBatch, estimate_color_fix

LEVELS = np.arange(256, dtype=np.float64)


def estimate(ctx: AnalysisContext, params: dict) -> dict:
    frames = ctx.sample_frames(width=96, height=54, rate=2.0, max_frames=120)
    fix = estimate_color_fix(frames, white_balance=params["white_balance"], contrast=params["contrast"])
    red, green, blue = fix.gains
    return {"gain_r": red, "gain_g": green, "gain_b": blue, "black": fix.black, "white": fix.white}


def apply(batch: FrameBatch, params: dict, derived: dict) -> np.ndarray:
    mixed = np.empty_like(batch.frames)
    for channel, name in enumerate(("r", "g", "b")):
        table = np.clip(np.rint(LEVELS * derived[name]), 0, 255).astype(np.uint8)
        mixed[..., channel] = table[batch.frames[..., channel]]
    low, high = int(np.rint(derived["lo"] * 255)), int(np.rint(derived["hi"] * 255))
    if high == low:
        return mixed
    coefficient = np.float32(255 / (high - low))
    levelled = (mixed.astype(np.float32) - np.float32(low)) * coefficient
    return np.clip(np.trunc(levelled), 0, 255).astype(np.uint8)
