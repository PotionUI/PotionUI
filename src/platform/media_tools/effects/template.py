from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from src.platform.media_tools.effects.derive import DeriveError, Expression, parse_derive

VIDEO_FILTERS: dict[str, frozenset[str]] = {
    "colorchannelmixer": frozenset({"rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb"}),
    "colorlevels": frozenset({
        "rimin", "gimin", "bimin", "rimax", "gimax", "bimax",
        "romin", "gomin", "bomin", "romax", "gomax", "bomax",
    }),
    "eq": frozenset({"brightness", "contrast", "saturation", "gamma", "eval"}),
    "hue": frozenset({"h", "s", "b"}),
    "curves": frozenset({"preset", "master", "r", "g", "b", "all"}),
    "vignette": frozenset({"angle", "x0", "y0", "aspect", "dither", "eval", "mode"}),
    "fade": frozenset({"t", "st", "d", "start_frame", "nb_frames", "color", "alpha"}),
    "lut3d": frozenset({"file", "interp"}),
    "perspective": frozenset({"x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3", "interpolation", "eval", "sense"}),
    "gblur": frozenset({"sigma", "steps"}),
    "unsharp": frozenset({"lx", "ly", "la", "cx", "cy", "ca"}),
    "noise": frozenset({"alls", "allf", "all_seed"}),
    "hflip": frozenset(),
    "vflip": frozenset(),
}
AUDIO_FILTERS: dict[str, frozenset[str]] = {
    "afade": frozenset({"t", "st", "d", "curve"}),
    "volume": frozenset({"volume", "eval"}),
}
ALLOWED_FILTERS: dict[str, frozenset[str]] = {**VIDEO_FILTERS, **AUDIO_FILTERS}
DENIED_FILTERS = frozenset({
    "movie", "amovie", "sendcmd", "asendcmd", "zmq", "azmq", "subtitles", "ass", "drawtext", "ladspa", "lv2",
    "frei0r", "frei0r_src", "arnndn", "haldclut", "signature",
})
DENIED_PREFIXES = ("dnn_", "vidstab")
ASSET_OPTIONS = frozenset({("lut3d", "file")})
RUNTIME_VARS: dict[str, frozenset[str]] = {
    "perspective": frozenset({"W", "H", "in", "on"}),
    "vignette": frozenset({"w", "h", "n", "t", "pts", "r", "tb"}),
    "eq": frozenset({"n", "t", "r"}),
    "noise": frozenset({"t", "u", "p", "a"}),
    "volume": frozenset({"n", "t", "pts"}),
}
FUNCTIONS = frozenset({
    "clip", "if", "ifnot", "min", "max", "abs", "sin", "cos", "pow", "sqrt", "floor", "ceil", "round", "lerp",
    "gte", "lte", "gt", "lt", "eq",
})
WORDS = frozenset({
    "in", "out", "linear", "cubic", "tetrahedral", "trilinear", "nearest", "init", "frame", "forward",
    "backward", "source", "destination", "tri", "qsin", "esin", "hsin", "log", "ipar", "qua", "cub", "squ",
    "cbr", "par", "exp", "iqsin", "ihsin", "dese", "desi", "losi", "none", "color_negative", "cross_process",
    "darker", "increase_contrast", "lighter", "linear_contrast", "medium_contrast", "negative",
    "strong_contrast", "vintage",
})
MAX_STEP_CHARS = 16_384
MAX_EXPANSIONS = 32

_FILTER_NAME = re.compile(r"^[a-z0-9_]+$")
_OPTION_NAME = re.compile(r"^[a-z0-9_]+$")
_TOKEN = re.compile(r" *(?:(\d+(?:\.\d+)?(?:e[+-]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([-+*/^(),]))")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)\}")
_ANY_BRACE = re.compile(r"[{}]")
_SAFE_ASSET = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
_COLOR = re.compile(r"^#[0-9a-f]{6}$")
_HEX = re.compile(r"^0x[0-9a-f]{6}$")
_INT_LIMIT = 2 ** 53


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class AssetPath:
    path: str


@dataclass(frozen=True)
class TemplateEntry:
    filter: str
    options: tuple[tuple[str, Any], ...]
    when: Optional[Expression] = None


