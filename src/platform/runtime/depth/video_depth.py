# Derived from: https://github.com/DepthAnything/Video-Depth-Anything (Apache-2.0), run.py and video_depth_anything/video_depth.py

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from src.platform.runtime.native.errors import SamplingCancelled

logger = logging.getLogger(__name__)

DEPTH_REPO = "depth-anything/Video-Depth-Anything-Small"
WEIGHT_FILENAME = "video_depth_anything_vits.pth"
WEIGHT_SIZE = 116440756
WEIGHT_SHA256 = "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"

INFER_LEN = 32
OVERLAP = 10
KEYFRAMES = (0, 12, 24, 25, 26, 27, 28, 29, 30, 31)
INTERP_LEN = 8
PATCH = 14
INPUT_SIZE = 518
WIDE_RATIO = 1.78
IMAGE_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGE_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_verified: set = set()


class DepthWeightsError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_weights(directory: Path) -> Path:
    path = Path(directory) / WEIGHT_FILENAME
    if not path.is_file():
        raise DepthWeightsError(f"Video depth weights not found: {path}")
    stat = path.stat()
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if stamp not in _verified:
        if stat.st_size != WEIGHT_SIZE or _sha256(path) != WEIGHT_SHA256:
            raise DepthWeightsError(f"{WEIGHT_FILENAME} is incomplete or corrupted; delete {path} and run again")
        _verified.add(stamp)
    return path


