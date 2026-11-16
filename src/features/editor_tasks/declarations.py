from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.features.presets.quantity import mode_quantity_fields
from src.features.presets.templates import FieldTemplate, ModeTemplate, PresetTemplate
from src.platform.filesystem.yaml_io import load_yaml_file

DECLARATION_FILE = "editor.yml"
MASK_SUFFIX = "_inpaint_mask"
DEFAULT_AREA = 1048576
MIN_AREA = 262144
MAX_AREA = 4194304
QUANTITY_FIELD_TYPES = frozenset({"stepper", "number", "integer", "slider"})

Strategy = Literal["crop", "mask", "whole"]


class MediaListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    index: int = Field(default=0, ge=0)


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: Union[str, MediaListInput]
    mask: Optional[str] = None


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: int = Field(default=DEFAULT_AREA, ge=MIN_AREA, le=MAX_AREA)
    multiple: Literal[8, 16, 32, 64] = 16
    field: Optional[str] = None
    buckets: Optional[List[List[int]]] = None

    @field_validator("buckets")
    @classmethod
    def _pairs(cls, value: Optional[List[List[int]]]) -> Optional[List[List[int]]]:
        if value is not None and any(len(pair) != 2 or min(pair) <= 0 for pair in value):
            raise ValueError("buckets must be [width, height] pairs")
        return value


class TaskDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task: str = Field(min_length=1)
    region: Strategy
    inputs: Inputs
    size: Size = Field(default_factory=Size)
    context: float = Field(default=0.5, ge=0.0, le=4.0)
    set_values: Dict[str, Any] = Field(default_factory=dict, alias="set")
    owned: List[str] = Field(default_factory=list)
    seed_field: Optional[str] = None
    quantity_field: Optional[str] = None
    label: Optional[str] = Field(default=None, min_length=1)


class EditorDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    tasks: List[TaskDeclaration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_tasks(self) -> "EditorDeclaration":
        seen: Set[str] = set()
        for declaration in self.tasks:
            if declaration.task in seen:
                raise ValueError(f"task '{declaration.task}' is declared twice")
            seen.add(declaration.task)
        return self


@dataclass
class ParsedFile:
    declaration: Optional[EditorDeclaration] = None
    error: Optional[str] = None
    present: bool = False


@dataclass
class ModeDeclaration:
    preset_id: str
    mode: str
    tasks: List[TaskDeclaration] = dataclass_field(default_factory=list)
    errors: List[str] = dataclass_field(default_factory=list)
    warnings: List[str] = dataclass_field(default_factory=list)
    present: bool = False


def failure_text(failure: Exception) -> str:
    if isinstance(failure, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'document'}: {item['msg'].removeprefix('Value error, ')}"
            for item in failure.errors()
        )
    return str(failure)


def parse_file(path: Path) -> ParsedFile:
    if not path.is_file():
        return ParsedFile()
    try:
        document = load_yaml_file(path)
        return ParsedFile(
            declaration=EditorDeclaration.model_validate(document if document is not None else {}), present=True
        )
    except Exception as failure:
        return ParsedFile(error=failure_text(failure), present=True)


def walk_fields(fields: Optional[List[FieldTemplate]], out: Dict[str, FieldTemplate]) -> None:
    for spec in fields or []:
        if spec.name:
            out.setdefault(spec.name, spec)
        if isinstance(spec.children, list):
            walk_fields(spec.children, out)


def mode_fields(mode: ModeTemplate) -> Dict[str, FieldTemplate]:
    found: Dict[str, FieldTemplate] = {}
    for form in mode.forms:
        walk_fields(form.fields, found)
    return found


def _options(spec: FieldTemplate) -> Optional[Set[str]]:
    options = (spec.configuration or {}).get("options")
    if not isinstance(options, list):
        return None
    return {str(item.get("value")) if isinstance(item, dict) else str(item) for item in options}


def image_field_name(declaration: TaskDeclaration) -> str:
    image = declaration.inputs.image
    return image if isinstance(image, str) else image.field


def derived_seed_field(fields: Dict[str, FieldTemplate]) -> Optional[str]:
    return next((name for name, spec in fields.items() if spec.type == "seed"), None)


def derived_quantity_field(mode: ModeTemplate, fields: Dict[str, FieldTemplate]) -> Optional[str]:
    names = [name for name in mode_quantity_fields(mode) if name in fields and fields[name].type in QUANTITY_FIELD_TYPES]
    if "quantity" in names or len(names) == 1:
        return "quantity" if "quantity" in names else names[0]
    return None


def owned_fields(declaration: TaskDeclaration, mode: ModeTemplate) -> Set[str]:
    fields = mode_fields(mode)
    owned = {image_field_name(declaration), *declaration.owned, *declaration.set_values}
    if declaration.inputs.mask:
        owned.add(declaration.inputs.mask)
    if declaration.size.field:
        owned.add(declaration.size.field)
    quantity = declaration.quantity_field or derived_quantity_field(mode, fields)
    if quantity:
        owned.add(quantity)
    return owned


