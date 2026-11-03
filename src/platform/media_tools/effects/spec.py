from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src.platform.media_tools.effects.derive import RESERVED, DeriveError, Expression, parse_derive
from src.platform.media_tools.effects.template import FfmpegTemplate, TemplateError, parse_template
from src.platform.media_tools.params import ANCHORS, coerce_value
from src.platform.media_tools.types import MediaToolError

TIME_MODES = ("none", "seconds", "frame")
PYTHON_KINDS = ("torch", "numpy")
RENDER_KINDS = ("ffmpeg", *PYTHON_KINDS)
EFFECT_ACCEPTS = ("video", "image")
MEDIA_ATTRS = ("width", "height", "fps", "duration")
ANCHOR_ATTRS = ("x", "y")
UNIFORM_TYPES = {
    "float": "float",
    "integer": "int",
    "checkbox": "bool",
    "color": "vec3",
    "select": "int",
    "anchor": "vec2",
}
TEXTURE_KINDS = ("lut3d", "image2d")
TEXTURE_ONLY_TYPES = ("lut", "look")
TEXTURE_PARAM_TYPES = {"lut3d": ("lut", "look"), "image2d": ("media",)}
TOP_LEVEL_KEYS = frozenset({
    "id", "label", "description", "icon", "group", "accepts", "time", "requires", "params", "derive",
    "analysis", "preview", "render", "parity",
})
MAX_SHADER_BYTES = 64 * 1024
PARITY_METRICS = {"pixels": ("max_abs", "mean_abs"), "stats": ("mean_abs", "std_abs")}
PARITY_LIMITS = {"pixels": frozenset({"max_abs", "mean_abs", "p99_abs"}), "stats": frozenset({"mean_abs", "std_abs"})}
DEFAULT_ICON = "editor-effects"

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_REF = re.compile(r"^[A-Za-z0-9_]+\.py:[A-Za-z_][A-Za-z0-9_]*$")
_SHADER_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.frag$")
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_DIRECTIVE = re.compile(r"^\s*#\s*(version|extension|include|line)\b", re.MULTILINE)
_UNIFORM = re.compile(r"\buniform\b")
_INTERFACE = re.compile(
    r"(?:^|[;{}])\s*(?:layout\s*\([^)]*\)\s*)?"
    r"(?:(?:flat|smooth|centroid|invariant|highp|mediump|lowp)\s+)*(?:in|out|inout|attribute|varying|buffer)\s",
    re.MULTILINE,
)
_ENTRY = re.compile(r"\bvec4\s+effect\s*\(")


class EffectError(MediaToolError):
    code = "invalid_effect"


@dataclass(frozen=True)
class Candidate:
    kind: str
    template: Optional[FfmpegTemplate] = None
    ref: Optional[str] = None
    function: Optional[Callable] = None
    when: Optional[Expression] = None


@dataclass(frozen=True)
class AnalysisSpec:
    ref: str
    keys: tuple[str, ...]
    values: tuple[str, ...]
    function: Optional[Callable] = None


@dataclass(frozen=True)
class TextureSpec:
    uniform: str
    source: str
    kind: str
    mix: Optional[str] = None


@dataclass(frozen=True)
class EffectSpec:
    id: str
    tool_id: str
    source: str
    label: str
    group: str
    accepts: tuple[str, ...]
    time: str
    render: tuple[Candidate, ...]
    parity: dict
    folder: Path
    description: Optional[str] = None
    icon: str = DEFAULT_ICON
    requires: dict = field(default_factory=dict)
    params: tuple[dict, ...] = ()
    derive: tuple[tuple[str, Expression], ...] = ()
    analysis: Optional[AnalysisSpec] = None
    shader_file: Optional[str] = None
    shader: Optional[str] = None
    textures: tuple[TextureSpec, ...] = ()
    gain: Optional[Expression] = None
    live: Optional[Expression] = None

    def shader_etag(self) -> Optional[str]:
        if self.shader is None:
            return None
        return '"' + hashlib.sha256(self.shader.encode("utf-8")).hexdigest()[:16] + '"'

    def param(self, name: str) -> Optional[dict]:
        return next((param for param in self.params if param["name"] == name), None)

    def python_candidates(self) -> list[Candidate]:
        return [candidate for candidate in self.render if candidate.kind in PYTHON_KINDS]

    def uniforms(self) -> list[dict]:
        rows = [
            {"name": f"u_{param['name']}", "type": UNIFORM_TYPES[param["type"]], "from": "param", "key": param["name"]}
            for param in self.params
            if param["type"] in UNIFORM_TYPES
        ]
        if self.analysis is not None:
            rows += [
                {"name": f"u_{name}", "type": "float", "from": "analysis", "key": name}
                for name in self.analysis.values
            ]
        rows += [{"name": f"u_{name}", "type": "float", "from": "derive", "key": name} for name, _ in self.derive]
        return rows


