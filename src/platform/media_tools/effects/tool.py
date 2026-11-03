from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

import numpy as np
from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.effects.assets import AssetError, load_lut, stage_cube
from src.platform.imaging.filters.lut import mix_cube
from src.platform.media_tools.effects.derive import DeriveError, Expression, evaluate, evaluate_all
from src.platform.media_tools.effects.looks import Look, look_env, look_table
from src.platform.media_tools.effects.runtime import DEFAULT_RUNTIME, Choice, EffectRuntime
from src.platform.media_tools.effects.spec import ANCHOR_POINTS, EffectSpec
from src.platform.media_tools.effects.stage import FrameStage
from src.platform.media_tools.effects.template import AssetPath, CompiledTemplate, TemplateError, compile_template
from src.platform.media_tools.fastpath import RGB_IN, prepare_image, run_single
from src.platform.media_tools.frames import decode_rgb_frames, load_rgb_frames
from src.platform.media_tools.timeline import Clock, StepContext
from src.platform.media_tools.types import (
    DEFAULT_SCOPES,
    FilterFragment,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
    UnsupportedMedia,
)

if TYPE_CHECKING:
    from src.platform.media_tools.still import StillFrame

ANALYSIS_KEY = "@analysis"
ASSETS_KEY = "@assets"
LOOKS_KEY = "@looks"
LUT_ASSET_KIND = "lut"
LOOK_PARAM_TYPE = "look"
DEFAULT_FPS = 24.0
MAX_SAMPLE_SIDE = 1024
MAX_SAMPLE_FRAMES = 600


@dataclass
class Binding:
    params: dict
    env: dict
    derived: dict
    values: dict


class HostAnalysis:
    def __init__(self, step: StepContext):
        self.step = step

    def sample_frames(self, *, width: int, height: int, rate: float, max_frames: int) -> np.ndarray:
        width, height, max_frames = int(width), int(height), int(max_frames)
        if not (1 <= width <= MAX_SAMPLE_SIDE and 1 <= height <= MAX_SAMPLE_SIDE):
            raise ToolFailed("Analysis frames must be between 1 and 1024 pixels")
        if not 1 <= max_frames <= MAX_SAMPLE_FRAMES or not math.isfinite(rate) or rate <= 0:
            raise ToolFailed("Analysis needs a positive rate and at most 600 frames")
        clock, source = self.step.clock, self.step.source
        length = clock.length if clock.length is not None else source.duration
        effective = min(float(rate), max_frames / length) if length and length > 0 else float(rate)
        path, count = decode_rgb_frames(
            source, self.step.ctx, width, height,
            fps=effective / clock.rate, max_frames=max_frames, window=clock.source_window(),
        )
        try:
            return load_rgb_frames(path, count, width, height)
        finally:
            path.unlink(missing_ok=True)

    def is_cancelled(self) -> bool:
        return self.step.ctx.is_cancelled()


def _flatten(scope: Mapping[str, Any]) -> dict:
    values: dict = {}
    for name, value in scope.items():
        if isinstance(value, Mapping):
            for attr, inner in value.items():
                values[f"{name}.{attr}"] = inner
        else:
            values[name] = value
    return values


def _split_alpha(image: Image.Image) -> tuple[np.ndarray, Optional[Image.Image]]:
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    return np.ascontiguousarray(np.asarray(image.convert("RGB"))), alpha


def _join_alpha(rgb: np.ndarray, alpha: Optional[Image.Image]) -> Image.Image:
    result = Image.fromarray(rgb, "RGB")
    if alpha is not None:
        result.putalpha(alpha)
    return result


