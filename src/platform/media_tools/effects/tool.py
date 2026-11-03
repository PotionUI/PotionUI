from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

import numpy as np
from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.effects.derive import DeriveError, Expression, evaluate, evaluate_all
from src.platform.media_tools.effects.runtime import DEFAULT_RUNTIME, Choice, EffectRuntime
from src.platform.media_tools.effects.spec import ANCHOR_POINTS, EffectSpec
from src.platform.media_tools.effects.stage import FrameStage
from src.platform.media_tools.effects.template import CompiledTemplate, TemplateError, compile_template
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
DEFAULT_FPS = 24.0
MAX_SAMPLE_SIDE = 1024
MAX_SAMPLE_FRAMES = 600
VIDEO_TIME_RESET = "setpts=PTS-STARTPTS"
AUDIO_TIME_RESET = "asetpts=PTS-STARTPTS"


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
        return {name: value for name, value in params.items() if name != ANALYSIS_KEY}

    def bind(self, media: ToolMedia, params: Mapping[str, Any]) -> Binding:
        clean = self.public(params)
        analysis = dict(params.get(ANALYSIS_KEY) or {})
        if self.effect.analysis is not None and set(analysis) != set(self.effect.analysis.values):
            raise ToolFailed(f"{self.effect.label} was not measured before rendering")
        env: dict = {}
        for param in self.effect.params:
            value = clean.get(param["name"], param.get("default"))
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
        return Binding(params=clean, env=scope, derived={**analysis, **derived}, values=_flatten(scope))

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
        if choice.candidate.kind == "ffmpeg":
            compiled = self.compile(binding, choice)
            vf, af = list(compiled.vf), list(compiled.af)
            if self.effect.time != "none":
                vf = [VIDEO_TIME_RESET, *vf] if vf else vf
                af = [AUDIO_TIME_RESET, *af] if af else af
            return FilterFragment(vf=vf, af=af, rgb=bool(vf))
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
