# Derived from: https://github.com/DepthAnything/Video-Depth-Anything (Apache-2.0), run.py and video_depth_anything/video_depth.py

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

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


def window_starts(total: int) -> List[int]:
    step = INFER_LEN - OVERLAP
    return list(range(0, total, step))


def padded_length(total: int) -> int:
    step = INFER_LEN - OVERLAP
    return total + (step - (total % step)) % step + (INFER_LEN - step)


def align_windows(depth_list: Sequence[np.ndarray], total: int) -> List[np.ndarray]:
    align_len = OVERLAP - INTERP_LEN
    kf_align = KEYFRAMES[:align_len]
    aligned: List[np.ndarray] = []
    reference: List[np.ndarray] = []
    for frame_id in range(0, len(depth_list), INFER_LEN):
        if not aligned:
            aligned.extend(depth_list[:INFER_LEN])
            reference.extend(depth_list[frame_id + kf] for kf in kf_align)
            continue
        current = [depth_list[frame_id + i] for i in range(len(kf_align))]
        scale, shift = solve_scale_and_shift(np.concatenate(current), np.concatenate(reference))
        before = aligned[-INTERP_LEN:]
        after = [np.maximum(depth_list[frame_id + i] * scale + shift, 0.0) for i in range(align_len, OVERLAP)]
        aligned[-INTERP_LEN:] = blend_frames(before, after)
        for i in range(OVERLAP, INFER_LEN):
            aligned.append(np.maximum(depth_list[frame_id + i] * scale + shift, 0.0))
        reference = reference[:1]
        for kf in kf_align[1:]:
            reference.append(np.maximum(depth_list[frame_id + kf] * scale + shift, 0.0))
    return aligned[:total]


@dataclass
class VideoDepthEstimator:
    model: object
    device: str = "cpu"

    def release(self) -> None:
        import torch

        self.model.cpu()
        self.device = "cpu"
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _run_window(self, window) -> np.ndarray:
        import torch

        with torch.no_grad():
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=self.device.startswith("cuda")):
                return self.model(window)

    def estimate(
        self,
        frames: Sequence[np.ndarray],
        output_size: Optional[Tuple[int, int]] = None,
        progress: Optional[Callable[[int, int], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        total = len(frames)
        if total == 0:
            raise ValueError("No frames to estimate depth for")
        source_h, source_w = frames[0].shape[:2]
        out_w, out_h = output_size if output_size else (source_w, source_h)
        size = model_input_size(source_h, source_w)
        padded = list(frames) + [frames[-1]] * (padded_length(total) - total)
        starts = window_starts(total)
        previous = None
        depth_list: List[np.ndarray] = []
        for index, start in enumerate(starts):
            if cancelled is not None and cancelled():
                raise SamplingCancelled()
            batch = np.stack([prepare_frame(padded[start + i], size) for i in range(INFER_LEN)])
            window = torch.from_numpy(batch).unsqueeze(0).to(self.device)
            if previous is not None:
                window[:, :OVERLAP] = previous[:, list(KEYFRAMES)]
            depth = self._run_window(window).float()
            resized = F.interpolate(depth.flatten(0, 1).unsqueeze(1), size=(out_h, out_w), mode="bilinear", align_corners=True)
            depth_list.extend(resized[i, 0].cpu().numpy() for i in range(resized.shape[0]))
            previous = window
            if progress is not None:
                progress(index + 1, len(starts))
        aligned = align_windows(depth_list, total)
        return np.stack(aligned).astype(np.float16)


def depth_to_guide(depth: np.ndarray, low_percentile: float = 2.0, high_percentile: float = 98.0) -> np.ndarray:
    sample = depth[:: max(1, depth.shape[0] // 64)].astype(np.float32)
    low, high = np.percentile(sample, [low_percentile, high_percentile])
    span = max(float(high - low), 1e-6)
    scaled = np.clip((depth.astype(np.float32) - float(low)) / span, 0.0, 1.0)
    return np.rint(scaled * 255.0).astype(np.uint8)
