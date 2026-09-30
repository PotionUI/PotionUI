from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable, List, Mapping, Optional

from src.features.cloud.contracts import CloudModelSpec, MediaInputSpec, ParamSpec

CLOUD_PIPE_NAME = "cloud_generate"


@dataclass(frozen=True)
class CapabilityBinding:
    field: str
    model_field: str
    param: Optional[str] = None
    input: Optional[str] = None


def mode_task(preset_template: Any, mode: str) -> Optional[str]:
    mode_data = (getattr(preset_template, "modes", None) or {}).get(mode)
    for pipe in getattr(mode_data, "pipes", None) or []:
        if getattr(pipe, "name", None) != CLOUD_PIPE_NAME:
            continue
        task = (getattr(pipe, "configuration", None) or {}).get("task")
        if isinstance(task, str) and task and "{{" not in task and "{%" not in task:
            return task
    return None


def applies_to_task(tasks: Iterable[str], task: Optional[str]) -> bool:
    tasks = frozenset(tasks)
    return not tasks or task is None or task in tasks


def params_for_task(spec: CloudModelSpec, task: Optional[str]) -> List[ParamSpec]:
    return [param for param in spec.params if applies_to_task(param.tasks, task)]


def inputs_for_task(spec: CloudModelSpec, task: Optional[str]) -> List[MediaInputSpec]:
    return [media for media in spec.inputs if applies_to_task(media.tasks, task)]


def find_param(spec: CloudModelSpec, name: str, task: Optional[str]) -> Optional[ParamSpec]:
    return next((param for param in params_for_task(spec, task) if param.name == name), None)


def find_input(spec: CloudModelSpec, role: str, task: Optional[str]) -> Optional[MediaInputSpec]:
    return next((media for media in inputs_for_task(spec, task) if media.role == role), None)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_of(value: Any) -> Optional[float]:
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _label(param: ParamSpec) -> str:
    return param.label or param.name


def validate_param_value(param: ParamSpec, value: Any) -> Optional[str]:
    if value is None or value == "":
        return f"{_label(param)} is required by this model" if param.required else None
    if param.kind == "enum":
        allowed = {str(item) for item in param.values}
        if value in param.values or str(value) in allowed:
            return None
        return f"'{value}' is not offered by this model. Choose one of: {', '.join(str(item) for item in param.values)}"
    if param.kind == "range":
        number = _number_of(value)
        if number is None:
            return "must be a number"
        if param.integer and number != int(number):
            return "must be a whole number"
        if param.minimum is not None and number < param.minimum:
            return f"must be at least {_trim(param.minimum)}"
        if param.maximum is not None and number > param.maximum:
            return f"must be at most {_trim(param.maximum)}"
        return None
    if param.kind == "boolean":
        return None if isinstance(value, bool) else "must be true or false"
    if param.kind == "text":
        return None if isinstance(value, str) else "must be text"
    return None


def coerce_param_value(param: ParamSpec, value: Any) -> Any:
    if param.kind != "range":
        return value
    number = _number_of(value)
    if number is None:
        return value
    return int(number) if param.integer else number


def _trim(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def media_items(value: Any) -> List[Any]:
    if value is None or value == "" or value == []:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def media_path(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("relative_path") or item.get("path") or item.get("name") or "")
    return str(item or "")


def _extension(path: str) -> str:
    name = PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _format_key(entry: str) -> str:
    key = entry.lower().strip()
    key = key.rsplit("/", 1)[-1].lstrip(".")
    return "jpg" if key == "jpeg" else key


def media_problems(media: MediaInputSpec, items: List[Any]) -> List[str]:
    problems: List[str] = []
    count = len(items)
    role = media.role.replace("_", " ")
    if count < media.min_items:
        problems.append(f"needs at least {media.min_items} {role} file(s), got {count}")
    if count > media.max_items:
        problems.append(f"accepts at most {media.max_items} {role} file(s), got {count}")
    if media.formats:
        allowed = {_format_key(entry) for entry in media.formats}
        for item in items:
            extension = _format_key(_extension(media_path(item)))
            if extension and extension not in allowed:
                problems.append(f"'{media_path(item)}' is not an accepted {role} format ({', '.join(media.formats)})")
    return problems
