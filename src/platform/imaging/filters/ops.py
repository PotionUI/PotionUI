from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

COLOUR = "colour"
SPATIAL = "spatial"
MAX_STEPS = 64
MIN_CURVE_POINTS = 2
MAX_CURVE_POINTS = 16
IDENTITY_CURVE: List[List[float]] = [[0, 0], [1, 1]]

RULE_SCHEMA = "schema"
RULE_OP_UNKNOWN = "op_unknown"
RULE_PARAM_UNKNOWN = "param_unknown"
RULE_PARAM_RANGE = "param_range"
RULE_CURVE_POINTS = "curve_points"
RULE_STEP_ORDER = "step_order"
RULE_STEP_COUNT = "step_count"


@dataclass(frozen=True)
class ParamSpec:
    id: str
    label: str
    type: str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    unit: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "min": self.min,
            "max": self.max,
            "default": self.default,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class OpSpec:
    id: str
    label: str
    kind: str
    params: Tuple[ParamSpec, ...] = ()
    source: str = "core"
    plugin_id: Optional[str] = None
    python: Optional[str] = None

    def param(self, param_id: str) -> Optional[ParamSpec]:
        for param in self.params:
            if param.id == param_id:
                return param
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "source": self.source,
            "plugin_id": self.plugin_id,
            "params": [param.to_dict() for param in self.params],
        }


@dataclass(frozen=True)
class StepIssue:
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _int(id_: str, label: str, lo: int, hi: int, default: int = 0, unit: Optional[str] = None) -> ParamSpec:
    return ParamSpec(id_, label, "int", default, lo, hi, unit)


def _float(id_: str, label: str, lo: float, hi: float, default: float = 0, unit: Optional[str] = None) -> ParamSpec:
    return ParamSpec(id_, label, "float", default, lo, hi, unit)


def _curve(id_: str, label: str) -> ParamSpec:
    return ParamSpec(id_, label, "curve", [list(point) for point in IDENTITY_CURVE])


CORE_OPS: Dict[str, OpSpec] = {
    spec.id: spec
    for spec in (
        OpSpec(
            "tone", "Tone", COLOUR,
            (
                _int("brightness", "Brightness", -100, 100),
                _int("contrast", "Contrast", -100, 100),
                _int("saturation", "Saturation", -100, 100),
                _int("hue", "Hue", -180, 180, unit="°"),
            ),
        ),
        OpSpec("exposure", "Exposure", COLOUR, (_float("stops", "Stops", -2, 2),)),
        OpSpec(
            "white_balance", "White balance", COLOUR,
            (_int("temperature", "Temperature", -100, 100), _int("tint", "Tint", -100, 100)),
        ),
        OpSpec(
            "curves", "Curves", COLOUR,
            (_curve("master", "Master"), _curve("r", "Red"), _curve("g", "Green"), _curve("b", "Blue")),
        ),
        OpSpec("vibrance", "Vibrance", COLOUR, (_int("amount", "Amount", -100, 100),)),
        OpSpec(
            "split_tone", "Split tone", COLOUR,
            (
                _int("shadow_hue", "Shadow hue", 0, 360, unit="°"),
                _int("shadow_sat", "Shadow saturation", 0, 100),
                _int("highlight_hue", "Highlight hue", 0, 360, unit="°"),
                _int("highlight_sat", "Highlight saturation", 0, 100),
                _int("balance", "Balance", -100, 100),
            ),
        ),
        OpSpec(
            "fade", "Fade", COLOUR,
            (
                _int("black_lift", "Black lift", 0, 40, unit="%"),
                _int("white_cap", "White cap", 0, 30, unit="%"),
            ),
        ),
        OpSpec("grayscale", "Grayscale", COLOUR),
        OpSpec("invert", "Invert", COLOUR),
        OpSpec(
            "vignette", "Vignette", SPATIAL,
            (
                _int("amount", "Amount", -100, 100),
                _int("midpoint", "Midpoint", 0, 100, 50),
                _int("feather", "Feather", 0, 100, 60),
            ),
        ),
        OpSpec(
            "grain", "Grain", SPATIAL,
            (
                _int("amount", "Amount", 0, 100),
                _float("size", "Size", 0.5, 4, 1),
                _int("seed", "Seed", 0, 65535),
            ),
        ),
    )
}


