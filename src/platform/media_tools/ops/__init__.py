from __future__ import annotations

from typing import Optional

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
    cut_finder,
    mute,
    volume,
    replace_audio,
    paint_layer,
)
from src.platform.media_tools.effects.loader import core_effects, register_effects
from src.platform.media_tools.effects.runtime import EffectRuntime
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
    cut_finder,
    mute,
    volume,
    replace_audio,
    paint_layer,
)


def core_specs() -> list[ToolSpec]:
    return [module.spec() for module in CORE_MODULES]


def register_core_tools(registry: MediaToolRegistry, effects_runtime: Optional[EffectRuntime] = None) -> None:
    for tool_spec in core_specs():
        registry.register(tool_spec)
    register_effects(registry, core_effects(), effects_runtime)