@dataclass(frozen=True)
class FfmpegTemplate:
    expr: tuple[tuple[str, str], ...] = ()
    vf: tuple[TemplateEntry, ...] = ()
    af: tuple[TemplateEntry, ...] = ()

    def filters(self) -> set[str]:
        return {entry.filter for entry in (*self.vf, *self.af)}

    def placeholders(self) -> set[str]:
        macros = {name for name, _ in self.expr}
        found: set[str] = set()
        texts = [text for _, text in self.expr]
        texts += [value for entry in (*self.vf, *self.af) for _, value in entry.options if isinstance(value, str)]
        for text in texts:
            found.update(name for name in _PLACEHOLDER.findall(text) if name not in macros)
        return found

    def conditions(self) -> list[Expression]:
        return [entry.when for entry in (*self.vf, *self.af) if entry.when is not None]


@dataclass
class CompiledTemplate:
    vf: list[str] = field(default_factory=list)
    af: list[str] = field(default_factory=list)


def is_denied(name: str) -> bool:
    return name in DENIED_FILTERS or name.startswith(DENIED_PREFIXES)


def _check_filter(name: str, audio: bool) -> None:
    if not isinstance(name, str) or not _FILTER_NAME.match(name):
        raise TemplateError("a filter name must be lowercase letters, digits or underscores")
    if is_denied(name):
        raise TemplateError(f"the {name} filter is never allowed")
    allowed = AUDIO_FILTERS if audio else VIDEO_FILTERS
    if name not in allowed:
        where = "an audio" if audio else "a video"
        raise TemplateError(f"the {name} filter is not allowed as {where} filter")


def _check_option(name: str, option: Any) -> None:
    if not isinstance(option, str) or not _OPTION_NAME.match(option):
        raise TemplateError(f"{name}: an option name must be lowercase letters, digits or underscores")
    if "file" in option and (name, option) not in ASSET_OPTIONS:
        raise TemplateError(f"{name}.{option} reads a file and is not allowed")
    if option not in ALLOWED_FILTERS[name]:
        raise TemplateError(f"{name}.{option} is not allowed")


def _check_text(text: str, where: str) -> None:
    if _ANY_BRACE.search(_PLACEHOLDER.sub("", text)):
        raise TemplateError(f"{where}: braces must wrap a lowercase {{placeholder}}")


def _parse_entry(raw: Any, audio: bool) -> TemplateEntry:
    if not isinstance(raw, Mapping):
        raise TemplateError("each filter entry must be a mapping")
    keys = [key for key in raw if key != "when"]
    if len(keys) != 1:
        raise TemplateError("each filter entry names exactly one filter")
    name = keys[0]
    _check_filter(name, audio)
    options = raw[name] or {}
    if not isinstance(options, Mapping):
        raise TemplateError(f"{name}: options must be a mapping")
    parsed: list[tuple[str, Any]] = []
    for option, value in options.items():
        _check_option(name, option)
        if isinstance(value, bool) or isinstance(value, (int, float)):
            format_value(value)
        elif isinstance(value, str):
            _check_text(value, f"{name}.{option}")
        else:
            raise TemplateError(f"{name}.{option} must be a number, a flag or text")
        parsed.append((option, value))
    when = None
    if "when" in raw:
        try:
            when = parse_derive(raw["when"])
        except DeriveError as exc:
            raise TemplateError(f"{name}.when: {exc}") from None
    return TemplateEntry(filter=name, options=tuple(parsed), when=when)


def parse_template(data: Any) -> FfmpegTemplate:
    if not isinstance(data, Mapping):
        raise TemplateError("an ffmpeg render must be a mapping")
    unknown = set(data) - {"expr", "vf", "af"}
    if unknown:
        raise TemplateError(f"unknown ffmpeg render keys: {', '.join(sorted(map(str, unknown)))}")
    expr = data.get("expr") or {}
    if not isinstance(expr, Mapping):
        raise TemplateError("expr must be a mapping of names to expressions")
    macros: list[tuple[str, str]] = []
    for name, text in expr.items():
        if not isinstance(name, str) or not re.match(r"^[a-z_][a-z0-9_]*$", name):
            raise TemplateError("an expr name must be lowercase letters, digits or underscores")
        if not isinstance(text, str):
            raise TemplateError(f"expr {name} must be text")
        _check_text(text, f"expr {name}")
        macros.append((name, text))
    lists = {}
    for key in ("vf", "af"):
        entries = data.get(key) or []
        if not isinstance(entries, list):
            raise TemplateError(f"{key} must be a list of filters")
        lists[key] = tuple(_parse_entry(entry, audio=key == "af") for entry in entries)
    if not lists["vf"] and not lists["af"]:
        raise TemplateError("an ffmpeg render needs at least one filter")
    return FfmpegTemplate(expr=tuple(macros), vf=lists["vf"], af=lists["af"])


