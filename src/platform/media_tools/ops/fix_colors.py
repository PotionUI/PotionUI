from __future__ import annotations

from src.platform.imaging.color_fix import ColorFix, estimate_fix, ffmpeg_filters
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
        path, frame_count = decode_rgb_frames(
            media,
            ctx.scoped(0.0, 0.2),
            SAMPLE_WIDTH,
            SAMPLE_HEIGHT,
            fps=sample_rate_for(media.duration),
            max_frames=MAX_SAMPLES,
        )
        try:
            fix = estimate_fix(
                load_rgb_frames(path, frame_count, SAMPLE_WIDTH, SAMPLE_HEIGHT),
                white_balance=bool(params.get("white_balance", True)),
                contrast=bool(params.get("contrast", True)),
            )
        finally:
            path.unlink(missing_ok=True)
        fix = fix.with_strength(float(params.get("strength", 100.0)) / 100.0)
        return self._render(media, fix, ctx.scoped(0.2, 1.0))

    def _render(self, media: ToolMedia, fix: ColorFix, ctx: ToolContext) -> ToolMedia:
        return run_fragments(media, [FilterFragment(vf=ffmpeg_filters(fix))], ctx)


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=FixColors())