class EffectTool(MediaTool):
    def __init__(self, effect: EffectSpec, runtime: Optional[EffectRuntime] = None):
        self.effect = effect
        self.runtime = runtime or DEFAULT_RUNTIME

    def has_image_path(self) -> bool:
        return "image" in self.effect.accepts and self.effect.analysis is None and bool(
            self.effect.python_candidates()
        )

    def advance(self, media: ToolMedia, params: dict) -> ToolMedia:
        return media

    def prepare(self, params: dict, step: StepContext) -> dict:
        params = self._measured(params, step)
        params = self._with_looks(params, step.ctx.user_id)
        if not self.lut_textures() or ASSETS_KEY in params or step.source.kind == "image":
            return params
        binding = self.bind(step.source, params)
        return {**params, ASSETS_KEY: self.stage_assets(binding, step.ctx.scratch_dir)}

    def look_params(self) -> list[str]:
        return [param["name"] for param in self.effect.params if param["type"] == LOOK_PARAM_TYPE]

    def _with_looks(self, params: dict, user_id: Optional[str]) -> dict:
        names = self.look_params()
        if not names or LOOKS_KEY in params:
            return params
        clean = self.public(params)
        looks = {}
        for name in names:
            ref = clean.get(name) or ""
            looks[name] = self.runtime.resolve_look(ref, user_id) if ref else None
        return {**params, LOOKS_KEY: looks}

    def _looks(self, params: Mapping[str, Any], clean: Mapping[str, Any]) -> dict[str, Optional[Look]]:
        given = params.get(LOOKS_KEY)
        looks: dict[str, Optional[Look]] = {}
        for name in self.look_params():
            ref = clean.get(name) or ""
            if not ref:
                looks[name] = None
                continue
            look = given.get(name) if isinstance(given, Mapping) else self.runtime.resolve_look(ref, None)
            if look is None or look.id != ref:
                raise ToolFailed(f"{self.effect.label}: this look is not available")
            looks[name] = look
        return looks

    def lut_textures(self) -> list:
        return [texture for texture in self.effect.textures if texture.kind == "lut3d"]

    def stage_assets(self, binding: Binding, folder: Path) -> dict:
        staged: dict = {}
        for texture in self.lut_textures():
            cube = binding.derived.get(texture.source)
            if cube is None:
                continue
            relative = stage_cube(cube, Path(folder))
            if relative is not None:
                staged[texture.source] = relative
        return staged

    def _load_textures(self, clean: Mapping[str, Any], scope: Mapping[str, Any], looks: Mapping[str, Optional[Look]]) -> dict:
        loaded: dict = {}
        for texture in self.lut_textures():
            if texture.source in looks:
                look = looks[texture.source]
                strength = float(scope.get(texture.mix, 1.0)) if texture.mix else 1.0
                table = look_table(look) if look is not None else None
                loaded[texture.source] = None if table is None else table if strength >= 1 else mix_cube(table, strength)
                loaded[f"@{texture.source}"] = look
                continue
            ref = clean.get(texture.source) or ""
            if not ref:
                loaded[texture.source] = None
                continue
            path = self.runtime.resolve_asset(LUT_ASSET_KIND, ref)
            if path is None:
                raise ToolFailed(f"{self.effect.label}: the LUT {ref} is not installed")
            strength = float(scope.get(texture.mix, 1.0)) if texture.mix else 1.0
            try:
                loaded[texture.source] = load_lut(path, strength)
            except (AssetError, OSError) as exc:
                raise ToolFailed(f"{self.effect.label}: {exc}") from None
        return loaded

    def _measured(self, params: dict, step: StepContext) -> dict:
        analysis = self.effect.analysis
        if analysis is None or ANALYSIS_KEY in params:
            return params
        clean = self.public(params)
        start, end = step.clock.source_window()
        window = (round(start, 3), None if end is None else round(end, 3))
        key = (
            "effect-analysis", self.effect.tool_id, window, round(step.clock.rate, 6),
            tuple((name, clean.get(name)) for name in analysis.keys),
        )
        values = step.memo(key, lambda: self.measure(clean, step))
        return {**params, ANALYSIS_KEY: dict(values)}

    def measure(self, params: dict, step: StepContext) -> dict:
        analysis = self.effect.analysis
        result = analysis.function(HostAnalysis(step), dict(params))
        if not isinstance(result, Mapping) or set(result) != set(analysis.values):
            raise ToolFailed(f"{self.effect.label} analysis must return {', '.join(analysis.values)}")
        values: dict = {}
        for name in analysis.values:
            value = result[name]
            if isinstance(value, bool) or not isinstance(value, (int, float, np.floating, np.integer)):
                raise ToolFailed(f"{self.effect.label} analysis value {name} is not a number")
            if not math.isfinite(float(value)):
                raise ToolFailed(f"{self.effect.label} analysis value {name} is not finite")
            values[name] = float(value)
        return values

    @staticmethod
    def public(params: Mapping[str, Any]) -> dict:
        return {name: value for name, value in params.items() if not name.startswith("@")}

    def bind(self, media: ToolMedia, params: Mapping[str, Any]) -> Binding:
        clean = self.public(params)
        analysis = dict(params.get(ANALYSIS_KEY) or {})
        if self.effect.analysis is not None and set(analysis) != set(self.effect.analysis.values):
            raise ToolFailed(f"{self.effect.label} was not measured before rendering")
        looks = self._looks(params, clean)
        env: dict = {}
        for param in self.effect.params:
            value = clean.get(param["name"], param.get("default"))
            if param["type"] == LOOK_PARAM_TYPE:
                env[param["name"]] = look_env(looks[param["name"]])
                continue
            env[param["name"]] = ANCHOR_POINTS.get(value, ANCHOR_POINTS["center"]) if param["type"] == "anchor" else value
        env.update(analysis)
        env["media"] = {
            "width": media.width or 0,
            "height": media.height or 0,
            "fps": float(media.fps or DEFAULT_FPS),
            "duration": float(media.duration or 0.0),
        }
        try:
            derived = evaluate_all(self.effect.derive, env)
        except DeriveError as exc:
            raise ToolFailed(f"{self.effect.label}: {exc}") from None
        scope = {**env, **derived}
        textures = self._load_textures(clean, scope, looks)
        values = _flatten(scope)
        for name, relative in (params.get(ASSETS_KEY) or {}).items():
            values[name] = AssetPath(relative)
        return Binding(params=clean, env=scope, derived={**analysis, **derived, **textures}, values=values)

    def compile(self, binding: Binding, choice: Choice) -> CompiledTemplate:
        def condition(expression: Expression) -> bool:
            try:
                return bool(evaluate(expression, binding.env))
            except DeriveError as exc:
                raise ToolFailed(f"{self.effect.label}: {exc}") from None

        try:
            return compile_template(choice.candidate.template, binding.values, condition)
        except TemplateError as exc:
            raise ToolFailed(f"{self.effect.label}: {exc}") from None

    def stage(self, binding: Binding, choice: Choice) -> FrameStage:
        return FrameStage(
            label=self.effect.label,
            kind=choice.candidate.kind,
            render=choice.candidate.function,
            params=dict(binding.params),
            derived=dict(binding.derived),
            device=choice.device,
        )

    def allows(self, choice: Choice, binding: Binding) -> bool:
        when = choice.candidate.when
        if when is None:
            return True
        try:
            return bool(evaluate(when, binding.env))
        except DeriveError as exc:
            raise ToolFailed(f"{self.effect.label}: {exc}") from None

    def _choose(self, python_only: bool = False) -> Choice:
        choice = self.runtime.choose(self.effect, python_only=python_only)
        if choice is None:
            raise ToolFailed(f"{self.effect.label} cannot render on this server")
        return choice

    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        if media.kind != "video":
            return None
        binding = self.bind(media, params)
        choice = self._choose()
        staged = params.get(ASSETS_KEY) or {}
        if choice.candidate.kind == "ffmpeg" and (not self.allows(choice, binding) or any(
            binding.derived.get(texture.source) is not None and texture.source not in staged
            for texture in self.lut_textures()
        )):
            choice = self._choose(python_only=True)
        if choice.candidate.kind == "ffmpeg":
            compiled = self.compile(binding, choice)
            return FilterFragment(vf=compiled.vf, af=compiled.af, rgb=bool(compiled.vf))
        return FilterFragment(stage=self.stage(binding, choice))

    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        results = []
        for media in inputs:
            prepared = params
            if media.kind == "video":
                prepared = self.prepare(params, StepContext(source=media, clock=Clock(length=media.duration), ctx=ctx.quiet()))
            results.append(run_single(self, media, prepared, ctx))
        return results

    def image(self, image: Image.Image, params: dict) -> Image.Image:
        if not self.has_image_path():
            raise UnsupportedMedia(f"{self.effect.label} cannot edit images")
        media = ToolMedia(path=Path(getattr(image, "filename", "") or "image"), kind="image", width=image.width, height=image.height)
        binding = self.bind(media, params)
        rgb, alpha = _split_alpha(image)
        stage = self.stage(binding, self._choose(python_only=True))
        return _join_alpha(stage.apply(rgb[None], (0,), DEFAULT_FPS, 0.0)[0], alpha)

    def still(self, image: Image.Image, params: dict, frame: "StillFrame") -> Optional[Image.Image]:
        if frame.media.kind == "image":
            return self.image(image, params) if self.has_image_path() else None
        media = frame.media.with_changes(width=image.width, height=image.height)
        binding = self.bind(media, params)
        choice = self.runtime.choose(self.effect, python_only=True) or self._choose()
        if choice.candidate.kind == "ffmpeg" and not self.allows(choice, binding):
            raise ToolFailed(f"{self.effect.label} cannot render these settings on this server")
        if choice.candidate.kind == "ffmpeg":
            return self._still_ffmpeg(image, binding, choice, frame)
        rgb, alpha = _split_alpha(image)
        stage = self.stage(binding, choice)
        out = stage.apply(rgb[None], (frame.index,), frame.fps, frame.clock.length)[0]
        return _join_alpha(out, alpha)

    def _still_ffmpeg(self, image: Image.Image, binding: Binding, choice: Choice, frame: "StillFrame") -> Image.Image:
        compiled = self.compile(binding, choice)
        if not compiled.vf:
            return image
        ctx = frame.ctx
        source = ctx.new_path(".png", stem="still")
        dest = ctx.new_path(".png", stem="still")
        image.convert("RGB").save(source)
        try:
            filters = [f"setpts={frame.time:.6f}/TB", *RGB_IN, *compiled.vf, "format=rgb24"]
            ctx.ffmpeg(["-i", str(source), "-vf", ",".join(filters), "-frames:v", "1", str(dest)])
            return prepare_image(dest)
        finally:
            source.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)


def effect_tool_spec(effect: EffectSpec, runtime: Optional[EffectRuntime] = None) -> ToolSpec:
    tool = EffectTool(effect, runtime)
    accepts = effect.accepts if tool.has_image_path() else tuple(kind for kind in effect.accepts if kind != "image")
    return ToolSpec(
        id=effect.tool_id,
        label=effect.label,
        icon=effect.icon,
        accepts=accepts,
        produces="same",
        cardinality="one_to_one",
        params=[dict(param) for param in effect.params],
        requires=dict(effect.requires),
        scopes=DEFAULT_SCOPES,
        source=effect.source,
        impl=tool,
        description=effect.description,
        name_suffix=f"-{effect.id.rsplit('_', 1)[-1]}",
        group="effects",
    )