def _fail(where: str, message: str) -> EffectError:
    return EffectError(f"{where}: {message}")


def _text(data: Mapping, key: str, where: str, required: bool = False) -> Optional[str]:
    value = data.get(key)
    if value is None:
        if required:
            raise _fail(where, f"'{key}' is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, f"'{key}' must be text")
    return value.strip()


def _names(values: Any, where: str, what: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise _fail(where, f"{what} must be a list of names")
    return tuple(values)


def check_shader(text: str, where: str) -> None:
    if len(text.encode("utf-8")) > MAX_SHADER_BYTES:
        raise _fail(where, "the shader is larger than 64 KB")
    code = _COMMENTS.sub(" ", text)
    found = _DIRECTIVE.search(code)
    if found:
        raise _fail(where, f"the shader may not use #{found.group(1)}; the host owns the prelude")
    if _UNIFORM.search(code) or _INTERFACE.search(code):
        raise _fail(where, "the shader may not declare uniform, in or out; the host declares them")
    if not _ENTRY.search(code):
        raise _fail(where, "the shader must define vec4 effect(vec4 color, vec2 uv)")


def _params(raw: Any, where: str) -> tuple[dict, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _fail(where, "params must be a list")
    params: list[dict] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise _fail(where, "each param must be a mapping")
        param = dict(entry)
        name = param.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise _fail(where, "a param name must be lowercase letters, digits or underscores")
        if param.get("type") not in UNIFORM_TYPES and param.get("type") not in TEXTURE_ONLY_TYPES:
            raise _fail(where, f"param '{name}' has an unsupported type '{param.get('type')}'")
        if "default" not in param:
            raise _fail(where, f"param '{name}' needs a default")
        try:
            coerce_value(param, param["default"])
        except MediaToolError as exc:
            raise _fail(where, f"param '{name}' default: {exc.message}") from None
        params.append(param)
    return tuple(params)


def _analysis(raw: Any, params: tuple[dict, ...], where: str) -> Optional[AnalysisSpec]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _fail(where, "analysis must be a mapping")
    ref = raw.get("python")
    if not isinstance(ref, str) or not _REF.match(ref):
        raise _fail(where, "analysis.python must be 'file.py:function'")
    keys = _names(raw.get("keys") or [], where, "analysis.keys")
    known = {param["name"] for param in params}
    for key in keys:
        if key not in known:
            raise _fail(where, f"analysis key '{key}' is not a param")
    values = _names(raw.get("values"), where, "analysis.values")
    if not values:
        raise _fail(where, "analysis.values must name at least one value")
    return AnalysisSpec(ref=ref, keys=keys, values=values)


def _parse_expression(raw: Any, where: str) -> Expression:
    try:
        return parse_derive(raw)
    except DeriveError as exc:
        raise _fail(where, str(exc)) from None


def _check_names(expression: Expression, known: set[str], where: str) -> None:
    for name in sorted(expression.names()):
        if name not in known:
            raise _fail(where, f"'{name}' is not a param, analysis value or earlier derive entry")


def _derive(raw: Any, known: set[str], where: str) -> tuple[tuple[str, Expression], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise _fail(where, "derive must be a mapping of names to expressions")
    entries: list[tuple[str, Expression]] = []
    scope = set(known)
    for name, source in raw.items():
        expression = _parse_expression(source, f"{where} derive.{name}")
        _check_names(expression, scope | {"media", "pi"}, f"{where} derive.{name}")
        entries.append((name, expression))
        scope.add(name)
    return tuple(entries)


def _check_unique(names: list[str], where: str) -> None:
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not _NAME.match(name):
            raise _fail(where, f"'{name}' must be lowercase letters, digits or underscores")
        if name in RESERVED:
            raise _fail(where, f"'{name}' is reserved")
        if name in seen:
            raise _fail(where, f"'{name}' is declared twice")
        seen.add(name)


def _placeholder_ok(name: str, params: tuple[dict, ...], scalars: set[str]) -> bool:
    base, _, attr = name.partition(".")
    if not attr:
        return name in scalars
    if base == "media":
        return attr in MEDIA_ATTRS
    param = next((param for param in params if param["name"] == base), None)
    return param is not None and param["type"] == "anchor" and attr in ANCHOR_ATTRS


def _render(raw: Any, params: tuple[dict, ...], scalars: set[str], where: str) -> tuple[Candidate, ...]:
    if not isinstance(raw, list) or not raw:
        raise _fail(where, "render must list at least one candidate")
    candidates: list[Candidate] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or len(entry) != 1:
            raise _fail(where, "each render candidate is one of ffmpeg, torch or numpy")
        ((kind, body),) = entry.items()
        if kind not in RENDER_KINDS:
            raise _fail(where, f"unknown render candidate '{kind}'")
        if any(candidate.kind == kind for candidate in candidates):
            raise _fail(where, f"the {kind} candidate is listed twice")
        if kind == "ffmpeg":
            when = None
            if isinstance(body, Mapping) and "when" in body:
                body = {key: value for key, value in body.items() if key != "when"}
                when = _parse_expression(entry["ffmpeg"]["when"], f"{where} ffmpeg when")
                _check_names(when, scalars | {"media", "pi"}, f"{where} ffmpeg when")
            try:
                template = parse_template(body)
            except TemplateError as exc:
                raise _fail(f"{where} ffmpeg", str(exc)) from None
            for name in sorted(template.placeholders()):
                if not _placeholder_ok(name, params, scalars):
                    raise _fail(f"{where} ffmpeg", f"unknown placeholder {{{name}}}")
            for condition in template.conditions():
                _check_names(condition, scalars | {"media", "pi"}, f"{where} ffmpeg when")
            candidates.append(Candidate(kind=kind, template=template, when=when))
        else:
            if not isinstance(body, str) or not _REF.match(body):
                raise _fail(where, f"the {kind} candidate must be 'file.py:function'")
            candidates.append(Candidate(kind=kind, ref=body))
    return tuple(candidates)


def _preview(raw: Any, params: tuple[dict, ...], scalars: set[str], where: str):
    if raw is None:
        return None, (), None, None
    if not isinstance(raw, Mapping):
        raise _fail(where, "preview must be a mapping")
    unknown = set(raw) - {"shader", "textures", "gain", "live"}
    if unknown:
        raise _fail(where, f"unknown preview keys: {', '.join(sorted(map(str, unknown)))}")
    shader = raw.get("shader")
    if shader is not None and (not isinstance(shader, str) or not _SHADER_FILE.match(shader)):
        raise _fail(where, "preview.shader must be a .frag file next to effect.yml")
    textures: list[TextureSpec] = []
    for entry in raw.get("textures") or []:
        if not isinstance(entry, Mapping):
            raise _fail(where, "each preview texture must be a mapping")
        uniform, source, kind = entry.get("uniform"), entry.get("from"), entry.get("kind")
        if not isinstance(uniform, str) or not _NAME.match(uniform):
            raise _fail(where, "a texture uniform must be lowercase letters, digits or underscores")
        if kind not in TEXTURE_KINDS:
            raise _fail(where, f"texture kind must be one of {', '.join(TEXTURE_KINDS)}")
        param = next((param for param in params if param["name"] == source), None)
        if param is None or param["type"] not in TEXTURE_PARAM_TYPES[kind]:
            raise _fail(where, f"texture '{uniform}' must come from a {kind} param")
        mix = entry.get("mix")
        if mix is not None and mix not in scalars:
            raise _fail(where, f"texture '{uniform}' mixes by '{mix}', which is not a param or derive value")
        textures.append(TextureSpec(uniform=str(uniform), source=source, kind=kind, mix=mix))
    gain = None
    if raw.get("gain") is not None:
        gain = _parse_expression(raw["gain"], f"{where} preview.gain")
        _check_names(gain, scalars | {"media", "pi", "time"}, f"{where} preview.gain")
    live = None
    if raw.get("live") is not None:
        live = _parse_expression(raw["live"], f"{where} preview.live")
        _check_names(live, scalars | {"media", "pi"}, f"{where} preview.live")
    return shader, tuple(textures), gain, live


def _parity(raw: Any, params: tuple[dict, ...], where: str) -> dict:
    if not isinstance(raw, Mapping):
        raise _fail(where, "parity is required")
    if not isinstance(raw.get("media"), Mapping):
        raise _fail(where, "parity.media must be a mapping")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise _fail(where, "parity.cases must list at least one case")
    known = {param["name"] for param in params}
    for case in cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("params", {}), Mapping):
            raise _fail(where, "each parity case needs params")
        unknown = set(case.get("params") or {}) - known
        if unknown:
            raise _fail(where, f"parity case names unknown params: {', '.join(sorted(unknown))}")
        frames = case.get("frames")
        if not isinstance(frames, list) or not frames or not all(
            isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0 for frame in frames
        ):
            raise _fail(where, "each parity case lists frame numbers")
    fixtures = raw.get("fixtures") or {}
    if not isinstance(fixtures, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) and value and not value.startswith("/") and ".." not in value.split("/")
        for key, value in fixtures.items()
    ):
        raise _fail(where, "parity.fixtures maps an asset kind to a folder inside the effect")
    metric = raw.get("metric", "pixels")
    if metric not in PARITY_METRICS:
        raise _fail(where, f"parity.metric must be one of {', '.join(PARITY_METRICS)}")
    needed = PARITY_METRICS[metric]
    tolerance = raw.get("tolerance")
    if not isinstance(tolerance, Mapping) or not set(needed) <= set(tolerance):
        raise _fail(where, f"parity.tolerance needs {' and '.join(needed)}")
    if set(tolerance) - PARITY_LIMITS[metric]:
        raise _fail(where, f"parity.tolerance takes {', '.join(sorted(PARITY_LIMITS[metric]))}")
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 for value in tolerance.values()
    ):
        raise _fail(where, "parity.tolerance values must be numbers of at least 0")
    return dict(raw)


def qualified_id(bare: str, source: str, core_source: str) -> str:
    return bare if source == core_source else f"{source}.{bare}"


def parse_effect(data: Any, folder: Path, source: str, core_source: str = "core") -> EffectSpec:
    where = str(folder.name)
    if not isinstance(data, Mapping):
        raise _fail(where, "effect.yml must be a mapping")
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        raise _fail(where, f"unknown keys: {', '.join(sorted(map(str, unknown)))}")
    bare = _text(data, "id", where, required=True)
    if not _NAME.match(bare):
        raise _fail(where, "id must be lowercase letters, digits or underscores")
    where = bare
    label = _text(data, "label", where, required=True)
    group = _text(data, "group", where, required=True)
    if not _NAME.match(group):
        raise _fail(where, "group must be lowercase letters, digits or underscores")
    accepts = _names(data.get("accepts"), where, "accepts")
    if "video" not in accepts or any(kind not in EFFECT_ACCEPTS for kind in accepts):
        raise _fail(where, "accepts must be [video] or [video, image]")
    time = data.get("time")
    if time not in TIME_MODES:
        raise _fail(where, f"time must be one of {', '.join(TIME_MODES)}")
    requires = data.get("requires") or {}
    if not isinstance(requires, Mapping) or set(requires) - {"min_duration", "has_audio"}:
        raise _fail(where, "requires takes min_duration and has_audio")

    params = _params(data.get("params"), where)
    analysis = _analysis(data.get("analysis"), params, where)
    derive_raw = data.get("derive")
    derive_names = list(derive_raw) if isinstance(derive_raw, Mapping) else []
    analysis_names = list(analysis.values) if analysis else []
    _check_unique([param["name"] for param in params] + analysis_names + derive_names, where)
    base = {param["name"] for param in params} | set(analysis_names)
    derive = _derive(derive_raw, base, where)
    scalars = base | {name for name, _ in derive}
    shader_file, textures, gain, live = _preview(data.get("preview"), params, scalars, where)
    render = _render(data.get("render"), params, scalars, where)
    parity = _parity(data.get("parity"), params, where)

    return EffectSpec(
        id=bare,
        tool_id=qualified_id(bare, source, core_source),
        source=source,
        label=label,
        group=group,
        accepts=accepts,
        time=time,
        render=render,
        parity=parity,
        folder=folder,
        description=_text(data, "description", where),
        icon=_text(data, "icon", where) or DEFAULT_ICON,
        requires=dict(requires),
        params=params,
        derive=derive,
        analysis=analysis,
        shader_file=shader_file,
        textures=textures,
        gain=gain,
        live=live,
    )


def anchor_point(value: str) -> dict:
    column = 0.0 if "left" in value else 1.0 if "right" in value else 0.5
    row = 0.0 if "top" in value else 1.0 if "bottom" in value else 0.5
    return {"x": column, "y": row}


ANCHOR_POINTS = {anchor: anchor_point(anchor) for anchor in ANCHORS}
