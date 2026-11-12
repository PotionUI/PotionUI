from __future__ import annotations

from typing import Any, List, Tuple
import numpy as np
import torch
from torch import Tensor


def to_frames_tensor(image: Any) -> Tensor:
    if hasattr(image, "convert"):
        arr = torch.from_numpy(np.array(image.convert("RGB"))).float() / 255.0
    else:
        arr = torch.as_tensor(image, dtype=torch.float32)
        if arr.max() > 1.5:
            arr = arr / 255.0
    if arr.ndim == 3:
        arr = arr.unsqueeze(0)
    return arr


def hold_frames(frames: Tensor, count: int) -> Tensor:
    return frames[:1].expand(int(count), *frames.shape[1:])


def load_video_frames(path: str, max_frames: int, start: int = 0) -> Tensor:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"generator/video_ltx: cannot open video file {path!r}")
    frames: List[np.ndarray] = []
    try:
        for _ in range(max(0, start)):
            if not cap.grab():
                break
        while len(frames) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    if not frames:
        raise ValueError(f"generator/video_ltx: no frames decoded from {path!r}")
    return torch.from_numpy(np.stack(frames)).float().div_(255.0)


def probe_video_stream(path: str) -> Tuple[int, int, float, int]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video file {path!r}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if count <= 0:
            while cap.grab():
                count += 1
    finally:
        cap.release()
    if width <= 0 or height <= 0 or count <= 0:
        raise ValueError(f"no frames decoded from {path!r}")
    return width, height, fps, count


def resize_cover_center_crop(frames: Tensor, height: int, width: int) -> Tensor:
    chw = frames.permute(0, 3, 1, 2)
    _, _, h0, w0 = chw.shape
    scale = max(width / w0, height / h0)
    chw = torch.nn.functional.interpolate(
        chw, size=(round(h0 * scale), round(w0 * scale)), mode="bilinear", align_corners=False)
    _, _, h1, w1 = chw.shape
    top, left = (h1 - height) // 2, (w1 - width) // 2
    chw = chw[:, :, top:top + height, left:left + width]
    return (chw * 2.0 - 1.0).permute(1, 0, 2, 3).unsqueeze(0)


def prep_start_frame(image: Any, height: int, width: int, device: str, dtype: torch.dtype) -> torch.Tensor:
    if hasattr(image, "convert"):
        arr = torch.from_numpy(np.asarray(image.convert("RGB"))).to(dtype) / 255.0
    else:
        arr = torch.as_tensor(image, dtype=dtype)
        if arr.max() > 1.5:
            arr = arr / 255.0
    if arr.ndim == 4:
        arr = arr[0]
    chw = arr.permute(2, 0, 1).unsqueeze(0).to(device)
    _, _, h0, w0 = chw.shape
    scale = max(width / w0, height / h0)
    chw = torch.nn.functional.interpolate(chw, size=(round(h0 * scale), round(w0 * scale)),
                                          mode="bilinear", align_corners=False)
    _, _, h1, w1 = chw.shape
    top, left = (h1 - height) // 2, (w1 - width) // 2
    chw = chw[:, :, top:top + height, left:left + width]
    return chw.squeeze(0).permute(1, 2, 0).unsqueeze(0)
