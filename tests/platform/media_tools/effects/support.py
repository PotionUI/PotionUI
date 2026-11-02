from __future__ import annotations

from src.platform.media_tools.effects.loader import core_effects
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.tool import effect_tool_spec
from src.platform.media_tools.types import ToolSpec


def core_effect(effect_id: str):
    return next(effect for effect in core_effects() if effect.id == effect_id)


def effect_spec(effect_id: str, runtime: EffectRuntime | None = None) -> ToolSpec:
    return effect_tool_spec(core_effect(effect_id), runtime)


def numpy_only() -> EffectRuntime:
    return EffectRuntime(filters=frozenset, torch_available=lambda: False)


def without_filters() -> EffectRuntime:
    return EffectRuntime(filters=frozenset)
