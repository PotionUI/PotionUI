# Derived from: https://github.com/IDEA-Research/DWPose (Apache-2.0), onnx branch, annotator/dwpose/onnxdet.py, onnxpose.py, wholebody.py

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from src.platform.runtime.pose.layout import coco_wholebody_to_openpose

DETECTOR_FILENAME = "yolox_l.onnx"
POSE_FILENAME = "dw-ll_ucoco_384.onnx"
DWPOSE_REPO = "yzd-v/DWPose"

WEIGHT_FILES = {
    DETECTOR_FILENAME: (216746733, "7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411"),
    POSE_FILENAME: (134399116, "724f4ff2439ed61afb86fb8a1951ec39c6220682803b4a8bd4f598cd913b1843"),
}
_verified: set = set()

DETECTOR_INPUT = (640, 640)
DETECTOR_NMS = 0.45
DETECTOR_PRE_SCORE = 0.1
DETECTOR_SCORE = 0.3
POSE_BBOX_PADDING = 1.25
POSE_DEFAULT_SIZE = (288, 384)
SIMCC_SPLIT_RATIO = 2.0
POSE_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
POSE_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

SessionFactory = Callable[[str], Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_weights(directory: Path) -> None:
    for name, (size, sha256) in WEIGHT_FILES.items():
        path = Path(directory) / name
        if not path.is_file():
            raise ValueError(f"DWPose weights not found: {path}")
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if stamp in _verified:
            continue
        if stat.st_size != size or _sha256(path) != sha256:
            raise ValueError(f"{name} is incomplete or corrupted; delete {path} and run again")
        _verified.add(stamp)


def create_onnx_session(path: str) -> Any:
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    providers = [name for name in ("CUDAExecutionProvider",) if name in available] + ["CPUExecutionProvider"]
    return ort.InferenceSession(str(path), providers=providers)


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> List[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        overlap = inter / (areas[i] + areas[rest] - inter)
        order = rest[np.where(overlap <= threshold)[0]]
    return keep


def yolox_grid_decode(outputs: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
    grids = []
    strides_out = []
    for stride in (8, 16, 32):
        h, w = size[0] // stride, size[1] // stride
        xv, yv = np.meshgrid(np.arange(w), np.arange(h))
        grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
        grids.append(grid)
        strides_out.append(np.full((*grid.shape[:2], 1), stride))
    grid_all = np.concatenate(grids, 1)
    stride_all = np.concatenate(strides_out, 1)
    decoded = outputs.copy()
    decoded[..., :2] = (decoded[..., :2] + grid_all) * stride_all
    decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * stride_all
    return decoded


def letterbox(image: np.ndarray, size: Tuple[int, int]) -> Tuple[np.ndarray, float]:
    canvas = np.full((size[0], size[1], 3), 114, dtype=np.uint8)
    ratio = min(size[0] / image.shape[0], size[1] / image.shape[1])
    resized = cv2.resize(
        image, (int(image.shape[1] * ratio), int(image.shape[0] * ratio)), interpolation=cv2.INTER_LINEAR
    )
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    return np.ascontiguousarray(canvas.transpose(2, 0, 1), dtype=np.float32), ratio


class PersonDetector:
    def __init__(self, session: Any):
        self.session = session

    def __call__(self, bgr: np.ndarray) -> np.ndarray:
        tensor, ratio = letterbox(bgr, DETECTOR_INPUT)
        name = self.session.get_inputs()[0].name
        raw = self.session.run(None, {name: tensor[None]})[0]
        predictions = yolox_grid_decode(np.asarray(raw, dtype=np.float32), DETECTOR_INPUT)[0]
        centers = predictions[:, :4]
        person = predictions[:, 4] * predictions[:, 5]
        boxes = np.empty_like(centers)
        boxes[:, 0] = centers[:, 0] - centers[:, 2] / 2.0
        boxes[:, 1] = centers[:, 1] - centers[:, 3] / 2.0
        boxes[:, 2] = centers[:, 0] + centers[:, 2] / 2.0
        boxes[:, 3] = centers[:, 1] + centers[:, 3] / 2.0
        boxes /= ratio
        candidates = person > DETECTOR_PRE_SCORE
        if not candidates.any():
            return np.zeros((0, 5), dtype=np.float32)
        boxes, person = boxes[candidates], person[candidates]
        keep = nms(boxes, person, DETECTOR_NMS)
        boxes, person = boxes[keep], person[keep]
        confident = person > DETECTOR_SCORE
        result = np.concatenate([boxes[confident], person[confident, None]], axis=1)
        return result.astype(np.float32)


def box_center_scale(box: Sequence[float], padding: float = POSE_BBOX_PADDING) -> Tuple[np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = (float(v) for v in box[:4])
    center = np.array([(x0 + x1) * 0.5, (y0 + y1) * 0.5], dtype=np.float32)
    scale = np.array([(x1 - x0) * padding, (y1 - y0) * padding], dtype=np.float32)
    return center, scale


def fix_aspect_ratio(scale: np.ndarray, aspect: float) -> np.ndarray:
    w, h = float(scale[0]), float(scale[1])
    if w > h * aspect:
        return np.array([w, w / aspect], dtype=np.float32)
    return np.array([h * aspect, h], dtype=np.float32)


def _third_point(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    direction = a - b
    return b + np.array([-direction[1], direction[0]], dtype=np.float32)


def warp_matrix(center: np.ndarray, scale: np.ndarray, size: Tuple[int, int], inverse: bool = False) -> np.ndarray:
    dst_w, dst_h = size
    src = np.zeros((3, 2), dtype=np.float32)
    src[0] = center
    src[1] = center + np.array([0.0, scale[0] * -0.5], dtype=np.float32)
    src[2] = _third_point(src[0], src[1])
    dst = np.zeros((3, 2), dtype=np.float32)
    dst[0] = [dst_w * 0.5, dst_h * 0.5]
    dst[1] = dst[0] + np.array([0.0, dst_w * -0.5], dtype=np.float32)
    dst[2] = _third_point(dst[0], dst[1])
    if inverse:
        return cv2.getAffineTransform(dst, src)
    return cv2.getAffineTransform(src, dst)


def simcc_decode(simcc_x: np.ndarray, simcc_y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n, k, _ = simcc_x.shape
    flat_x = simcc_x.reshape(n * k, -1)
    flat_y = simcc_y.reshape(n * k, -1)
    locs = np.stack((np.argmax(flat_x, axis=1), np.argmax(flat_y, axis=1)), axis=-1).astype(np.float32)
    values = np.minimum(np.amax(flat_x, axis=1), np.amax(flat_y, axis=1))
    locs[values <= 0.0] = -1
    return (locs / SIMCC_SPLIT_RATIO).reshape(n, k, 2), values.reshape(n, k)


class PoseModel:
    def __init__(self, session: Any):
        self.session = session
        shape = session.get_inputs()[0].shape
        height, width = (shape[2], shape[3]) if len(shape) == 4 else (None, None)
        if not isinstance(height, int) or not isinstance(width, int):
            width, height = POSE_DEFAULT_SIZE
        self.input_size = (int(width), int(height))

    def __call__(self, bgr: np.ndarray, box: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
        width, height = self.input_size
        center, scale = box_center_scale(box)
        scale = fix_aspect_ratio(scale, width / height)
        matrix = warp_matrix(center, scale, self.input_size)
        crop = cv2.warpAffine(bgr, matrix, (width, height), flags=cv2.INTER_LINEAR)
        tensor = ((crop.astype(np.float32) - POSE_MEAN) / POSE_STD).transpose(2, 0, 1)[None]
        name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {name: np.ascontiguousarray(tensor, dtype=np.float32)})
        locs, scores = simcc_decode(np.asarray(outputs[0]), np.asarray(outputs[1]))
        points = locs[0] / np.array([width, height], dtype=np.float32) * scale + center - scale / 2
        return points.astype(np.float32), scores[0].astype(np.float32)


@dataclass
class DWPoseEstimator:
    detector: PersonDetector
    pose: PoseModel
    neck_threshold: float = 0.3

    @classmethod
    def from_files(
        cls, directory: Path, session_factory: Optional[SessionFactory] = None,
    ) -> "DWPoseEstimator":
        factory = session_factory or create_onnx_session
        detector_path = Path(directory) / DETECTOR_FILENAME
        pose_path = Path(directory) / POSE_FILENAME
        for path in (detector_path, pose_path):
            if session_factory is None and not path.is_file():
                raise ValueError(f"DWPose weights not found: {path}")
        return cls(PersonDetector(factory(str(detector_path))), PoseModel(factory(str(pose_path))))

    def detect(self, rgb: np.ndarray) -> np.ndarray:
        return self.detector(np.ascontiguousarray(rgb[:, :, ::-1]))

    def estimate(self, rgb: np.ndarray, box: Sequence[float]) -> np.ndarray:
        points, scores = self.pose(np.ascontiguousarray(rgb[:, :, ::-1]), box)
        return coco_wholebody_to_openpose(points[None], scores[None], self.neck_threshold)[0]

    def unload(self) -> None:
        self.detector = None
        self.pose = None
