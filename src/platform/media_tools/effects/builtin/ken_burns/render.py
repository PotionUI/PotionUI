import numpy as np


def _window(index: int, derived) -> tuple[float, float, float]:
    p = min(max(index / float(derived["span"]), 0.0), 1.0)
    e = p * p * (3 - 2 * p) if derived["smooth"] else p
    z = float(derived["zoom_from"]) + (float(derived["zoom_to"]) - float(derived["zoom_from"])) * e
    hw = 0.5 / z
    x = (float(derived["fx"]) + (float(derived["tx"]) - float(derived["fx"])) * e) * (1 - 2 * hw)
    y = (float(derived["fy"]) + (float(derived["ty"]) - float(derived["fy"])) * e) * (1 - 2 * hw)
    return z, x, y


def _taps(origin: float, count: int, z: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fixed = np.floor((origin + np.arange(count, dtype=np.float64) / z) * 256 + 0.5).astype(np.int64)
    base = fixed >> 8
    return np.clip(base, 0, count - 1), np.clip(base + 1, 0, count - 1), (fixed & 255)


def ken_burns_numpy(batch, params, derived):
    values = {**params, **derived}
    frames = batch.frames
    height, width = frames.shape[1:3]
    out = np.empty_like(frames)
    for slot, index in enumerate(batch.indices):
        z, x, y = _window(int(index), values)
        u0, u1, su = _taps(x * width, width, z)
        v0, v1, sv = _taps(y * height, height, z)
        frame = frames[slot].astype(np.int64)
        top, bottom = frame[v0], frame[v1]
        su = su[None, :, None]
        sv = sv[:, None, None]
        total = (256 - su) * ((256 - sv) * top[:, u0] + sv * bottom[:, u0]) + su * (
            (256 - sv) * top[:, u1] + sv * bottom[:, u1]
        )
        out[slot] = (total + 32768) >> 16
    return out
