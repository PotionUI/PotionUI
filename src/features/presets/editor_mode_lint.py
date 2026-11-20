from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.features.presets.editor_modes import EDITOR_MODES_DIR, input_names
from src.features.presets.extensions import walk_fields
from src.features.presets.mode_labels import known_icon_names
from src.features.presets.templates import FieldTemplate, ModeTemplate, PresetTemplate

LEFTOVER_EDITOR_YML_WARNS = True
DECLARATION_FILE = "editor.yml"
MASK_SUFFIX = "_inpaint_mask"
MEDIA_TYPES = frozenset({"image", "video", "audio", "media"})
RANGE_TYPES = frozenset({"slider", "number", "integer", "stepper"})

Issue = Tuple[str, str]


def _fields(mode: ModeTemplate) -> Dict[str, FieldTemplate]:
    found: Dict[str, FieldTemplate] = {}
    for form in mode.forms:
        for field, _ in walk_fields(form.fields):
            if field.name:
                found.setdefault(field.name, field)
    return found


def _options(spec: FieldTemplate) -> Optional[set]:
    options = (spec.configuration or {}).get("options")
    if not isinstance(options, list):
        return None
    return {str(item.get("value")) if isinstance(item, dict) else str(item) for item in options}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fixed_value_problems(name: str, spec: FieldTemplate, value: Any) -> List[str]:
    if spec.type == "select":
        options = _options(spec)
        if options is not None and str(value) not in options:
            return [f"fixed value '{value}' is not an option of '{name}'"]
    elif spec.type == "checkbox":
        if not isinstance(value, bool):
            return [f"fixed value for '{name}' must be true or false"]
    elif spec.type in RANGE_TYPES:
        if not _number(value):
            return [f"fixed value for '{name}' must be a number"]
        configuration = spec.configuration or {}
        low, high = configuration.get("min"), configuration.get("max")
        if _number(low) and value < low or _number(high) and value > high:
            return [f"fixed value {value} for '{name}' is outside its range {low} to {high}"]
    return []


def scale_problems(spec: Any, fields: Dict[str, FieldTemplate]) -> List[str]:
    name = spec.inputs.get("scale")
    found = fields.get(name) if name else None
    if found is not None and found.type not in RANGE_TYPES:
        return [f"inputs.scale '{name}' is a {found.type} field, not a number field"]
    if spec.task == "upscale" and not name and not spec.size.get("field"):
        return ["an upscale mode needs inputs.scale or size.field to receive the size it must produce"]
    return []


def strength_problems(strength: Optional[Dict[str, Any]], fields: Dict[str, FieldTemplate]) -> List[str]:
    if not strength:
        return []
    name = strength["field"]
    spec = fields.get(name)
    if spec is None:
        return [f"strength.field names '{name}', which is not a field of the mode"]
    if spec.type not in RANGE_TYPES:
        return [f"strength.field '{name}' is a {spec.type} field, not a number field"]
    configuration = spec.configuration or {}
    low, high = configuration.get("min"), configuration.get("max")
    if _number(low) and strength["min"] < low or _number(high) and strength["max"] > high:
        return [f"strength range {strength['min']} to {strength['max']} is outside the range {low} to {high} of '{name}'"]
    return []

def source_problems(
    source: Any, fields: Dict[str, FieldTemplate], editors: Iterable[str]
) -> List[str]:
    name = source["field"] if isinstance(source, dict) else source
    spec = fields.get(name)
    if spec is None:
        return [f"inputs.source names '{name}', which is not a field of the mode"]
    taken = set(editors)
    if isinstance(source, dict):
        if spec.type != "media" or not taken:
            return [f"inputs.source '{name}' is a {spec.type} field, but an index needs a media field"]
        return []
    if spec.type not in taken:
        wanted = " or ".join(sorted(taken)) or "a medium"
        return [f"inputs.source '{name}' is a {spec.type} field, not a {wanted} field"]
    return []


def mask_problems(inputs: Dict[str, Any], fields: Dict[str, FieldTemplate]) -> List[str]:
    mask = inputs.get("mask")
    if not mask:
        return []
    source = inputs["source"]
    name = source["field"] if isinstance(source, dict) else source
    if mask != f"{name}{MASK_SUFFIX}":
        return [f"inputs.mask must be '{name}{MASK_SUFFIX}'"]
    spec = fields.get(name)
    if spec is None or not (spec.configuration or {}).get("allow_inpaint"):
        return [f"inputs.mask needs '{name}' to set allow_inpaint: true"]
    return []


def region_problems(region: str, editors: Tuple[str, ...]) -> List[str]:
    if region == "range" and "video" not in editors:
        return ["region: range is for a task of the video editor"]
    if region != "range" and "video" in editors and "image" not in editors:
        return [f"region: {region} is for pictures; a video task takes region: range"]
    return []

