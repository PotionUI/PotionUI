import math
from typing import Dict, Optional

import numpy as np

from src.platform.runtime.pose.layout import (
    BODY_PARENT,
    FACE,
    L_ANKLE,
    L_ELBOW,
    L_EYE,
    L_EAR,
    L_HIP,
    L_KNEE,
    L_SHOULDER,
    L_WRIST,
    LEFT_HAND,
    NECK,
    NOSE,
    NUM_POINTS,
    R_ANKLE,
    R_EAR,
    R_ELBOW,
    R_EYE,
    R_HIP,
    R_KNEE,
    R_SHOULDER,
    R_WRIST,
    RIGHT_HAND,
)

DEFAULT_LENGTHS: Dict[int, float] = {
    NOSE: 20.0,
    R_SHOULDER: 24.0, L_SHOULDER: 24.0,
    R_ELBOW: 36.0, L_ELBOW: 36.0,
    R_WRIST: 32.0, L_WRIST: 32.0,
    R_HIP: 82.0, L_HIP: 82.0,
    R_KNEE: 50.0, L_KNEE: 50.0,
    R_ANKLE: 48.0, L_ANKLE: 48.0,
}

DEFAULT_ANGLES: Dict[int, float] = {
    NOSE: -90.0,
    R_SHOULDER: 180.0, L_SHOULDER: 0.0,
    R_ELBOW: 90.0, L_ELBOW: 90.0,
    R_WRIST: 90.0, L_WRIST: 90.0,
    R_HIP: 100.0, L_HIP: 80.0,
    R_KNEE: 90.0, L_KNEE: 90.0,
    R_ANKLE: 90.0, L_ANKLE: 90.0,
}

BONES = (
    NOSE, R_SHOULDER, R_ELBOW, R_WRIST, L_SHOULDER, L_ELBOW, L_WRIST,
    R_HIP, R_KNEE, R_ANKLE, L_HIP, L_KNEE, L_ANKLE,
)


def skeleton(
    neck=(200.0, 100.0),
    lengths: Optional[Dict[int, float]] = None,
    angles: Optional[Dict[int, float]] = None,
    size: float = 1.0,
    score: float = 0.9,
    hands: bool = True,
    face: bool = True,
    hidden: tuple = (),
) -> np.ndarray:
    use_lengths = {**DEFAULT_LENGTHS, **(lengths or {})}
    use_angles = {**DEFAULT_ANGLES, **(angles or {})}
    pose = np.zeros((NUM_POINTS, 3), dtype=np.float32)
    pose[NECK] = (neck[0], neck[1], score)
    for joint in BONES:
        parent = BODY_PARENT[joint]
        radians = math.radians(use_angles[joint])
        pose[joint, 0] = pose[parent, 0] + math.cos(radians) * use_lengths[joint] * size
        pose[joint, 1] = pose[parent, 1] + math.sin(radians) * use_lengths[joint] * size
        pose[joint, 2] = score
    head = use_lengths[NOSE] * size
    for eye, ear, side in ((R_EYE, R_EAR, -1.0), (L_EYE, L_EAR, 1.0)):
        pose[eye] = (pose[NOSE, 0] + side * 0.25 * head, pose[NOSE, 1] - 0.3 * head, score)
        pose[ear] = (pose[NOSE, 0] + side * 0.5 * head, pose[NOSE, 1] - 0.1 * head, score)
    if face:
        for k in range(FACE.stop - FACE.start):
            angle = 2 * math.pi * k / (FACE.stop - FACE.start)
            pose[FACE.start + k] = (
                pose[NOSE, 0] + math.cos(angle) * 1.4 * head,
                pose[NOSE, 1] + math.sin(angle) * 1.4 * head,
                score,
            )
    if hands:
        for hand, wrist in ((LEFT_HAND, L_WRIST), (RIGHT_HAND, R_WRIST)):
            unit = use_lengths[R_WRIST] * size * 0.12
            for k in range(hand.stop - hand.start):
                pose[hand.start + k] = (
                    pose[wrist, 0] + ((k % 5) - 2) * unit,
                    pose[wrist, 1] + (k // 5) * unit,
                    score,
                )
    for index in hidden:
        pose[index, 2] = 0.0
    return pose


def dance(frames: int, base: Optional[np.ndarray] = None, **kwargs) -> np.ndarray:
    poses = []
    for t in range(frames):
        swing = math.sin(t / frames * 2 * math.pi)
        poses.append(skeleton(
            neck=(200.0 + 6.0 * t, 100.0 + 10.0 * swing),
            angles={R_ELBOW: 90.0 + 70.0 * swing, R_WRIST: 90.0 + 130.0 * swing, L_ELBOW: 90.0 - 40.0 * swing},
            **kwargs,
        ))
    return np.stack(poses)


def direction(pose: np.ndarray, child: int) -> np.ndarray:
    vector = pose[child, :2] - pose[BODY_PARENT[child], :2]
    return vector / np.linalg.norm(vector)


def length(pose: np.ndarray, child: int) -> float:
    return float(np.linalg.norm(pose[child, :2] - pose[BODY_PARENT[child], :2]))
