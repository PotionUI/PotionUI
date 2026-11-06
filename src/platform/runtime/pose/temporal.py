from __future__ import annotations

import math
import numpy as np

from src.platform.runtime.pose.layout import BODY

MIN_BODY_POINTS = 4
ONE_EURO_BETA = 1.5
ONE_EURO_DERIVATIVE_CUTOFF = 1.0


def valid_mask(poses: np.ndarray, min_confidence: float) -> np.ndarray:
    return poses[..., 2] >= min_confidence


def lost_frames(poses: np.ndarray, min_confidence: float) -> np.ndarray:
    return (poses[:, BODY, 2] >= min_confidence).sum(axis=1) < MIN_BODY_POINTS


def fill_gaps(poses: np.ndarray, min_confidence: float, max_gap: int) -> np.ndarray:
    out = poses.copy()
    valid = valid_mask(poses, min_confidence)
    count = poses.shape[0]
    frames = np.arange(count)
    for point in range(poses.shape[1]):
        ok = valid[:, point]
        if ok.all() or not ok.any():
            continue
        anchors = frames[ok]
        missing = frames[~ok]
        right = np.searchsorted(anchors, missing)
        has_both = (right > 0) & (right < len(anchors))
        left_idx = anchors[np.clip(right - 1, 0, len(anchors) - 1)]
        right_idx = anchors[np.clip(right, 0, len(anchors) - 1)]
        fill = has_both & ((right_idx - left_idx - 1) <= max_gap)
        for t, lo, hi in zip(missing[fill], left_idx[fill], right_idx[fill]):
            weight = (t - lo) / (hi - lo)
            out[t, point, :2] = poses[lo, point, :2] * (1 - weight) + poses[hi, point, :2] * weight
            out[t, point, 2] = min(poses[lo, point, 2], poses[hi, point, 2])
    return out


def hold_lost(poses: np.ndarray, min_confidence: float) -> np.ndarray:
    lost = lost_frames(poses, min_confidence)
    if lost.all():
        raise ValueError("no person found in any frame")
    out = poses.copy()
    good = np.flatnonzero(~lost)
    for t in np.flatnonzero(lost):
        pos = np.searchsorted(good, t)
        source = good[pos - 1] if pos > 0 else good[0]
        out[t] = out[source]
    return out


def person_scale(poses: np.ndarray, min_confidence: float) -> float:
    sizes = []
    for pose in poses:
        valid = pose[BODY, 2] >= min_confidence
        if valid.sum() >= MIN_BODY_POINTS:
            points = pose[BODY][valid, :2]
            sizes.append(float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))))
    return float(np.median(sizes)) if sizes else 1.0


class OneEuroBank:
    def __init__(self, points: int, min_cutoff: float, beta: float, derivative_cutoff: float, dt: float, scale: float):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.dt = dt
        self.scale = scale
        self.derivative_alpha = self._alpha(derivative_cutoff)
        self.value = np.zeros((points, 2), dtype=np.float64)
        self.derivative = np.zeros((points, 2), dtype=np.float64)
        self.seeded = np.zeros(points, dtype=bool)

    def _alpha(self, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / self.dt)

    def step(self, sample: np.ndarray, valid: np.ndarray) -> np.ndarray:
        fresh = valid & ~self.seeded
        self.value[fresh] = sample[fresh]
        self.derivative[fresh] = 0.0
        self.seeded |= fresh
        follow = valid & ~fresh
        if follow.any():
            speed = (sample[follow] - self.value[follow]) / self.dt
            derivative = self.derivative[follow] + self.derivative_alpha * (speed - self.derivative[follow])
            cutoff = self.min_cutoff + self.beta * np.abs(derivative) / self.scale
            alpha = self._alpha(cutoff)
            self.derivative[follow] = derivative
            self.value[follow] = self.value[follow] + alpha * (sample[follow] - self.value[follow])
        return self.value.copy()


def _one_euro_pass(poses: np.ndarray, valid: np.ndarray, fps: float, strength: float, scale: float) -> np.ndarray:
    bank = OneEuroBank(
        poses.shape[1], 4.0 * (0.1 ** strength), ONE_EURO_BETA, ONE_EURO_DERIVATIVE_CUTOFF,
        1.0 / max(fps, 1.0), scale,
    )
    return np.stack([bank.step(poses[t, :, :2].astype(np.float64), valid[t]) for t in range(poses.shape[0])])


def smooth(poses: np.ndarray, fps: float, strength: float, min_confidence: float) -> np.ndarray:
    if strength <= 0.0 or poses.shape[0] < 2:
        return poses
    strength = min(float(strength), 1.0)
    valid = valid_mask(poses, min_confidence)
    scale = max(person_scale(poses, min_confidence), 1.0)
    forward = _one_euro_pass(poses, valid, fps, strength, scale)
    backward = _one_euro_pass(poses[::-1], valid[::-1], fps, strength, scale)[::-1]
    blended = ((forward + backward) * 0.5).astype(np.float32)
    out = poses.copy()
    out[..., :2] = np.where(valid[..., None], blended, poses[..., :2])
    return out
