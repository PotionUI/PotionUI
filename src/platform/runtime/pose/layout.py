import numpy as np

NUM_POINTS = 134

BODY = slice(0, 18)
FEET = slice(18, 24)
FACE = slice(24, 92)
LEFT_HAND = slice(92, 113)
RIGHT_HAND = slice(113, 134)

(
    NOSE, NECK, R_SHOULDER, R_ELBOW, R_WRIST, L_SHOULDER, L_ELBOW, L_WRIST,
    R_HIP, R_KNEE, R_ANKLE, L_HIP, L_KNEE, L_ANKLE, R_EYE, L_EYE, R_EAR, L_EAR,
) = range(18)

COCO_TO_OPENPOSE_SOURCE = (17, 6, 8, 10, 7, 9, 12, 14, 16, 13, 15, 2, 1, 4, 3)
COCO_TO_OPENPOSE_TARGET = (1, 2, 3, 4, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17)

BODY_LIMBS = (
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
    (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17),
)

BODY_COLORS = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0),
    (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255),
    (0, 0, 255), (85, 0, 255), (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85),
)

HAND_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (0, 9), (9, 10),
    (10, 11), (11, 12), (0, 13), (13, 14), (14, 15), (15, 16), (0, 17), (17, 18),
    (18, 19), (19, 20),
)

BODY_PARENT = {
    NOSE: NECK,
    R_SHOULDER: NECK, R_ELBOW: R_SHOULDER, R_WRIST: R_ELBOW,
    L_SHOULDER: NECK, L_ELBOW: L_SHOULDER, L_WRIST: L_ELBOW,
    R_HIP: NECK, R_KNEE: R_HIP, R_ANKLE: R_KNEE,
    L_HIP: NECK, L_KNEE: L_HIP, L_ANKLE: L_KNEE,
}

HEAD_POINTS = (R_EYE, L_EYE, R_EAR, L_EAR)

SYMMETRIC_BONES = (
    ((R_SHOULDER, NECK), (L_SHOULDER, NECK)),
    ((R_ELBOW, R_SHOULDER), (L_ELBOW, L_SHOULDER)),
    ((R_WRIST, R_ELBOW), (L_WRIST, L_ELBOW)),
    ((R_HIP, NECK), (L_HIP, NECK)),
    ((R_KNEE, R_HIP), (L_KNEE, L_HIP)),
    ((R_ANKLE, R_KNEE), (L_ANKLE, L_KNEE)),
)


def empty_pose() -> np.ndarray:
    return np.zeros((NUM_POINTS, 3), dtype=np.float32)


def coco_wholebody_to_openpose(keypoints: np.ndarray, scores: np.ndarray, neck_threshold: float = 0.3) -> np.ndarray:
    count = keypoints.shape[0]
    info = np.concatenate([keypoints, scores[..., None]], axis=-1).astype(np.float32)
    neck = info[:, [5, 6]].mean(axis=1)
    neck[:, 2] = np.logical_and(info[:, 5, 2] > neck_threshold, info[:, 6, 2] > neck_threshold).astype(np.float32)
    expanded = np.insert(info, 17, neck, axis=1)
    expanded[:, list(COCO_TO_OPENPOSE_TARGET)] = expanded[:, list(COCO_TO_OPENPOSE_SOURCE)]
    if expanded.shape[1] != NUM_POINTS:
        raise ValueError(f"expected 133 whole-body keypoints, got {expanded.shape[1] - 1}")
    return expanded.reshape(count, NUM_POINTS, 3)
