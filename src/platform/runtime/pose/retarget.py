from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.platform.runtime.pose.layout import (
    BODY,
    BODY_PARENT,
    FACE,
    HEAD_POINTS,
    L_EAR,
    L_EYE,
    L_WRIST,
    LEFT_HAND,
    NECK,
    NOSE,
    NUM_POINTS,
    R_EAR,
    R_EYE,
    R_WRIST,
    RIGHT_HAND,
    SYMMETRIC_BONES,
)

REFERENCE_PERCENTILE = 85.0
SCALE_FLOOR = 0.6
SCALE_CEILING = 1.6
MIN_SAMPLES = 3
EPS = 1e-6
MAJOR_BONES = tuple(
    bone for pair in SYMMETRIC_BONES[1:] for bone in pair
) + ((NOSE, NECK),)
HEAD_PAIRS = ((R_EAR, L_EAR), (R_EYE, L_EYE))
COUNTERPART = {a: b for left, right in SYMMETRIC_BONES for a, b in ((left, right), (right, left))}


class RetargetError(ValueError):
    pass


@dataclass
class RetargetResult:
    poses: np.ndarray
    scale: float
    bone_scales: Dict[Tuple[int, int], float]
    head_scale: float


def _bone_order() -> List[int]:
    order: List[int] = []
    pending = [NECK]
    while pending:
        node = pending.pop(0)
        for child, parent in BODY_PARENT.items():
            if parent == node:
                order.append(child)
                pending.append(child)
    return order


BONE_ORDER = _bone_order()


def reference_lengths(poses: np.ndarray, pairs, min_confidence: float) -> Dict[Tuple[int, int], float]:
    valid = poses[..., 2] >= min_confidence
    lengths: Dict[Tuple[int, int], float] = {}
    for child, parent in pairs:
        both = valid[:, child] & valid[:, parent]
        if both.sum() < MIN_SAMPLES:
            continue
        spans = np.linalg.norm(poses[both, child, :2] - poses[both, parent, :2], axis=1)
        lengths[(child, parent)] = float(np.percentile(spans, REFERENCE_PERCENTILE))
    return lengths


def subject_lengths(subject: np.ndarray, min_confidence: float) -> Dict[Tuple[int, int], float]:
    valid = subject[:, 2] >= min_confidence
    lengths: Dict[Tuple[int, int], float] = {}
    for child, parent in BODY_PARENT.items():
        if valid[child] and valid[parent]:
            lengths[(child, parent)] = float(np.linalg.norm(subject[child, :2] - subject[parent, :2]))
    for left, right in SYMMETRIC_BONES:
        present = [lengths[bone] for bone in (left, right) if bone in lengths]
        if len(present) == 2:
            lengths[left] = lengths[right] = max(present)
    return lengths


def pair_span(poses: np.ndarray, a: int, b: int, min_confidence: float, min_samples: int) -> Optional[float]:
    valid = poses[..., 2] >= min_confidence
    both = valid[:, a] & valid[:, b]
    if both.sum() < min_samples:
        return None
    spans = np.linalg.norm(poses[both, a, :2] - poses[both, b, :2], axis=1)
    return float(np.percentile(spans, REFERENCE_PERCENTILE))


def head_ratio(driver: np.ndarray, subject: np.ndarray, min_confidence: float) -> Optional[float]:
    for a, b in HEAD_PAIRS:
        driver_span = pair_span(driver, a, b, min_confidence, MIN_SAMPLES)
        subject_span = pair_span(subject[None], a, b, min_confidence, 1)
        if driver_span and subject_span and driver_span > EPS:
            return subject_span / driver_span
    return None


def _clip(value: float, anchor: float) -> float:
    return float(np.clip(value, anchor * SCALE_FLOOR, anchor * SCALE_CEILING))


def compute_scales(
    driver: np.ndarray, subject: np.ndarray, min_confidence: float,
) -> Tuple[float, Dict[Tuple[int, int], float], float]:
    driver_ref = reference_lengths(driver, list(BODY_PARENT.items()), min_confidence)
    subject_len = subject_lengths(subject, min_confidence)
    ratios = {
        bone: subject_len[bone] / driver_ref[bone]
        for bone in BODY_PARENT.items()
        if bone in subject_len and driver_ref.get(bone, 0.0) > EPS and subject_len[bone] > EPS
    }
    major = [ratios[bone] for bone in MAJOR_BONES if bone in ratios]
    pool = major or list(ratios.values())
    head_value = head_ratio(driver, subject, min_confidence)
    if pool:
        overall = float(np.median(pool))
    elif head_value is not None:
        overall = head_value
    else:
        raise RetargetError("the picture shows too little of the person to scale the motion")
    scales: Dict[Tuple[int, int], float] = {}
    for bone in BODY_PARENT.items():
        if bone in ratios:
            scales[bone] = _clip(ratios[bone], overall)
            continue
        counterpart = COUNTERPART.get(bone)
        scales[bone] = _clip(ratios[counterpart], overall) if counterpart in ratios else overall
    head = _clip(head_value, overall) if head_value is not None else overall
    return overall, scales, head


