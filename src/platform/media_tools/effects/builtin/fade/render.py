import numpy as np

FULL = 65535


def _fade_in(index: int, count: int) -> int:
    return min(FULL, max(0, index) * (65536 // count))


def _scale(frame: np.ndarray, factor: int) -> np.ndarray:
    if factor >= FULL:
        return frame
    return ((frame.astype(np.int64) * factor + 32768) >> 16).astype(np.uint8)


def fade_numpy(batch, params, derived):
    in_frames, out_frames, out_frame = (int(derived[name]) for name in ("in_frames", "out_frames", "out_frame"))
    out = np.empty_like(batch.frames)
    for slot, index in enumerate(batch.indices):
        frame = batch.frames[slot]
        if in_frames > 0:
            frame = _scale(frame, _fade_in(int(index), in_frames))
        if out_frames > 0 and index >= out_frame:
            frame = _scale(frame, FULL - _fade_in(int(index) - out_frame, out_frames))
        out[slot] = frame
    return out
