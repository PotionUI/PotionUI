from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional

from src.platform.media_tools.conditions import when_matches
from src.platform.media_tools.types import InvalidToolParams, ToolMedia, ToolSpec

PARAM_TYPES = frozenset({"integer", "float", "select", "checkbox", "color", "string", "anchor", "media", "json", "lut"})
LUT_PARAM = "lut"
MAX_LUT_REF_CHARS = 255
_LUT_REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _.()+-]*(?:/[A-Za-z0-9_][A-Za-z0-9 _.()+-]*)*\.cube$", re.IGNORECASE)
MEDIA_PARAM = "media"
MEDIA_REF_KEYS = ("item_id", "generation_file_id")
ANCHORS = (
    "center", "top", "bottom", "left", "right",
    "top_left", "top_right", "bottom_left", "bottom_right",
)
TRANSPARENT = "transparent"

_HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_REF_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off", ""})
_MISSING = object()


def _label(param: dict) -> str:
    return str(param.get("label") or param.get("name"))


def _fail(param: dict, message: str) -> InvalidToolParams:
    return InvalidToolParams(str(param["name"]), message)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce_integer(param: dict, value: Any) -> int:
    if isinstance(value, bool):
        raise _fail(param, f"{_label(param)} must be a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value == int(value):
            return int(value)
        raise _fail(param, f"{_label(param)} must be a whole number")
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise _fail(param, f"{_label(param)} must be a whole number") from None
        if math.isfinite(number) and number == int(number):
            return int(number)
    raise _fail(param, f"{_label(param)} must be a whole number")


def _coerce_float(param: dict, value: Any) -> float:
    if isinstance(value, bool):
        raise _fail(param, f"{_label(param)} must be a number")
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        raise _fail(param, f"{_label(param)} must be a number") from None
    if not math.isfinite(number):
        raise _fail(param, f"{_label(param)} must be a finite number")
    return number


def _check_range(param: dict, number: float) -> None:
    low, high = param.get("min"), param.get("max")
    unit = f" {param['unit']}" if param.get("unit") else ""
    if low is not None and number < low:
        raise _fail(param, f"{_label(param)} must be at least {low:g}{unit}")
    if high is not None and number > high:
        raise _fail(param, f"{_label(param)} must be at most {high:g}{unit}")


def _option_values(param: dict) -> list[Any]:
    values = []
    for option in param.get("options") or []:
        values.append(option.get("value") if isinstance(option, dict) else option)
    return values


def _coerce_select(param: dict, value: Any) -> Any:
    options = _option_values(param)
    for option in options:
        if isinstance(option, bool) or isinstance(value, bool):
            if option is value:
                return option
            continue
        if option == value or str(option) == str(value).strip():
            return option
    allowed = ", ".join(str(option) for option in options)
    raise _fail(param, f"{_label(param)} must be one of: {allowed}")


def _coerce_checkbox(param: dict, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise _fail(param, f"{_label(param)} must be true or false")


def _coerce_color(param: dict, value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if text.lower() == TRANSPARENT:
        if param.get("allow_transparent"):
            return TRANSPARENT
        raise _fail(param, f"{_label(param)} cannot be transparent")
    found = _HEX_COLOR_RE.match(text)
    if not found:
        raise _fail(param, f"{_label(param)} must be a #RRGGBB colour")
    digits = found.group(1).lower()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits}"


def _coerce_string(param: dict, value: Any) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise _fail(param, f"{_label(param)} must be text")
    text = str(value)
    high = param.get("max")
    if high is not None and len(text) > high:
        raise _fail(param, f"{_label(param)} must be at most {int(high)} characters")
    return text


def _coerce_anchor(param: dict, value: Any) -> str:
    text = str(value).strip().lower().replace("-", "_") if value is not None else ""
    if text in ANCHORS:
        return text
    raise _fail(param, f"{_label(param)} must be one of: {', '.join(ANCHORS)}")


def _coerce_media(param: dict, value: Any) -> Any:
    if isinstance(value, ToolMedia):
        return value
    if isinstance(value, str):
        value = {"item_id": value}
    if not isinstance(value, dict) or set(value) - set(MEDIA_REF_KEYS):
        raise _fail(param, f"{_label(param)} must name one Library item")
    named = [key for key in MEDIA_REF_KEYS if value.get(key)]
    if len(named) != 1:
        raise _fail(param, f"{_label(param)} must name one Library item")
    identifier = str(value[named[0]]).strip()
    if not _REF_ID_RE.match(identifier):
        raise _fail(param, f"{_label(param)} must name one Library item")
    return {named[0]: identifier}


def media_params(spec: ToolSpec) -> list[dict]:
    return [param for param in spec.params if param.get("type") == MEDIA_PARAM]


def bind_media(spec: ToolSpec, params: dict, lookup: Callable[[dict, dict], ToolMedia]) -> dict:
    bound = dict(params)
    for param in media_params(spec):
        value = bound.get(param["name"])
        if isinstance(value, dict):
            bound[param["name"]] = lookup(param, value)
    return bound


def coerce_value(param: dict, value: Any) -> Any:
    kind = param.get("type")
    if kind == "integer":
        number = _coerce_integer(param, value)
        _check_range(param, number)
        return number
    if kind == "float":
        number = _coerce_float(param, value)
        _check_range(param, number)
        return number
    if kind == "select":
        return _coerce_select(param, value)
    if kind == "checkbox":
        return _coerce_checkbox(param, value)
    if kind == "color":
        return _coerce_color(param, value)
    if kind == "string":
        return _coerce_string(param, value)
    if kind == "anchor":
        return _coerce_anchor(param, value)
    if kind == MEDIA_PARAM:
        return _coerce_media(param, value)
    if kind == LUT_PARAM:
        return _coerce_lut(param, value)
    return value


def _coerce_lut(param: dict, value: Any) -> str:
    text = "" if value is None else value
    if not isinstance(text, str) or len(text) > MAX_LUT_REF_CHARS or (text and not _LUT_REF_RE.match(text)):
        raise _fail(param, f"{_label(param)} must be a .cube file from the LUT folder")
    return text


def _candidate(param: dict, given: dict) -> Any:
    name = param["name"]
    supplied = given.get(name, _MISSING)
    if supplied is _MISSING or _is_blank(supplied) and param.get("type") != "string":
        return param.get("default")
    return supplied


def validate_params(spec: ToolSpec, params: Optional[dict]) -> dict:
    given = dict(params or {})
    declared = {str(p["name"]): p for p in spec.params}

    for name in given:
        if name not in declared:
            raise InvalidToolParams(str(name), f"Unknown parameter: {name}")

    raw = {name: _candidate(param, given) for name, param in declared.items()}

    resolved: dict[str, Any] = {}
    for name, param in declared.items():
        if param.get("when") is not None and not when_matches(param["when"], raw):
            continue
        value = raw[name]
        if value is None:
            if "default" not in param:
                raise InvalidToolParams(name, f"{_label(param)} is required")
            resolved[name] = None
            continue
        resolved[name] = coerce_value(param, value)
    if spec.impl is not None:
        spec.impl.check_params(resolved)
    return resolved