def merged_ops(extra: Optional[Mapping[str, OpSpec]] = None) -> Dict[str, OpSpec]:
    ops = dict(CORE_OPS)
    if extra:
        for op_id, spec in extra.items():
            if op_id not in CORE_OPS:
                ops[op_id] = spec
    return ops


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_curve(path: str, value: Any, issues: List[StepIssue]) -> None:
    if not isinstance(value, (list, tuple)):
        issues.append(StepIssue(RULE_SCHEMA, path, "must be a list of [x, y] points"))
        return
    if not MIN_CURVE_POINTS <= len(value) <= MAX_CURVE_POINTS:
        issues.append(
            StepIssue(RULE_CURVE_POINTS, path, f"needs {MIN_CURVE_POINTS} to {MAX_CURVE_POINTS} points, got {len(value)}")
        )
        return
    previous_x: Optional[float] = None
    for index, point in enumerate(value):
        point_path = f"{path}[{index}]"
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(_is_number(v) for v in point):
            issues.append(StepIssue(RULE_CURVE_POINTS, point_path, "must be a pair of numbers [x, y]"))
            return
        x, y = point
        if not (0 <= x <= 1 and 0 <= y <= 1):
            issues.append(StepIssue(RULE_CURVE_POINTS, point_path, "x and y must be within 0..1"))
        if previous_x is not None and x <= previous_x:
            issues.append(StepIssue(RULE_CURVE_POINTS, point_path, "x must be strictly increasing"))
        previous_x = x


def _check_param(path: str, spec: ParamSpec, value: Any, issues: List[StepIssue]) -> None:
    if spec.type == "curve":
        _check_curve(path, value, issues)
        return
    if not _is_number(value):
        issues.append(StepIssue(RULE_SCHEMA, path, "must be a number"))
        return
    if spec.type == "int" and float(value) != math.floor(value):
        issues.append(StepIssue(RULE_SCHEMA, path, "must be a whole number"))
        return
    if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
        issues.append(StepIssue(RULE_PARAM_RANGE, path, f"{value} is outside {spec.min}..{spec.max}"))


def validate_steps(steps: Any, ops: Optional[Mapping[str, OpSpec]] = None) -> List[StepIssue]:
    known = ops if ops is not None else CORE_OPS
    issues: List[StepIssue] = []
    if not isinstance(steps, (list, tuple)):
        return [StepIssue(RULE_SCHEMA, "steps", "must be a list")]
    if len(steps) > MAX_STEPS:
        issues.append(StepIssue(RULE_STEP_COUNT, "steps", f"{len(steps)} steps; the limit is {MAX_STEPS}"))
    seen_spatial = False
    for index, step in enumerate(steps[: MAX_STEPS * 2]):
        path = f"steps[{index}]"
        if not isinstance(step, Mapping):
            issues.append(StepIssue(RULE_SCHEMA, path, "must be an object"))
            continue
        op_id = step.get("op")
        if not isinstance(op_id, str) or not op_id:
            issues.append(StepIssue(RULE_SCHEMA, f"{path}.op", "is required and must be a string"))
            continue
        spec = known.get(op_id)
        if spec is None:
            issues.append(StepIssue(RULE_OP_UNKNOWN, f"{path}.op", f"unknown op '{op_id}'"))
            continue
        if "enabled" in step and not isinstance(step["enabled"], bool):
            issues.append(StepIssue(RULE_SCHEMA, f"{path}.enabled", "must be true or false"))
        for key, value in step.items():
            if key in ("op", "enabled"):
                continue
            param = spec.param(key)
            if param is None:
                issues.append(StepIssue(RULE_PARAM_UNKNOWN, f"{path}.{key}", f"op '{op_id}' has no param '{key}'"))
                continue
            _check_param(f"{path}.{key}", param, value, issues)
        if spec.kind == SPATIAL:
            seen_spatial = True
        elif seen_spatial:
            issues.append(
                StepIssue(
                    RULE_STEP_ORDER, f"{path}.op", f"colour op '{op_id}' follows a spatial op; colour steps come first"
                )
            )
    return issues


def resolve_params(spec: OpSpec, step: Mapping[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for param in spec.params:
        value = step.get(param.id, param.default)
        values[param.id] = [list(point) for point in value] if param.type == "curve" else value
    return values


def is_noop(spec: OpSpec, step: Mapping[str, Any]) -> bool:
    if spec.kind == COLOUR and not spec.params:
        return False
    resolved = resolve_params(spec, step)
    return all(resolved[param.id] == param.default for param in spec.params)


def split_steps(
    steps: Sequence[Mapping[str, Any]], ops: Optional[Mapping[str, OpSpec]] = None
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    known = ops if ops is not None else CORE_OPS
    colour: List[Mapping[str, Any]] = []
    spatial: List[Mapping[str, Any]] = []
    for step in steps:
        if step.get("enabled", True) is False:
            continue
        spec = known.get(step.get("op"))
        if spec is None:
            continue
        (spatial if spec.kind == SPATIAL else colour).append(step)
    return colour, spatial
