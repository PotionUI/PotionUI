from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

from src.platform.imaging.color_fix import ColorFix, apply_fix, estimate_fix, ffmpeg_filters
from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.fastpath import run_fragments
from src.platform.media_tools.frames import decode_rgb_frames, load_rgb_frames
from src.platform.media_tools.types import (
    FilterFragment,
    InvalidToolParams,
    ToolContext,
    ToolMedia,
    ToolSpec,
)

SAMPLE_WIDTH = 96
SAMPLE_HEIGHT = 54
SAMPLE_RATE = 2.0
MAX_SAMPLES = 120

SPEC = dict(
    id="fix_colors",
    label="Fix colours",
    icon="palette",
    accepts=("video",),
    produces="same",
    cardinality="one_to_one",
    group="effects",
    name_suffix="-colors",
    params=[
        {"name": "white_balance", "type": "checkbox", "label": "White balance", "default": True},
        {"name": "contrast", "type": "checkbox", "label": "Normalise contrast", "default": True},
        {"name": "strength", "type": "float", "label": "Strength", "default": 100, "min": 0, "max": 100, "step": 5, "unit": "%"},
    ],
)


def sample_rate_for(duration: float | None) -> float:
    if not duration or duration <= 0:
        return SAMPLE_RATE
    return min(SAMPLE_RATE, MAX_SAMPLES / duration)


def estimate(
    media: ToolMedia,
    params: dict,
    ctx: ToolContext,
    rate: float,
    window: Optional[tuple[float, Optional[float]]] = None,
) -> ColorFix:
    path, frame_count = decode_rgb_frames(
        media,
        ctx,
        SAMPLE_WIDTH,
        SAMPLE_HEIGHT,
        fps=rate,
        max_frames=MAX_SAMPLES,
        window=window,
    )
    try:
        return estimate_fix(
            load_rgb_frames(path, frame_count, SAMPLE_WIDTH, SAMPLE_HEIGHT),
            white_balance=bool(params.get("white_balance", True)),
            contrast=bool(params.get("contrast", True)),
        )
    finally:
        path.unlink(missing_ok=True)


class FixColors(MediaTool):
    def check_params(self, params: dict) -> None:
        if not params.get("white_balance", True) and not params.get("contrast", True):
            raise InvalidToolParams("white_balance", "Turn on white balance or contrast")

    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        self.check_params(params)
        count = len(inputs)
        return [
            self._fix(media, params, ctx.scoped(index / count, (index + 1) / count))
            for index, media in enumerate(inputs)
        ]

    def _fix(self, media: ToolMedia, params: dict, ctx: ToolContext) -> ToolMedia:
        fix = estimate(media, params, ctx.scoped(0.0, 0.2), sample_rate_for(media.duration))
        fix = fix.with_strength(float(params.get("strength", 100.0)) / 100.0)
        return self._render(media, fix, ctx.scoped(0.2, 1.0))

    def still(self, image: Image.Image, params: dict, frame) -> Image.Image:
        self.check_params(params)
        window = frame.clock.source_window()
        length = frame.clock.length if frame.clock.length is not None else frame.source.duration
        rate = sample_rate_for(length) / frame.clock.rate
        key = ("fix_colors", bool(params.get("white_balance", True)), bool(params.get("contrast", True)), window)
        fix = frame.memo(key, lambda: estimate(frame.source, params, frame.ctx, rate, window))
        fix = fix.with_strength(float(params.get("strength", 100.0)) / 100.0)
        rgb = np.asarray(image.convert("RGB"))
        return Image.fromarray(apply_fix(rgb, fix))

    def _render(self, media: ToolMedia, fix: ColorFix, ctx: ToolContext) -> ToolMedia:
        return run_fragments(media, [FilterFragment(vf=ffmpeg_filters(fix))], ctx)


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=FixColors())
