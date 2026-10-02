import math
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.features.cloud.capability_rules import find_input, find_param
from src.features.cloud.contracts import (
    PARAM_DURATION_S,
    ROLE_FIRST_FRAME,
    ROLE_LAST_FRAME,
    TASK_KINDS,
    CloudModelSpec,
)
from src.features.cloud.costs import estimate

TEXT_TO_VIDEO = "txt2video"
IMAGE_TO_VIDEO = "img2video"
DEFAULT_FPS = 24
MAX_LISTED_DURATIONS = 120
MAX_ESTIMATE_SHOTS = 64
UNKNOWN_PRICE_MESSAGE = "This model's price is not known, so the cost cannot be estimated."
PARTLY_KNOWN_PRICE_MESSAGE = "Part of this model's price is not known, so the estimate only covers what is."


class DirectorEstimateError(ValueError):
    pass


def _whole(value: float) -> Any:
    return int(value) if float(value).is_integer() else value


def allowed_durations(spec: CloudModelSpec) -> List[Any]:
    param = find_param(spec, PARAM_DURATION_S, None)
    if param is None:
        return []
    values: List[float] = []
    if param.kind == "enum":
        for value in param.values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0:
                values.append(number)
    elif param.kind == "range" and param.minimum is not None and param.maximum is not None:
        step = float(param.step or 1)
        if step <= 0:
            return []
        current = float(param.minimum)
        while current <= float(param.maximum) + 1e-9 and len(values) < MAX_LISTED_DURATIONS:
            if current > 0:
                values.append(round(current, 6))
            current += step
    return [_whole(value) for value in sorted(set(values))]


def _default_duration(spec: CloudModelSpec, durations: List[Any]) -> Any:
    param = find_param(spec, PARAM_DURATION_S, None)
    default = param.default if param is not None else None
    try:
        default = float(default) if default is not None else None
    except (TypeError, ValueError):
        default = None
    if default is not None and any(abs(default - value) < 1e-6 for value in durations):
        return _whole(default)
    return durations[0]


def is_director_model(spec: CloudModelSpec) -> bool:
    return "video" in spec.outputs and bool(spec.tasks & {TEXT_TO_VIDEO, IMAGE_TO_VIDEO})


def director_overlay(spec: Optional[CloudModelSpec]) -> Optional[Dict[str, Any]]:
    if spec is None or not is_director_model(spec):
        return None
    declared = dict(spec.director or {})
    declared_limits = declared.get("limits") if isinstance(declared.get("limits"), Mapping) else {}

    limits: Dict[str, Any] = {}
    durations = allowed_durations(spec)
    if durations:
        limits["durations"] = durations
        limits["default_duration"] = _default_duration(spec, durations)
        limits["max_duration"] = durations[-1]
        limits["default_fps"] = DEFAULT_FPS
    limits.update(declared_limits)

    image_task = IMAGE_TO_VIDEO in spec.tasks
    first_frame = find_input(spec, ROLE_FIRST_FRAME, IMAGE_TO_VIDEO) if image_task else None
    last_frame = find_input(spec, ROLE_LAST_FRAME, IMAGE_TO_VIDEO) if image_task else None

    modes: Dict[str, Any] = {}
    director: Dict[str, Any] = {}
    if TEXT_TO_VIDEO not in spec.tasks:
        modes["t2v"] = None
    if first_frame is None:
        modes["i2v"] = None
        modes["flf"] = None
        director["keyframes"] = None
        director["continuation"] = None
        director["continue_from_video"] = False
    else:
        director["continue_from_video"] = True
        if last_frame is None:
            modes["flf"] = None
    if isinstance(limits.get("max_duration"), (int, float)):
        fps = limits.get("default_fps") or DEFAULT_FPS
        director["max_frames_per_segment"] = int(math.ceil(float(limits["max_duration"]) * float(fps)))

    declared_modes = declared.get("modes") if isinstance(declared.get("modes"), Mapping) else {}
    declared_director = declared_modes.get("director")
    for name, value in declared_modes.items():
        if name != "director":
            modes[name] = value
    if "director" in declared_modes and not isinstance(declared_director, Mapping):
        modes["director"] = declared_director
    else:
        director.update(declared_director or {})
        if director:
            modes["director"] = director

    overlay: Dict[str, Any] = {key: value for key, value in declared.items() if key not in ("limits", "modes")}
    overlay.setdefault("model_label", spec.label)
    if limits:
        overlay["limits"] = limits
    if modes:
        overlay["modes"] = modes
    return overlay


def shot_task(has_image: bool) -> str:
    return IMAGE_TO_VIDEO if has_image else TEXT_TO_VIDEO


def estimate_shots(spec: CloudModelSpec, shots: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not shots:
        raise DirectorEstimateError("Add at least one shot to estimate.")
    if len(shots) > MAX_ESTIMATE_SHOTS:
        raise DirectorEstimateError(f"At most {MAX_ESTIMATE_SHOTS} shots can be estimated at once.")
    total = Decimal(0)
    priced = 0
    partial = False
    rows: List[Dict[str, Any]] = []
    for index, shot in enumerate(shots):
        task = shot.get("task") or TEXT_TO_VIDEO
        if task not in TASK_KINDS:
            raise DirectorEstimateError(f"Shot {index + 1} names an unknown task '{task}'.")
        params = shot.get("params") or {}
        if not isinstance(params, Mapping):
            raise DirectorEstimateError(f"Shot {index + 1} needs its settings as an object.")
        result = estimate(spec, task, params, 1)
        if result is None:
            rows.append({"index": index, "task": task, "amount_usd": None, "lines": [], "skipped_units": []})
            continue
        priced += 1
        skipped = list(result.detail.get("skipped_units") or [])
        partial = partial or bool(skipped)
        total += result.amount_usd
        rows.append({
            "index": index,
            "task": task,
            "amount_usd": str(result.amount_usd),
            "lines": list(result.detail.get("lines") or []),
            "skipped_units": skipped,
        })
    known = priced == len(rows) and not partial
    if priced == 0:
        message: Optional[str] = UNKNOWN_PRICE_MESSAGE
    elif not known:
        message = PARTLY_KNOWN_PRICE_MESSAGE
    else:
        message = None
    return {
        "known": known,
        "total_usd": str(total) if priced else None,
        "shots": rows,
        "shot_count": len(rows),
        "source": "estimate",
        "message": message,
    }
