from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from src.platform.media_tools.effects.derive import Expression
from src.platform.media_tools.effects.spec import ANCHOR_POINTS, EffectError, EffectSpec, check_shader, parse_effect
from src.platform.media_tools.effects.template import AssetPath, TemplateError, compile_template
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.tool import EffectTool, effect_tool_spec
from src.platform.media_tools.registry import MediaToolRegistry
from src.platform.media_tools.types import CORE_SOURCE, MediaToolError

logger = logging.getLogger(__name__)

EFFECT_FILE = "effect.yml"
MODULE_PREFIX = "potionui_effect_"
MAX_YAML_BYTES = 256 * 1024
CORE_ROOT = Path(__file__).resolve().parent / "builtin"
DUMMY_MEDIA = {"width": 640, "height": 360, "fps": 24.0, "duration": 4.0}


@dataclass
class LoadedEffects:
    effects: list[EffectSpec] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _contained(folder: Path, name: str) -> Path:
    base = folder.resolve()
    target = (base / name).resolve()
    if target.parent != base or not target.is_file():
        raise EffectError(f"{folder.name}: '{name}' must be a file inside the effect folder")
    return target


def load_callable(folder: Path, reference: str) -> Callable:
    file_part, _, name = reference.partition(":")
    target = _contained(folder, file_part)
    module_name = MODULE_PREFIX + hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(module_name, target)
    if spec is None or spec.loader is None:
        raise EffectError(f"{folder.name}: '{file_part}' cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise EffectError(f"{folder.name}: '{file_part}' failed to load: {exc}") from exc
    function = getattr(module, name, None)
    if not callable(function):
        raise EffectError(f"{folder.name}: '{reference}' is not a function")
    return function


def _dummy_values(effect: EffectSpec) -> dict[str, Any]:
    values: dict[str, Any] = {f"media.{attr}": value for attr, value in DUMMY_MEDIA.items()}
    for param in effect.params:
        if param["type"] == "anchor":
            point = ANCHOR_POINTS.get(param["default"], ANCHOR_POINTS["center"])
            values.update({f"{param['name']}.x": point["x"], f"{param['name']}.y": point["y"]})
        elif param["type"] in ("lut", "media"):
            values[param["name"]] = AssetPath("asset.bin")
        else:
            values[param["name"]] = param["default"]
    for name in effect.analysis.values if effect.analysis else ():
        values[name] = 1.0
    for name, _ in effect.derive:
        values[name] = 1.0
    return values


def check_templates(effect: EffectSpec) -> None:
    def always(_: Expression) -> bool:
        return True

    for candidate in effect.render:
        if candidate.template is None:
            continue
        try:
            compile_template(candidate.template, _dummy_values(effect), always)
        except TemplateError as exc:
            raise EffectError(f"{effect.id}: ffmpeg render: {exc}") from None


def load_effect(folder: Path, source: str) -> EffectSpec:
    path = folder / EFFECT_FILE
    raw = path.read_bytes()
    if len(raw) > MAX_YAML_BYTES:
        raise EffectError(f"{folder.name}: effect.yml is larger than 256 KB")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise EffectError(f"{folder.name}: effect.yml is not valid YAML: {exc}") from None
    effect = parse_effect(data, folder, source, CORE_SOURCE)
    shader: Optional[str] = None
    if effect.shader_file:
        shader = _contained(folder, effect.shader_file).read_text(encoding="utf-8")
        check_shader(shader, effect.id)
    render = tuple(
        dataclasses.replace(candidate, function=load_callable(folder, candidate.ref)) if candidate.ref else candidate
        for candidate in effect.render
    )
    analysis = effect.analysis
    if analysis is not None:
        analysis = dataclasses.replace(analysis, function=load_callable(folder, analysis.ref))
    effect = dataclasses.replace(effect, shader=shader, render=render, analysis=analysis)
    check_templates(effect)
    return effect


class EffectLoader:
    def load_root(self, root: Path, source: str) -> LoadedEffects:
        loaded = LoadedEffects()
        root = Path(root)
        if not root.is_dir():
            return loaded
        seen: set[str] = set()
        for folder in sorted(path for path in root.iterdir() if (path / EFFECT_FILE).is_file()):
            try:
                effect = load_effect(folder, source)
            except (MediaToolError, OSError, UnicodeDecodeError) as exc:
                message = exc.message if isinstance(exc, MediaToolError) else str(exc)
                logger.warning("Effect %s from %s did not load: %s", folder.name, source, message)
                loaded.errors.append((folder.name, message))
                continue
            if effect.id in seen:
                loaded.errors.append((folder.name, f"{effect.id}: declared twice"))
                continue
            seen.add(effect.id)
            loaded.effects.append(effect)
        return loaded


@lru_cache(maxsize=1)
def core_effects() -> tuple[EffectSpec, ...]:
    loaded = EffectLoader().load_root(CORE_ROOT, CORE_SOURCE)
    if loaded.errors:
        raise EffectError("; ".join(f"{name}: {message}" for name, message in loaded.errors))
    return tuple(loaded.effects)


def register_effects(
    registry: MediaToolRegistry, effects: tuple[EffectSpec, ...] | list[EffectSpec],
    runtime: Optional[EffectRuntime] = None,
) -> list[str]:
    registered = []
    for effect in effects:
        registry.register(effect_tool_spec(effect, runtime))
        registered.append(effect.tool_id)
    return registered


def find_effect(registry: MediaToolRegistry, tool_id: str) -> Optional[EffectSpec]:
    spec = registry.get(tool_id)
    if spec is None or not isinstance(spec.impl, EffectTool):
        return None
    return spec.impl.effect
