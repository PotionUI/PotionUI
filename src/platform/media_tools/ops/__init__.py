from __future__ import annotations

from src.platform.media_tools.ops import (
    trim,
    crop,
    resize,
    rotate,
    flip,
    speed,
    fit_canvas,
    color_key,
    crop_subject,
    frame,
    split,
    concat,
    fix_colors,
    film_grain,
    cut_finder,
    mute,
    volume,
    replace_audio,
    paint_layer,
)
from src.platform.media_tools.registry import MediaToolRegistry
from src.platform.media_tools.types import ToolSpec

CORE_MODULES = (
    trim,
    crop,
    resize,
    rotate,
    flip,
    speed,
    fit_canvas,
    color_key,
    crop_subject,
    frame,
    split,
    concat,
    fix_colors,
    film_grain,
    cut_finder,
    mute,
    volume,
    replace_audio,
    paint_layer,
)


def core_specs() -> list[ToolSpec]:
    return [module.spec() for module in CORE_MODULES]


def register_core_tools(registry: MediaToolRegistry) -> None:
    for tool_spec in core_specs():
        registry.register(tool_spec)