def size_problems(size: Dict[str, Any], fields: Dict[str, FieldTemplate]) -> List[str]:
    name = size.get("field")
    if not name:
        return []
    spec = fields.get(name)
    if spec is None:
        return [f"size.field names '{name}', which is not a field of the mode"]
    if spec.type != "resolution":
        return [f"size.field '{name}' is a {spec.type} field, not a resolution field"]
    return []


def task_problems(
    task: str,
    needs: Callable[[str], Optional[str]],
    owner: Optional[str],
) -> Tuple[List[str], List[str]]:
    if owner is not None and "." in task and not task.startswith(f"{owner}."):
        return [f"task '{task}' cannot be offered by an editor mode of plugin '{owner}'"], []
    if needs(task) is not None:
        return [], []
    if "." in task:
        return [], [f"task '{task}' belongs to a plugin that is not installed or enabled; the editor mode is skipped"]
    return [f"unknown task '{task}'"], []


def lint_editor_mode(
    template: PresetTemplate,
    name: str,
    mode: ModeTemplate,
    needs: Callable[[str], Optional[str]],
    editors_of: Callable[[str], Iterable[str]],
    owner: Optional[str],
) -> List[Issue]:
    spec = mode.editor
    if spec is None:
        return []
    prefix = f"{EDITOR_MODES_DIR}/{name}/editor_mode.yml"
    fields = _fields(mode)
    issues: List[Issue] = []

    icons = known_icon_names()
    if spec.icon and icons is not None and spec.icon not in icons:
        issues.append(("warning", f"{prefix}: icon \"{spec.icon}\" is not an icon in the app's Icon set"))

    errors, warnings = task_problems(spec.task, needs, owner)
    issues.extend(("error", f"{prefix}: {message}") for message in errors)
    issues.extend(("warning", f"{prefix}: {message}") for message in warnings)
    if errors or warnings:
        return issues

    problems = [
        *region_problems(spec.region, tuple(editors_of(spec.task))),
        *source_problems(spec.inputs["source"], fields, editors_of(spec.task)),
        *mask_problems(spec.inputs, fields),
        *size_problems(spec.size, fields),
        *strength_problems(spec.strength, fields),
    ]
    for key in ("quantity", "seed", "scale"):
        value = spec.inputs.get(key)
        if value and value not in fields:
            problems.append(f"inputs.{key} names '{value}', which is not a field of the mode")
    problems.extend(scale_problems(spec, fields))
    for key, value in spec.fixed.items():
        problems.extend(fixed_value_problems(key, fields[key], value) if key in fields else [])
    issues.extend(("error", f"{prefix}: {message}") for message in problems)

    promptless = {item for item in (template.vars or {}).get("promptless_modes") or [] if isinstance(item, str)}
    if spec.from_mode in promptless and needs(spec.task) == "required":
        issues.append(("warning", f"{prefix}: task '{spec.task}' requires a prompt, and the mode it derives from takes none"))

    if spec.instruction and needs(spec.task) != "none":
        issues.append(("warning", f"{prefix}: instruction is only used by a task that takes no prompt; '{spec.task}' does"))

    owned = {*input_names(spec.inputs), *spec.fixed, *spec.hide}
    for key, field in fields.items():
        if field.type in MEDIA_TYPES and key not in owned and not key.endswith(MASK_SUFFIX):
            issues.append(("info", f"{prefix}: Settings will show the {field.type} field '{key}'"))
    return issues


def generation_mode_name_problems(template: PresetTemplate) -> List[Issue]:
    return [
        ("error", f"modes/{mode}: a mode name must not contain ':'")
        for mode in (template.modes or {})
        if ":" in mode
    ]


def leftover_declarations(template: PresetTemplate) -> List[Issue]:
    if not LEFTOVER_EDITOR_YML_WARNS:
        return []
    issues: List[Issue] = []
    for mode_name in (template.modes or {}):
        if not (Path(template.path) / "modes" / mode_name / DECLARATION_FILE).is_file():
            continue
        successors = [
            name
            for name, mode in template.editor_modes.items()
            if mode.editor is not None and (mode.editor.from_mode == mode_name or name == mode_name)
        ]
        if successors:
            issues.append((
                "warning",
                f"modes/{mode_name}/{DECLARATION_FILE}: no longer read; "
                f"editor_modes/{successors[0]}/editor_mode.yml replaces it, delete this file",
            ))
    return issues


def preset_editor_errors(load_errors: Dict[str, List[str]], preset_dir: Path) -> List[Issue]:
    resolved = preset_dir.resolve()
    issues: List[Issue] = []
    for key, messages in load_errors.items():
        location = Path(key)
        if location.parent.name == EDITOR_MODES_DIR and location.parent.parent.resolve() == resolved:
            issues.extend(("error", message) for message in messages)
    return issues
