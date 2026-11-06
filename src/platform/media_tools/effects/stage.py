from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from src.platform.media_tools.effects.api import FrameBatch, FrameRender
from src.platform.media_tools.types import ToolFailed


@dataclass(frozen=True)
class FrameStage:
    label: str
    kind: str
    render: FrameRender
    params: dict
    derived: dict
    device: Optional[str] = None

    def apply(
        self, frames: np.ndarray, indices: tuple[int, ...], fps: float, duration: Optional[float]
    ) -> np.ndarray:
        times = tuple(index / fps for index in indices)
        if self.kind == "torch":
            return self._torch(frames, indices, times, fps, duration)
        batch = FrameBatch(frames=frames, indices=indices, times=times, fps=fps, duration=duration)
        return self._checked(np.asarray(self.render(batch, self.params, self.derived)), frames)

    def _torch(
        self, frames: np.ndarray, indices: tuple[int, ...], times: tuple[float, ...], fps: float,
        duration: Optional[float],
    ) -> np.ndarray:
        import torch

        with torch.inference_mode():
            tensor = torch.from_numpy(np.ascontiguousarray(frames)).to(self.device or "cpu")
            tensor = tensor.permute(0, 3, 1, 2).float() / 255
            batch = FrameBatch(
                frames=frames, indices=indices, times=times, fps=fps, duration=duration, tensor=tensor,
            )
            result: Any = self.render(batch, self.params, self.derived)
            if not isinstance(result, torch.Tensor) or result.shape != tensor.shape:
                raise ToolFailed(f"{self.label} returned frames of the wrong shape")
            out = torch.floor(result.float() * 255 + 0.5).clamp(0, 255).to(torch.uint8)
            return out.permute(0, 2, 3, 1).contiguous().cpu().numpy()

    def _checked(self, result: np.ndarray, frames: np.ndarray) -> np.ndarray:
        if result.shape != frames.shape:
            raise ToolFailed(f"{self.label} changed the size of a frame")
        if result.dtype != np.uint8:
            result = np.clip(np.floor(result.astype(np.float64) + 0.5), 0, 255)
        return np.ascontiguousarray(result, dtype=np.uint8)


def run_stages(
    stages: list[FrameStage], frames: np.ndarray, indices: tuple[int, ...], fps: float, duration: Optional[float]
) -> np.ndarray:
    for stage in stages:
        frames = stage.apply(frames, indices, fps, duration)
    return frames
