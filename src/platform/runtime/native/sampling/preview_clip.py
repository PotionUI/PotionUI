from __future__ import annotations

import io
import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CLIP_MAX_FRAMES = 25
CLIP_MAX_UPDATES = 8
CLIP_MIN_EVERY = 3
CLIP_FPS = 8
CLIP_BYTE_CAP = 300 * 1024
CLIP_TIERS = (384, 288, 192, 128)
CLIP_QUALITY = 72
CLIP_FLOOR_QUALITY = 40


def clip_cadence(total_steps: int) -> int:
    return max(CLIP_MIN_EVERY, total_steps // CLIP_MAX_UPDATES)


class ClipSchedule:
    def __init__(self, max_updates: int = CLIP_MAX_UPDATES) -> None:
        self._max_updates = max_updates
        self.emitted = 0
        self.every = CLIP_MIN_EVERY

    def start(self, total_steps: int) -> None:
        self.every = clip_cadence(total_steps)

    def due(self, step_index: int) -> bool:
        if self.emitted >= self._max_updates:
            return False
        return (step_index + 1) % self.every == 0

    def mark_emitted(self) -> None:
        self.emitted += 1


@dataclass
class SpriteClip:
    sprite: Image.Image
    encoded: bytes
    frames: int
    columns: int
    frame_width: int
    frame_height: int
    fps: int
    step: int
    total_steps: int
    source_indices: list[int]
    long_edge: int
    update_index: int = 0
    updates_max: int = CLIP_MAX_UPDATES
    every: int = CLIP_MIN_EVERY


def _fit(frame: np.ndarray, long_edge: int) -> Image.Image:
    image = Image.fromarray(frame, mode="RGB")
    current = max(image.width, image.height)
    if current <= long_edge:
        return image
    scale = long_edge / current
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.BILINEAR)


def _compose(images: Sequence[Image.Image], columns: int) -> Image.Image:
    width, height = images[0].size
    rows = math.ceil(len(images) / columns)
    sheet = Image.new("RGB", (width * columns, height * rows))
    for index, image in enumerate(images):
        sheet.paste(image, ((index % columns) * width, (index // columns) * height))
    return sheet


def _encode(sheet: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    sheet.save(buffer, format="WEBP", quality=quality, method=4)
    return buffer.getvalue()


def build_sprite_clip(
    frames: Sequence[np.ndarray],
    source_indices: Sequence[int],
    *,
    step: int,
    total_steps: int,
    byte_cap: int = CLIP_BYTE_CAP,
    tiers: Sequence[int] = CLIP_TIERS,
    max_frames: int = CLIP_MAX_FRAMES,
) -> SpriteClip:
    frames = list(frames)[:max_frames]
    indices = [int(i) for i in list(source_indices)[:max_frames]]
    columns = max(1, math.ceil(math.sqrt(len(frames))))
    chosen: Optional[tuple[Image.Image, bytes, int, list[Image.Image]]] = None
    for tier in tiers:
        images = [_fit(frame, tier) for frame in frames]
        sheet = _compose(images, columns)
        encoded = _encode(sheet, CLIP_QUALITY)
        chosen = (sheet, encoded, tier, images)
        if len(encoded) <= byte_cap:
            break
    sheet, encoded, tier, images = chosen
    if len(encoded) > byte_cap:
        encoded = _encode(sheet, CLIP_FLOOR_QUALITY)
    return SpriteClip(
        sprite=sheet,
        encoded=encoded,
        frames=len(images),
        columns=columns,
        frame_width=images[0].width,
        frame_height=images[0].height,
        fps=CLIP_FPS,
        step=step,
        total_steps=total_steps,
        source_indices=indices,
        long_edge=tier,
    )


@dataclass
class _Job:
    frames: list[np.ndarray]
    indices: list[int]
    step: int
    total_steps: int
    update_index: int
    every: int


@dataclass
class ClipRelay:
    emit: Callable[[SpriteClip], None]
    updates_max: int = CLIP_MAX_UPDATES
    byte_cap: int = CLIP_BYTE_CAP
    dropped: int = 0
    _pending: Optional[_Job] = field(default=None, init=False, repr=False)
    _worker: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def submit(self, frames, indices, *, step: int, total_steps: int, update_index: int, every: int) -> None:
        job = _Job(list(frames), list(indices), step, total_steps, update_index, every)
        with self._lock:
            if self._pending is not None:
                self.dropped += 1
            self._pending = job
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="preview-clip-relay", daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._worker = None
                    return
            try:
                clip = build_sprite_clip(
                    job.frames,
                    job.indices,
                    step=job.step,
                    total_steps=job.total_steps,
                    byte_cap=self.byte_cap,
                )
                clip.update_index = job.update_index
                clip.updates_max = self.updates_max
                clip.every = job.every
                self.emit(clip)
            except Exception:
                logger.debug("preview clip emit failed; skipping update", exc_info=True)

    def drain(self, timeout: float = 5.0) -> None:
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout)