def load_model(path: Path):
    import torch
    from vendor.VideoDepthAnything import VideoDepthAnything

    model = VideoDepthAnything()
    state = torch.load(str(path), map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.eval()


def solve_scale_and_shift(prediction: np.ndarray, target: np.ndarray) -> Tuple[float, float]:
    prediction = prediction.astype(np.float64).ravel()
    target = target.astype(np.float64).ravel()
    a_00 = float(np.sum(prediction * prediction))
    a_01 = float(np.sum(prediction))
    a_11 = float(prediction.size)
    b_0 = float(np.sum(prediction * target))
    b_1 = float(np.sum(target))
    determinant = a_00 * a_11 - a_01 * a_01
    if determinant == 0:
        return 1.0, 0.0
    return (a_11 * b_0 - a_01 * b_1) / determinant, (-a_01 * b_0 + a_00 * b_1) / determinant


def blend_frames(before: Sequence[np.ndarray], after: Sequence[np.ndarray]) -> List[np.ndarray]:
    count = len(before)
    if count == 1:
        return [after[0]]
    weights = [i / (count - 1) for i in range(count)]
    return [before[i] * (1.0 - weights[i]) + after[i] * weights[i] for i in range(count)]


def model_input_size(height: int, width: int) -> Tuple[int, int]:
    size = INPUT_SIZE
    ratio = max(height, width) / max(1, min(height, width))
    if ratio > WIDE_RATIO:
        size = round(int(INPUT_SIZE * 1.777 / ratio) / PATCH) * PATCH
    scale = max(size / height, size / width)

    def snap(value: float) -> int:
        snapped = round(value / PATCH) * PATCH
        return snapped if snapped >= size else math.ceil(value / PATCH) * PATCH

    return snap(scale * height), snap(scale * width)


def prepare_frame(frame_rgb: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
    height, width = size
    resized = cv2.resize(frame_rgb, (width, height), interpolation=cv2.INTER_CUBIC)
    normalised = (resized.astype(np.float32) / 255.0 - IMAGE_MEAN) / IMAGE_STD
    return np.ascontiguousarray(normalised.transpose(2, 0, 1))


ALIGN_FRAMES = KEYFRAMES[: OVERLAP - INTERP_LEN]
NEW_FRAMES = INFER_LEN - OVERLAP
SAMPLE_VALUES = 2048


def _positive(frame: np.ndarray) -> np.ndarray:
    return np.maximum(frame, 0.0)


class WindowAligner:
    def __init__(self) -> None:
        self.tail: List[np.ndarray] = []
        self.reference: List[np.ndarray] = []

    def add(self, window: Sequence[np.ndarray]) -> List[np.ndarray]:
        if not self.reference:
            self.reference = [window[kf] for kf in ALIGN_FRAMES]
            self.tail = list(window[INFER_LEN - INTERP_LEN:])
            return list(window[: INFER_LEN - INTERP_LEN])
        current = [window[i] for i in range(len(ALIGN_FRAMES))]
        scale, shift = solve_scale_and_shift(np.concatenate(current), np.concatenate(self.reference))
        after = [_positive(window[i] * scale + shift) for i in range(len(ALIGN_FRAMES), OVERLAP)]
        finished = blend_frames(self.tail, after)
        fresh = [_positive(window[i] * scale + shift) for i in range(OVERLAP, INFER_LEN)]
        self.reference = self.reference[:1] + [_positive(window[kf] * scale + shift) for kf in ALIGN_FRAMES[1:]]
        finished.extend(fresh[: -INTERP_LEN])
        self.tail = fresh[-INTERP_LEN:]
        return finished

    def flush(self) -> List[np.ndarray]:
        remaining, self.tail = self.tail, []
        return remaining


def align_windows(depth_list: Sequence[np.ndarray], total: int) -> List[np.ndarray]:
    aligner = WindowAligner()
    aligned: List[np.ndarray] = []
    for start in range(0, len(depth_list), INFER_LEN):
        aligned.extend(aligner.add(depth_list[start:start + INFER_LEN]))
    aligned.extend(aligner.flush())
    return aligned[:total]


@dataclass
class VideoDepthEstimator:
    model: object
    device: str = "cpu"
    estimated_vram_gb: float = 1.5

    def activate(self) -> "VideoDepthEstimator":
        import torch

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        return self

    def release(self) -> None:
        import torch

        self.model.cpu()
        self.device = "cpu"
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def unload(self) -> None:
        self.release()
        self.model = None

    def _run_window(self, window) -> np.ndarray:
        import torch

        with torch.no_grad():
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=self.device.startswith("cuda")):
                return self.model(window)

    def iter_depth(
        self,
        frames: Iterable[np.ndarray],
        output_size: Optional[Tuple[int, int]] = None,
        progress: Optional[Callable[[int], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> Iterator[np.ndarray]:
        import torch
        import torch.nn.functional as F

        source = iter(frames)
        first = next(source, None)
        if first is None:
            raise ValueError("No frames to estimate depth for")
        out_w, out_h = output_size if output_size else (first.shape[1], first.shape[0])
        size = model_input_size(first.shape[0], first.shape[1])
        state = {"read": 1, "last": first}

        def pull(count: int) -> List[np.ndarray]:
            pulled = []
            for _ in range(count):
                frame = next(source, None)
                if frame is None:
                    frame = state["last"]
                else:
                    state["read"] += 1
                    state["last"] = frame
                pulled.append(frame)
            return pulled

        aligner = WindowAligner()
        previous = None
        emitted = 0
        index = 0
        while True:
            if cancelled is not None and cancelled():
                raise SamplingCancelled()
            if previous is None:
                batch_frames = [first] + pull(INFER_LEN - 1)
                batch = np.stack([prepare_frame(frame, size) for frame in batch_frames])
                window = torch.from_numpy(batch).unsqueeze(0).to(self.device)
            else:
                fresh = pull(INFER_LEN - OVERLAP)
                batch = np.stack([prepare_frame(frame, size) for frame in fresh])
                window = torch.empty((1, INFER_LEN) + batch.shape[1:], dtype=torch.float32, device=self.device)
                window[:, :OVERLAP] = previous[:, list(KEYFRAMES)]
                window[:, OVERLAP:] = torch.from_numpy(batch).unsqueeze(0).to(self.device)
            depth = self._run_window(window).float()
            resized = F.interpolate(depth.flatten(0, 1).unsqueeze(1), size=(out_h, out_w), mode="bilinear", align_corners=True)
            maps = [resized[i, 0].cpu().numpy() for i in range(resized.shape[0])]
            del resized, depth
            previous = window
            for item in aligner.add(maps):
                if emitted < state["read"]:
                    emitted += 1
                    yield item.astype(np.float16)
            index += 1
            if progress is not None:
                progress(state["read"])
            if state["read"] <= NEW_FRAMES * index:
                break
        for item in aligner.flush():
            if emitted < state["read"]:
                emitted += 1
                yield item.astype(np.float16)

    def estimate(
        self,
        frames: Sequence[np.ndarray],
        output_size: Optional[Tuple[int, int]] = None,
        progress: Optional[Callable[[int], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> np.ndarray:
        return np.stack(list(self.iter_depth(frames, output_size, progress, cancelled)))


def depth_levels(samples: Sequence[np.ndarray], low_percentile: float = 2.0, high_percentile: float = 98.0) -> Tuple[float, float]:
    low, high = np.percentile(np.concatenate([np.asarray(s, dtype=np.float32).ravel() for s in samples]), [low_percentile, high_percentile])
    return float(low), float(high)


def sample_depth(depth: np.ndarray) -> np.ndarray:
    flat = depth.ravel()
    stride = max(1, flat.size // SAMPLE_VALUES)
    return flat[::stride].astype(np.float32)


def depth_frame_to_guide(depth: np.ndarray, low: float, high: float) -> np.ndarray:
    span = max(high - low, 1e-6)
    scaled = np.clip((depth.astype(np.float32) - low) / span, 0.0, 1.0)
    return np.rint(scaled * 255.0).astype(np.uint8)


def depth_to_guide(depth: np.ndarray, low_percentile: float = 2.0, high_percentile: float = 98.0) -> np.ndarray:
    low, high = depth_levels([sample_depth(frame) for frame in depth], low_percentile, high_percentile)
    return np.stack([depth_frame_to_guide(frame, low, high) for frame in depth])
