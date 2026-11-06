from __future__ import annotations

import numpy as np

HIST_BINS = 16
BLOCK = 4
LOCAL_RADIUS = 12
LOCAL_FLOOR = 0.02
MIN_SENSITIVITY = 0.0
MAX_SENSITIVITY = 100.0


def frame_features(frames: np.ndarray) -> np.ndarray:
    count, height, width, _ = frames.shape
    data = np.asarray(frames)
    offsets = (np.arange(count, dtype=np.int64) * HIST_BINS)[:, None]
    hists = np.empty((count, 3 * HIST_BINS), dtype=np.float32)
    flat = data.reshape(count, -1, 3)
    position = np.clip(flat.astype(np.float32) * (HIST_BINS / 256.0) - 0.5, 0.0, HIST_BINS - 1.0)
    low = np.floor(position).astype(np.int64)
    high_weight = position - low
    high = np.minimum(low + 1, HIST_BINS - 1)
    for channel in range(3):
        weights = high_weight[..., channel].ravel()
        total = count * HIST_BINS
        counts = np.bincount((low[..., channel] + offsets).ravel(), weights=1.0 - weights, minlength=total)
        counts += np.bincount((high[..., channel] + offsets).ravel(), weights=weights, minlength=total)
        hists[:, channel * HIST_BINS:(channel + 1) * HIST_BINS] = counts.reshape(count, HIST_BINS) / flat.shape[1]
    luma = data.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32) / 255.0
    cropped_h, cropped_w = height - height % BLOCK, width - width % BLOCK
    blocks = luma[:, :cropped_h, :cropped_w].reshape(count, cropped_h // BLOCK, BLOCK, cropped_w // BLOCK, BLOCK)
    thumbs = blocks.mean(axis=(2, 4)).reshape(count, -1)
    return np.concatenate([hists, thumbs.astype(np.float32)], axis=1)


def transition_scores(features: np.ndarray) -> np.ndarray:
    scores = np.zeros(features.shape[0], dtype=np.float64)
    if features.shape[0] < 2:
        return scores
    hist_part = features[:, : 3 * HIST_BINS].astype(np.float64)
    thumb_part = features[:, 3 * HIST_BINS:].astype(np.float64)
    hist_delta = np.abs(np.diff(hist_part, axis=0)).sum(axis=1) / 6.0
    thumb_delta = np.abs(np.diff(thumb_part, axis=0)).mean(axis=1)
    scores[1:] = 0.5 * hist_delta + 0.5 * thumb_delta
    return scores


def thresholds_for(sensitivity: float) -> tuple[float, float]:
    amount = min(MAX_SENSITIVITY, max(MIN_SENSITIVITY, sensitivity)) / MAX_SENSITIVITY
    floor = 0.35 - 0.27 * amount
    ratio = 6.0 - 3.0 * amount
    return floor, ratio


def find_cuts(
    features: np.ndarray,
    fps: float,
    sensitivity: float = 50.0,
    min_shot_seconds: float = 0.5,
) -> list[int]:
    scores = transition_scores(features)
    count = scores.shape[0]
    floor, ratio = thresholds_for(sensitivity)
    candidates: list[int] = []
    for index in range(1, count):
        score = scores[index]
        if score < floor:
            continue
        low, high = max(1, index - LOCAL_RADIUS), min(count, index + LOCAL_RADIUS + 1)
        neighbours = np.concatenate([scores[low:index], scores[index + 1:high]])
        local = float(neighbours.mean()) if neighbours.size else 0.0
        if score > ratio * max(local, LOCAL_FLOOR):
            candidates.append(index)

    min_gap = max(1, int(round(min_shot_seconds * fps)))
    accepted: list[int] = []
    for index in candidates:
        if accepted and index - accepted[-1] < min_gap:
            if scores[index] > scores[accepted[-1]]:
                accepted[-1] = index
            continue
        accepted.append(index)
    return [index for index in accepted if index >= min_gap and count - index >= min_gap]