def _problems(declaration: TaskDeclaration, fields: Dict[str, FieldTemplate]) -> List[str]:
    problems: List[str] = []
    image = declaration.inputs.image
    name = image_field_name(declaration)
    spec = fields.get(name)
    if spec is None:
        problems.append(f"inputs.image names '{name}', which is not a field of the mode")
    elif isinstance(image, str) and spec.type != "image":
        problems.append(f"inputs.image '{name}' is a {spec.type} field, not an image field")
    elif not isinstance(image, str) and spec.type != "media":
        problems.append(f"inputs.image '{name}' is a {spec.type} field, but an index needs a media field")
    mask = declaration.inputs.mask
    if declaration.region == "mask":
        if not mask:
            problems.append("region: mask needs inputs.mask")
    if mask:
        if mask != f"{name}{MASK_SUFFIX}":
            problems.append(f"inputs.mask must be '{name}{MASK_SUFFIX}'")
        elif spec is None or not (spec.configuration or {}).get("allow_inpaint"):
            problems.append(f"inputs.mask needs '{name}' to set allow_inpaint: true")
    if declaration.size.field:
        size_spec = fields.get(declaration.size.field)
        if size_spec is None:
            problems.append(f"size.field names '{declaration.size.field}', which is not a field of the mode")
        elif size_spec.type != "resolution":
            problems.append(f"size.field '{declaration.size.field}' is a {size_spec.type} field, not a resolution field")
    for key, value in declaration.set_values.items():
        target = fields.get(key)
        if target is None:
            problems.append(f"set key '{key}' is not a field of the mode")
            continue
        options = _options(target)
        if target.type == "select" and options is not None and str(value) not in options:
            problems.append(f"set value '{value}' is not an option of '{key}'")
        elif target.type == "checkbox" and not isinstance(value, bool):
            problems.append(f"set value for '{key}' must be true or false")
    for label, value in (("seed_field", declaration.seed_field), ("quantity_field", declaration.quantity_field)):
        if value and value not in fields:
            problems.append(f"{label} names '{value}', which is not a field of the mode")
    for value in declaration.owned:
        if value not in fields:
            problems.append(f"owned names '{value}', which is not a field of the mode")
    return problems


def validate_mode(
    preset_id: str,
    mode_name: str,
    mode: ModeTemplate,
    parsed: ParsedFile,
    prompt_need: Callable[[str], Optional[str]],
    owner: Optional[str],
    promptless: Any = (),
) -> ModeDeclaration:
    result = ModeDeclaration(preset_id=preset_id, mode=mode_name, present=parsed.present)
    if parsed.error is not None:
        result.errors.append(f"modes/{mode_name}/{DECLARATION_FILE}: {parsed.error}")
        return result
    if parsed.declaration is None:
        return result
    fields = mode_fields(mode)
    promptless_modes = {item for item in promptless if isinstance(item, str)} if isinstance(promptless, (list, tuple, set)) else set()
    prefix = f"modes/{mode_name}/{DECLARATION_FILE}"
    for declaration in parsed.declaration.tasks:
        task_id = declaration.task
        if owner is not None and "." in task_id and not task_id.startswith(f"{owner}."):
            result.errors.append(f"{prefix}: task '{task_id}' cannot be offered by a mode of plugin '{owner}'")
            continue
        need = prompt_need(task_id)
        if need is None:
            if "." in task_id:
                result.warnings.append(
                    f"{prefix}: task '{task_id}' belongs to a plugin that is not installed or enabled; the offer is skipped"
                )
            else:
                result.errors.append(f"{prefix}: unknown task '{task_id}'")
            continue
        problems = _problems(declaration, fields)
        if problems:
            result.errors.extend(f"{prefix}: task '{task_id}': {problem}" for problem in problems)
            continue
        if declaration.region == "crop" and any(
            (spec.configuration or {}).get("allow_inpaint") for spec in fields.values() if spec.type == "image"
        ):
            result.warnings.append(
                f"{prefix}: task '{task_id}': region: crop on a mode with a mask input; region: mask usually gives a better seam"
            )
        if mode_name in promptless_modes and need == "required":
            result.warnings.append(f"{prefix}: task '{task_id}' requires a prompt, and this mode takes none")
        result.tasks.append(declaration)
    return result


def declaration_path(template: PresetTemplate, mode_name: str, mode: ModeTemplate, contributed: Dict[Tuple[str, str], Path]) -> Optional[Path]:
    if mode.source_plugin is None:
        return Path(template.path) / "modes" / mode_name / DECLARATION_FILE
    root = contributed.get((mode.source_plugin, template.id))
    return None if root is None else root / "modes" / mode_name / DECLARATION_FILE


def mode_owner(template: PresetTemplate, mode: ModeTemplate) -> Optional[str]:
    if mode.source_plugin is not None:
        return mode.source_plugin
    origin = template.origin if isinstance(template.origin, dict) else {}
    return origin.get("plugin_id")


def _stamp(path: Optional[Path]) -> Optional[Tuple[int, int]]:
    if path is None:
        return None
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


class DeclarationCache:
    def __init__(
        self,
        templates: Callable[[], Iterable[PresetTemplate]],
        contributions: Callable[[], Dict[Tuple[str, str], Path]],
    ):
        self._templates = templates
        self._contributions = contributions
        self._held: List[PresetTemplate] = []
        self._stamps: List[Tuple] = []
        self._parsed: Dict[Tuple[str, str], ParsedFile] = {}

    def __call__(self) -> Dict[Tuple[str, str], ParsedFile]:
        templates = list(self._templates())
        contributed = self._contributions()
        paths = {
            (template.id, name): declaration_path(template, name, mode, contributed)
            for template in templates
            for name, mode in (template.modes or {}).items()
        }
        stamps = [(key, str(path), _stamp(path)) for key, path in sorted(paths.items(), key=lambda item: item[0])]
        same = (
            len(templates) == len(self._held)
            and all(new is old for new, old in zip(templates, self._held))
            and stamps == self._stamps
        )
        if not same:
            self._parsed = {key: parse_file(path) for key, path in paths.items() if path is not None}
            self._held, self._stamps = templates, stamps
        return self._parsed
