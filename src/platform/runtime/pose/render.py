# Derived from: https://github.com/IDEA-Research/DWPose (Apache-2.0), annotator/dwpose/util.py
# Derived from: https://github.com/aigc-apps/VideoX-Fun (Apache-2.0), comfyui/annotator/dwpose_utils/util.py

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from typing import Tuple

import cv2
import numpy as np

from src.platform.runtime.pose.layout import (
    BODY,
    BODY_COLORS,
    BODY_LIMBS,
    FACE,
    HAND_EDGES,
    L_EYE,
    LEFT_HAND,
    R_EYE,
    RIGHT_HAND,
)

STICK_WIDTH = 4
JOINT_RADIUS = 4
FACE_RADIUS = 3
HAND_THICKNESS = 2
LIMB_DIM = 0.6
HAND_DOT = (0, 0, 255)
FACE_DOT = (255, 255, 255)
REFERENCE_SHORT_SIDE = 512
COORD_LIMIT = 100000.0

HAND_COLORS = tuple(
    tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(i / float(len(HAND_EDGES)), 1.0, 1.0))
    for i in range(len(HAND_EDGES))
)


@dataclass(frozen=True)
class RenderOptions:
    body: bool = True
    hands: bool = True
    face: bool = True
    min_confidence: float = 0.3
    detect_resolution: int = REFERENCE_SHORT_SIDE
    line_scale: float = 1.0

    def scale_for(self, width: int, height: int) -> float:
        return min(width, height) / float(self.detect_resolution) * self.line_scale


def _px(point: np.ndarray) -> Tuple[int, int]:
    clipped = np.clip(point, -COORD_LIMIT, COORD_LIMIT)
    return int(clipped[0]), int(clipped[1])


def _width(base: int, scale: float) -> int:
    return max(1, int(round(base * scale)))


def draw_body(canvas: np.ndarray, pose: np.ndarray, valid: np.ndarray, scale: float) -> None:
    stick = _width(STICK_WIDTH, scale)
    radius = _width(JOINT_RADIUS, scale)
    for (a, b), color in zip(BODY_LIMBS, BODY_COLORS):
        if not (valid[a] and valid[b]):
            continue
        ax, ay = np.clip(pose[a, :2], -COORD_LIMIT, COORD_LIMIT)
        bx, by = np.clip(pose[b, :2], -COORD_LIMIT, COORD_LIMIT)
        length = math.hypot(ay - by, ax - bx)
        angle = math.degrees(math.atan2(ay - by, ax - bx))
        polygon = cv2.ellipse2Poly(
            (int((ax + bx) / 2), int((ay + by) / 2)), (int(length / 2), stick), int(angle), 0, 360, 1,
        )
        cv2.fillConvexPoly(canvas, polygon, [int(c * LIMB_DIM) for c in color])
    for index in range(BODY.stop):
        if valid[index]:
            cv2.circle(canvas, _px(pose[index, :2]), radius, BODY_COLORS[index], thickness=-1)


def draw_hand(canvas: np.ndarray, pose: np.ndarray, valid: np.ndarray, scale: float) -> None:
    if not valid.any():
        return
    thickness = _width(HAND_THICKNESS, scale)
    radius = _width(JOINT_RADIUS, scale)
    for (a, b), color in zip(HAND_EDGES, HAND_COLORS):
        if valid[a] and valid[b]:
            cv2.line(canvas, _px(pose[a, :2]), _px(pose[b, :2]), color, thickness=thickness)
    for index in np.flatnonzero(valid):
        cv2.circle(canvas, _px(pose[index, :2]), radius, HAND_DOT, thickness=-1)


def draw_face(canvas: np.ndarray, pose: np.ndarray, valid: np.ndarray, body_pose: np.ndarray, body_valid: np.ndarray, scale: float) -> None:
    if not valid.any():
        return
    radius = _width(FACE_RADIUS, scale)
    for index in np.flatnonzero(valid):
        cv2.circle(canvas, _px(pose[index, :2]), radius, FACE_DOT, thickness=-1)
    for eye in (R_EYE, L_EYE):
        if body_valid[eye]:
            cv2.circle(canvas, _px(body_pose[eye, :2]), radius, FACE_DOT, thickness=-1)


def render_pose(pose: np.ndarray, width: int, height: int, options: RenderOptions = RenderOptions()) -> np.ndarray:
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    valid = pose[:, 2] >= options.min_confidence
    scale = options.scale_for(width, height)
    if options.body:
        draw_body(canvas, pose, valid[BODY], scale)
    if options.hands:
        draw_hand(canvas, pose[LEFT_HAND], valid[LEFT_HAND], scale)
        draw_hand(canvas, pose[RIGHT_HAND], valid[RIGHT_HAND], scale)
    if options.face:
        draw_face(canvas, pose[FACE], valid[FACE], pose[BODY], valid[BODY], scale)
    return canvas