def _number(value: float) -> str:
    if not math.isfinite(value):
        raise TemplateError("a value is not a finite number")
    if float(value).is_integer() and abs(value) < _INT_LIMIT:
        return str(int(value))
    return f"{value:.6g}"


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, AssetPath):
        return format_asset(value)
    if isinstance(value, str) and _COLOR.match(value):
        return "0x" + value[1:]
    if isinstance(value, str) and value in WORDS:
        return value
    raise TemplateError(f"{type(value).__name__} values cannot enter a filtergraph")


def format_asset(value: Any) -> str:
    if not isinstance(value, AssetPath):
        raise TemplateError("this option only takes a file staged by the host")
    path = value.path
    if not _SAFE_ASSET.match(path) or ".." in path.split("/"):
        raise TemplateError("a staged asset path is not safe")
    return path


def check_runtime_expression(text: str, filter_name: str) -> str:
    if _HEX.match(text):
        return text
    if not text.strip():
        raise TemplateError(f"{filter_name}: an option is empty")
    allowed = RUNTIME_VARS.get(filter_name, frozenset())
    position = 0
    while position < len(text):
        found = _TOKEN.match(text, position)
        if not found or found.end() == position:
            raise TemplateError(f"{filter_name}: unexpected character in an option")
        word = found.group(2)
        if word and word not in allowed and word not in FUNCTIONS and word not in WORDS:
            raise TemplateError(f"{filter_name}: '{word}' is not allowed here")
        position = found.end()
    return text


def substitute(text: str, values: Mapping[str, Any], macros: Mapping[str, str], filter_name: str) -> str:
    def resolve(match: re.Match) -> str:
        key = match.group(1)
        if key in macros:
            return f"({macros[key]})"
        if key not in values:
            raise TemplateError(f"unknown placeholder {{{key}}}")
        value = values[key]
        if isinstance(value, AssetPath):
            raise TemplateError(f"{filter_name} cannot take a file")
        return format_value(value)

    for _ in range(MAX_EXPANSIONS):
        expanded = _PLACEHOLDER.sub(resolve, text)
        if expanded == text:
            return text
        if len(expanded) > MAX_STEP_CHARS:
            raise TemplateError("the expanded expr is longer than 16 KB")
        text = expanded
    raise TemplateError("expr names refer to each other in a loop")


def _asset_option(raw: Any, values: Mapping[str, Any], name: str, option: str) -> str:
    found = _PLACEHOLDER.fullmatch(raw) if isinstance(raw, str) else None
    if found is None or found.group(1) not in values:
        raise TemplateError(f"{name}.{option} only takes a file staged by the host")
    return format_asset(values[found.group(1)])


def _compile_entry(entry: TemplateEntry, values: Mapping[str, Any], macros: Mapping[str, str]) -> str:
    parts = []
    for option, raw in entry.options:
        if (entry.filter, option) in ASSET_OPTIONS:
            text = _asset_option(raw, values, entry.filter, option)
        else:
            text = substitute(raw, values, macros, entry.filter) if isinstance(raw, str) else format_value(raw)
            check_runtime_expression(text, entry.filter)
        parts.append(f"{option}='{text}'")
    return entry.filter + ("=" + ":".join(parts) if parts else "")


def compile_template(
    template: FfmpegTemplate,
    values: Mapping[str, Any],
    condition: Callable[[Expression], bool],
) -> CompiledTemplate:
    macros = dict(template.expr)
    compiled = CompiledTemplate()
    for target, entries in ((compiled.vf, template.vf), (compiled.af, template.af)):
        for entry in entries:
            if entry.when is not None and not condition(entry.when):
                continue
            target.append(_compile_entry(entry, values, macros))
    if sum(len(item) + 1 for item in (*compiled.vf, *compiled.af)) > MAX_STEP_CHARS:
        raise TemplateError("the compiled filters are longer than 16 KB")
    return compiled
