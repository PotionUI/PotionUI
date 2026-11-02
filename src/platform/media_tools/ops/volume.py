from __future__ import annotations

from typing import Optional

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.types import FilterFragment, ToolFailed, ToolMedia, ToolSpec

PERCENT = "percent"
DECIBELS = "db"
MAX_FADE_SECONDS = 600

SPEC = dict(
    id="volume",
    label="Volume",
    icon="audio",
    accepts=("video", "audio"),
    produces="same",
    cardinality="one_to_one",
    group="audio",
    name_suffix="-volume",
    description="Louder, quieter, fades",
    requires={"has_audio": True},
    params=[
        {
            "name": "unit",
            "type": "select",
            "label": "Level in",
            "default": PERCENT,
            "options": [{"label": "%", "value": PERCENT}, {"label": "dB", "value": DECIBELS}],
        },
        {
            "name": "percent",
            "type": "float",
            "label": "Level",
            "default": 100,
            "min": 0,
            "max": 400,
            "step": 5,
            "unit": "%",
            "when": {"field": "unit", "equals": PERCENT},
        },
        {
            "name": "db",
            "type": "float",
            "label": "Level",
            "default": 0,
            "min": -60,
            "max": 20,
            "step": 0.5,
            "unit": "dB",
            "when": {"field": "unit", "equals": DECIBELS},
        },
        {
            "name": "fade_in",
            "type": "float",
            "label": "Fade in",
            "default": 0,
            "min": 0,
            "max": MAX_FADE_SECONDS,
            "step": 0.1,
            "unit": "s",
        },
        {
            "name": "fade_out",
            "type": "float",
            "label": "Fade out",
            "default": 0,
            "min": 0,
            "max": MAX_FADE_SECONDS,
            "step": 0.1,
            "unit": "s",
        },
    ],
)


def gain(params: dict) -> float:
    if params.get("unit") == DECIBELS:
        return 10 ** (float(params.get("db") or 0.0) / 20.0)
    return float(params.get("percent", 100.0)) / 100.0


def audio_filters(params: dict, duration: Optional[float]) -> list[str]:
    filters: list[str] = []
    level = gain(params)
    if abs(level - 1.0) > 1e-9:
        filters.append(f"volume={level:.6f}")
    fade_in = float(params.get("fade_in") or 0.0)
    fade_out = float(params.get("fade_out") or 0.0)
    if (fade_in > 0 or fade_out > 0) and duration is None:
        raise ToolFailed("Could not read the media's duration")
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={min(fade_in, duration):.6f}")
    if fade_out > 0:
        length = min(fade_out, duration)
        filters.append(f"afade=t=out:st={max(0.0, duration - length):.6f}:d={length:.6f}")
    return filters or ["anull"]


class Volume(MediaTool):
    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        return FilterFragment(af=audio_filters(params, media.duration), copy_video=True)

    def still(self, image, params: dict, frame):
        return image


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Volume())