def _anchor(subject: np.ndarray, driver_first: np.ndarray, overall: float, min_confidence: float) -> np.ndarray:
    valid = subject[:, 2] >= min_confidence
    if valid[NECK]:
        return subject[NECK, :2].astype(np.float64)
    body_valid = valid[BODY]
    if not body_valid.any():
        raise RetargetError("no person found in the picture")
    subject_mid = subject[BODY][body_valid, :2].mean(axis=0)
    dvalid = driver_first[BODY, 2] >= min_confidence
    driver_mid = driver_first[BODY][dvalid, :2].mean(axis=0)
    return subject_mid + (driver_first[NECK, :2] - driver_mid) * overall


def nearest_placed(child: int, placed: np.ndarray) -> Optional[int]:
    node = child
    while node in BODY_PARENT:
        node = BODY_PARENT[node]
        if placed[node]:
            return node
    return None


def retarget_frame(
    driver_t: np.ndarray,
    neck_out: np.ndarray,
    scales: Dict[Tuple[int, int], float],
    head: float,
    overall: float,
    min_confidence: float,
) -> np.ndarray:
    out = np.zeros((NUM_POINTS, 3), dtype=np.float32)
    valid = driver_t[:, 2] >= min_confidence
    placed = np.zeros(NUM_POINTS, dtype=bool)
    if valid[NECK]:
        out[NECK] = (neck_out[0], neck_out[1], driver_t[NECK, 2])
        placed[NECK] = True
    for child in BONE_ORDER:
        if not valid[child]:
            continue
        node = nearest_placed(child, placed)
        if node is None:
            continue
        scale = scales[(child, node)] if node == BODY_PARENT[child] else overall
        out[child, :2] = out[node, :2] + (driver_t[child, :2] - driver_t[node, :2]) * scale
        out[child, 2] = driver_t[child, 2]
        placed[child] = True

    if placed[NOSE]:
        head_anchor = NOSE
    elif placed[NECK]:
        head_anchor = NECK
    else:
        head_anchor = None
    if head_anchor is not None:
        indices = list(HEAD_POINTS) + list(range(FACE.start, FACE.stop))
        for index in indices:
            if valid[index]:
                out[index, :2] = out[head_anchor, :2] + (driver_t[index, :2] - driver_t[head_anchor, :2]) * head
                out[index, 2] = driver_t[index, 2]

    for hand, wrist in ((LEFT_HAND, L_WRIST), (RIGHT_HAND, R_WRIST)):
        if not placed[wrist]:
            continue
        forearm = scales[(wrist, BODY_PARENT[wrist])]
        base = hand.start
        origin = driver_t[base, :2] if valid[base] else driver_t[wrist, :2]
        for index in range(hand.start, hand.stop):
            if valid[index]:
                out[index, :2] = out[wrist, :2] + (driver_t[index, :2] - origin) * forearm
                out[index, 2] = driver_t[index, 2]
    return out


def ease_weights(count: int, frames: int) -> np.ndarray:
    weights = np.ones(count, dtype=np.float32)
    if frames > 0:
        ramp = np.clip(np.arange(min(frames, count), dtype=np.float32) / float(frames), 0.0, 1.0)
        weights[: len(ramp)] = ramp * ramp * (3.0 - 2.0 * ramp)
    return weights


def retarget_sequence(
    driver: np.ndarray,
    subject: np.ndarray,
    min_confidence: float = 0.3,
    ease_in_frames: int = 0,
) -> RetargetResult:
    dvalid = driver[:, NECK, 2] >= min_confidence
    if not dvalid.any():
        raise RetargetError("no usable neck in the driving video")
    first = int(np.flatnonzero(dvalid)[0])
    overall, scales, head = compute_scales(driver, subject, min_confidence)
    anchor = _anchor(subject, driver[first], overall, min_confidence)
    out = np.zeros_like(driver)
    for t in range(driver.shape[0]):
        neck_out = anchor + (driver[t, NECK, :2] - driver[first, NECK, :2]) * overall
        out[t] = retarget_frame(driver[t], neck_out, scales, head, overall, min_confidence)
    if ease_in_frames > 0:
        weights = ease_weights(driver.shape[0], ease_in_frames)
        svalid = subject[:, 2] >= min_confidence
        for t in np.flatnonzero(weights < 1.0):
            ovalid = out[t, :, 2] >= min_confidence
            both = ovalid & svalid
            blend = out[t].copy()
            blend[both, :2] = weights[t] * out[t, both, :2] + (1.0 - weights[t]) * subject[both, :2]
            only_subject = svalid & ~ovalid & (weights[t] < 0.5)
            blend[only_subject] = subject[only_subject]
            out[t] = blend
    return RetargetResult(poses=out, scale=overall, bone_scales=scales, head_scale=head)
