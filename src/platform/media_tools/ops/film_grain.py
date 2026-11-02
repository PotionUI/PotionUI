from __future__ import annotations

import numpy as np
from PIL import Image

from src.platform.imaging.film_grain import apply_film_grain
from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.streaming import stream_video_frames
from src.platform.media_tools.types import ToolContext, ToolMedia, ToolSpec

SHADOWS_BIAS = 0.6
HIGHLIGHTS_PROTECT = 0.2

SPEC = dict(
    id="film_grain",
    label="Film grain",
    icon="film",
    accepts=("video",),
    produces="same",
    cardinality="one_to_one",
    group="effects",
    name_suffix="-grain",
    params=[
        {
            "name": "amount",
            "type": "float",
            "label": "Amount",
            "description": "How strong the grain is",
            "default": 30,
            "min": 0,
            "max": 100,
            "step": 1,
            "unit": "%",
        },
        {
            "name": "size",
            "type": "float",
            "label": "Size",
            "description": "How coarse the grain is",
            "default": 1.5,
            "min": 0.5,
            "max": 3,
            "step": 0.1,
        },
        {
            "name": "color",
            "type": "checkbox",
            "label": "Color",
            "description": "Off for black and white grain",
            "default": True,
        },
        {
            "name": "seed",
            "type": "integer",
            "label": "Seed",
            "description": "The same seed gives the same grain",
            "default": 0,
            "min": 0,
            "max": 2147483647,
        },
    ],
)


class FilmGrain(MediaTool):
    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        count = len(inputs)
        return [
            self._grain(media, params, ctx.scoped(index / count, (index + 1) / count))
            for index, media in enumerate(inputs)
        ]

    def _grain(self, media: ToolMedia, params: dict, ctx: ToolContext) -> ToolMedia:
        intensity = float(params.get("amount", 30.0)) / 100.0
        size = float(params.get("size", 1.5))
        monochromatic = not bool(params.get("color", True))
        rng = np.random.default_rng(int(params.get("seed") or 0))

        def grain(frame: np.ndarray) -> np.ndarray:
            if intensity <= 0:
                return frame
            result = apply_film_grain(
                Image.fromarray(frame),
                intensity=intensity,
                grain_size=size,
                shadows_bias=SHADOWS_BIAS,
                highlights_protect=HIGHLIGHTS_PROTECT,
                monochromatic=monochromatic,
                blur_amount=0.0,
                rng=rng,
            )
            return np.asarray(result)

        return stream_video_frames(media, ctx, grain)


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=FilmGrain())
