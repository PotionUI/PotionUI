import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator

from src.features.presets.extensions import (
    PresetExtension,
    _group_field_dicts,
    _mark_source,
    _merge_field_group,
    _merge_pipes,
    walk_fields,
)
from src.features.presets.schema import ExtensionFieldGroup, ExtensionPipeOp, _format_errors
from src.features.presets.templates import (
    EditorModeSpec,
    FieldTemplate,
    ModeTemplate,
    PipeTemplate,
    PresetTemplate,
    default_form_name,
    find_mode,
    is_editor_key,
    sorted_forms,
)
from src.features.presets.mode_labels import mode_display_icon
from src.features.presets.quantity import mode_quantity_fields
from src.platform.filesystem.yaml_io import load_yaml_file
from src.platform.observability.logger import logger

EDITOR_MODES_DIR = "editor_modes"
EDITOR_MODE_FILE = "editor_mode.yml"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TASK_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)?$")
QUANTITY_FIELD_TYPES = frozenset({"stepper", "number", "integer", "slider"})
MIN_AREA = 262144
MAX_AREA = 4194304
DEFAULT_AREA = 1048576
CHANGEABLE = ("label", "description", "ai_hint", "audience")


class MediaListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    index: int = Field(default=0, ge=0)


class EditorInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Union[str, MediaListInput]
    mask: Optional[str] = None
    quantity: Optional[str] = None
    seed: Optional[str] = None

    @field_validator("source")
    @classmethod
    def _named(cls, value: Union[str, MediaListInput]) -> Union[str, MediaListInput]:
        if isinstance(value, str) and not value:
            raise ValueError("must name a field")
        return value


class EditorSize(BaseModel):
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


class EditorStrength(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    default: float
    min: float
    max: float

    @model_validator(mode="after")
    def _ordered(self) -> "EditorStrength":
        if self.min >= self.max:
            raise ValueError("min must be less than max")
        if not self.min <= self.default <= self.max:
            raise ValueError("default must be between min and max")
        return self


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[StrictStr] = None
    description: Optional[StrictStr] = None
    ai_hint: Optional[StrictStr] = None
    default: Any = None
    audience: Optional[Literal["simple", "advanced"]] = None

    @model_validator(mode="after")
    def _changes_something(self) -> "FieldChange":
        if not self.model_fields_set:
            raise ValueError("a change names at least one of label, description, ai_hint, default, audience")
        return self

    def values(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in self.model_dump(exclude_unset=True).items()
            if key == "default" or value is not None
        }


class EditorFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hide: List[StrictStr] = Field(default_factory=list)
    change: Dict[str, FieldChange] = Field(default_factory=dict)
    add: List[ExtensionFieldGroup] = Field(default_factory=list)

    @field_validator("hide")
    @classmethod
    def _unique(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("hide lists a field more than once")
        return value

    def is_empty(self) -> bool:
        return not (self.hide or self.change or self.add)


class EditorModeFile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1]
    task: str
    from_mode: Optional[str] = Field(default=None, alias="from")
    label: Optional[str] = Field(default=None, min_length=1)
    short_description: Optional[str] = Field(default=None, min_length=1)
    icon: Optional[str] = Field(default=None, min_length=1)
    order: int = 0
    region: Literal["crop", "mask", "whole", "range"]
    inputs: EditorInputs
    size: EditorSize = Field(default_factory=EditorSize)
    context: float = Field(default=0.5, ge=0.0, le=4.0)
    max_range_s: Optional[float] = Field(default=None, gt=0)
    instruction: Optional[str] = Field(default=None, min_length=1)
    strength: Optional[EditorStrength] = None
    fixed: Dict[str, Any] = Field(default_factory=dict)
    fields: EditorFields = Field(default_factory=EditorFields)
    pipeline: List[ExtensionPipeOp] = Field(default_factory=list)

    @field_validator("task")
    @classmethod
    def _task_shape(cls, value: str) -> str:
        if not TASK_PATTERN.match(value):
            raise ValueError("must be a core task id or '<plugin>.<name>'")
        return value

    @field_validator("from_mode")
    @classmethod
    def _from_named(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("must name a generation mode")
        return value

    @model_validator(mode="after")
    def _mask_region_has_a_mask(self) -> "EditorModeFile":
        if self.region == "mask" and not self.inputs.mask:
            raise ValueError("region: mask needs inputs.mask")
        if self.region == "range" and self.inputs.mask:
            raise ValueError("region: range takes no inputs.mask")
        return self

    @model_validator(mode="after")
    def _cap_is_for_ranges(self) -> "EditorModeFile":
        if self.max_range_s is not None and self.region != "range":
            raise ValueError("max_range_s is only valid with region: range")
        return self

    @model_validator(mode="after")
    def _derived_only_keys(self) -> "EditorModeFile":
        if self.from_mode is None and not self.fields.is_empty():
            raise ValueError("`fields` is only valid with `from`")
        if self.from_mode is None and self.pipeline:
            raise ValueError("`pipeline` operations are only valid with `from`")
        return self

    def input_fields(self) -> List[str]:
        return input_names(self.inputs.model_dump())


def validate_editor_mode_file(data: Any, prefix: str) -> Tuple[Optional[EditorModeFile], List[str]]:
    try:
        return EditorModeFile.model_validate(data), []
    except ValidationError as exc:
        return None, _format_errors(prefix, exc)


@dataclass
class EditorModeCandidate:
    name: str
    directory: Path
    preset_root: Path
    plugin_id: Optional[str]
    error_key: str

    @property
    def label(self) -> str:
        return f"{EDITOR_MODES_DIR}/{self.name}"


@dataclass
class EditorModeBuilders:
    load_mode: Callable[[EditorModeCandidate], Tuple[Optional[ModeTemplate], List[str]]]
    load_pipes: Callable[[EditorModeCandidate], Tuple[Optional[List[PipeTemplate]], List[str]]]
    build_fields: Callable[[List[Dict[str, Any]], EditorModeCandidate, str], List[FieldTemplate]]
    mode_checks: Callable[[PresetTemplate, str, ModeTemplate], List[str]]


def editor_overrides(spec: EditorModeSpec, with_inputs: bool = False) -> Dict[str, Dict[str, Any]]:
    overrides: Dict[str, Dict[str, Any]] = {name: {"visible": False} for name in spec.hide}
    for name, value in spec.fixed.items():
        overrides[name] = {"default": value, "visible": False}
    if with_inputs:
        for name in input_field_names(spec):
            overrides.setdefault(name, {"visible": False})
    return overrides


def input_names(inputs: Dict[str, Any]) -> List[str]:
    source = inputs.get("source")
    names = [source["field"] if isinstance(source, dict) else source]
    names.extend(inputs.get(key) for key in ("mask", "quantity", "seed"))
    return [name for name in names if name]


def input_field_names(spec: EditorModeSpec) -> List[str]:
    quantity = spec.inputs.get("quantity")
    return [name for name in input_names(spec.inputs) if name != quantity]


def form_overrides_for(
    template: Any,
    mode_key: Optional[str],
    stored: Optional[Dict[str, Any]],
    with_inputs: bool = False,
) -> Dict[str, Any]:
    stored = stored or {}
    if not is_editor_key(mode_key):
        return stored.get(mode_key, {})
    mode = find_mode(template, mode_key)
    spec = getattr(mode, "editor", None)
    if spec is None:
        return {}
    merged = dict(stored.get(spec.from_mode) or {}) if spec.from_mode else {}
    merged.update(editor_overrides(spec, with_inputs))
    return merged


def shown_fields(mode: ModeTemplate) -> List[Dict[str, Any]]:
    from src.features.presets.form_overrides import apply_overrides_to_fields

    spec = mode.editor
    forms = sorted_forms(mode)
    if spec is None or not forms:
        return []
    name = default_form_name(mode)
    form = next((item for item in forms if item.name == name), forms[0])
    visible = apply_overrides_to_fields(form.fields, editor_overrides(spec, with_inputs=True))
    return [
        {"name": field.name, "type": field.type, "label": field.label}
        for field, _ in walk_fields(visible)
        if field.name
    ]


def _inventory(mode: ModeTemplate) -> Dict[str, FieldTemplate]:
    found: Dict[str, FieldTemplate] = {}
    for form in mode.forms:
        for field, _ in walk_fields(form.fields):
            if field.name:
                found.setdefault(field.name, field)
    return found


def _overlap_problems(file: EditorModeFile) -> List[str]:
    claims: Dict[str, List[str]] = {}
    for name in file.input_fields():
        claims.setdefault(name, []).append("inputs")
    for name in file.fixed:
        claims.setdefault(name, []).append("fixed")
    if file.strength is not None and file.strength.field in claims:
        claims[file.strength.field].append("strength")
    for name in file.fields.hide:
        claims.setdefault(name, []).append("hide")
    return [
        f"field '{name}' is named in more than one of {', '.join(sorted(set(where)))}"
        for name, where in claims.items()
        if len(where) > 1
    ]


def _apply_changes(mode: ModeTemplate, changes: Dict[str, FieldChange]) -> None:
    for form in mode.forms:
        for field, _ in walk_fields(form.fields):
            change = changes.get(field.name) if field.name else None
            if change is None:
                continue
            for key, value in change.values().items():
                setattr(field, key, value)


def _add_fields(
    mode: ModeTemplate,
    file: EditorModeFile,
    candidate: EditorModeCandidate,
    target: PresetTemplate,
    builders: EditorModeBuilders,
) -> List[str]:
    if not file.fields.add:
        return []
    extension = PresetExtension(
        plugin_id=candidate.plugin_id or "",
        target_preset_id=target.id,
        modes=[],
        file=candidate.directory / EDITOR_MODE_FILE,
        plugin_dir=candidate.directory.resolve(),
        declared_file=f"{candidate.label}/{EDITOR_MODE_FILE}",
    )
    problems: List[str] = []
    groups = []
    for index, group in enumerate(file.fields.add):
        dicts, errors, _ = _group_field_dicts(group, index, extension)
        if errors:
            problems.extend(errors)
            continue
        if not dicts:
            problems.append(f"{extension.declared_file}: fields.add[{index}]: adds no fields")
            continue
        try:
            templates = builders.build_fields(dicts, candidate, f"{extension.declared_file}: fields.add[{index}]")
        except Exception as exc:
            problems.append(f"{extension.declared_file}: fields.add[{index}]: {exc}")
            continue
        if candidate.plugin_id:
            _mark_source(templates, candidate.plugin_id)
        groups.append((group, templates))
    if problems:
        return problems
    for form in mode.forms:
        loc = f"{candidate.label} form '{form.name}'"
        for group, templates in groups:
            problems.extend(_merge_field_group(form.fields, group, templates, loc))
    return problems


def _derive(
    candidate: EditorModeCandidate,
    file: EditorModeFile,
    target: PresetTemplate,
    builders: EditorModeBuilders,
) -> Tuple[Optional[ModeTemplate], List[str]]:
    source = target.modes.get(file.from_mode)
    if source is None:
        return None, [f"{candidate.label}: `from` names '{file.from_mode}', which is not a mode of preset '{target.id}'"]
    if (candidate.directory / "form.yml").exists():
        return None, [f"{candidate.label}: form.yml is not allowed with `from`"]

    mode = copy.deepcopy(source)
    mode.editor = None
    inventory = _inventory(mode)
    problems = []
    for name in file.fields.hide:
        field = inventory.get(name)
        if field is None:
            problems.append(f"{candidate.label}: fields.hide names '{name}', which is not a field of mode '{file.from_mode}'")
        elif field.required and field.default is None:
            problems.append(
                f"{candidate.label}: fields.hide names '{name}', which is required and has no default; "
                f"give it a value under `fixed`"
            )
    for name in file.fields.change:
        if name not in inventory:
            problems.append(f"{candidate.label}: fields.change names '{name}', which is not a field of mode '{file.from_mode}'")
    if problems:
        return None, problems

    _apply_changes(mode, file.fields.change)
    problems.extend(_add_fields(mode, file, candidate, target, builders))

    pipeline_file = candidate.directory / "pipeline.yml"
    if pipeline_file.exists():
        if file.pipeline:
            problems.append(f"{candidate.label}: `pipeline` operations are not allowed beside a pipeline.yml")
        else:
            pipes, errors = builders.load_pipes(candidate)
            problems.extend(errors)
            if pipes is not None and not errors:
                mode.pipes = pipes
    elif file.pipeline:
        problems.extend(_merge_pipes(mode, file.pipeline, candidate.label, candidate.plugin_id))
    return (None, problems) if problems else (mode, [])


def _standalone(
    candidate: EditorModeCandidate, builders: EditorModeBuilders
) -> Tuple[Optional[ModeTemplate], List[str]]:
    problems = []
    if not (candidate.directory / "form.yml").exists():
        problems.append(f"{candidate.label}/form.yml: file not found")
    if not (candidate.directory / "pipeline.yml").exists():
        problems.append(f"{candidate.label}/pipeline.yml: file not found")
    if problems:
        return None, problems
    return builders.load_mode(candidate)


def _read_file(candidate: EditorModeCandidate) -> Tuple[Optional[EditorModeFile], List[str]]:
    path = candidate.directory / EDITOR_MODE_FILE
    if not NAME_PATTERN.match(candidate.name):
        return None, [f"{candidate.label}: name must match {NAME_PATTERN.pattern}"]
    if (candidate.directory / "variants").exists():
        return None, [f"{candidate.label}: variants/ is not allowed in an editor mode"]
    if not path.is_file():
        return None, [f"{candidate.label}/{EDITOR_MODE_FILE}: file not found"]
    try:
        data = load_yaml_file(path)
    except Exception as exc:
        return None, [f"{candidate.label}/{EDITOR_MODE_FILE}: failed to parse: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{candidate.label}/{EDITOR_MODE_FILE}: must be a mapping"]
    return validate_editor_mode_file(data, f"{candidate.label}/{EDITOR_MODE_FILE}")


def _derived_quantity(mode: ModeTemplate, claimed: set) -> Optional[str]:
    fields = _inventory(mode)
    names = [
        name
        for name in mode_quantity_fields(mode)
        if name in fields and fields[name].type in QUANTITY_FIELD_TYPES and name not in claimed
    ]
    if "quantity" in names:
        return "quantity"
    return names[0] if len(names) == 1 else None


def _spec(candidate: EditorModeCandidate, file: EditorModeFile, mode: ModeTemplate) -> EditorModeSpec:
    inputs = file.inputs.model_dump()
    if inputs.get("quantity") is None:
        claimed = {*file.fixed, *file.fields.hide, *input_names(inputs)}
        inputs["quantity"] = _derived_quantity(mode, claimed)
    return EditorModeSpec(
        name=candidate.name,
        task=file.task,
        region=file.region,
        inputs=inputs,
        size=file.size.model_dump(),
        context=file.context,
        from_mode=file.from_mode,
        label=file.label,
        short_description=file.short_description,
        icon=file.icon,
        order=file.order,
        fixed=copy.deepcopy(file.fixed),
        hide=list(file.fields.hide),
        max_range_s=file.max_range_s,
        instruction=file.instruction,
        strength=file.strength.model_dump() if file.strength is not None else None,
    )


def build_editor_mode(
    candidate: EditorModeCandidate, target: PresetTemplate, builders: EditorModeBuilders
) -> Tuple[Optional[ModeTemplate], List[str]]:
    file, problems = _read_file(candidate)
    if file is None:
        return None, problems
    problems = _overlap_problems(file)
    if problems:
        return None, [f"{candidate.label}: {problem}" for problem in problems]

    if file.from_mode is None:
        mode, problems = _standalone(candidate, builders)
    else:
        mode, problems = _derive(candidate, file, target, builders)
    if mode is None or problems:
        return None, problems

    inventory = _inventory(mode)
    unknown = [name for name in file.fixed if name not in inventory]
    problems = [f"{candidate.label}: fixed names '{name}', which is not a field of the form" for name in unknown]
    if file.strength is not None and file.strength.field not in inventory:
        problems.append(f"{candidate.label}: strength.field names '{file.strength.field}', which is not a field of the form")
    if problems:
        return None, problems

    problems = builders.mode_checks(target, candidate.name, mode)
    if problems:
        return None, problems

    mode.editor = _spec(candidate, file, mode)
    mode.source_plugin = candidate.plugin_id
    mode.mode_label = file.label or mode.mode_label or _titled(candidate.name)
    mode.mode_icon = file.icon or mode.mode_icon or mode_display_icon(file.from_mode or candidate.name)
    return mode, []


def _titled(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").title()


def _directories(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _core_candidates(preset: PresetTemplate) -> List[EditorModeCandidate]:
    root = Path(preset.path)
    return [
        EditorModeCandidate(path.name, path, root, None, str(path))
        for path in _directories(root / EDITOR_MODES_DIR)
    ]


def _contributed_candidates(contribution: Any) -> List[EditorModeCandidate]:
    prefix = f"plugin '{contribution.plugin_id}' preset_modes (target '{contribution.target_preset_id}')"
    return [
        EditorModeCandidate(
            path.name, path, contribution.modes_root, contribution.plugin_id,
            f"{prefix} {EDITOR_MODES_DIR}/{path.name}",
        )
        for path in _directories(contribution.modes_root / EDITOR_MODES_DIR)
    ]


def apply_editor_modes(
    presets: List[PresetTemplate],
    errors: Dict[str, List[str]],
    contributions: List[Any],
    builders: EditorModeBuilders,
) -> None:
    first: Dict[str, PresetTemplate] = {}
    for preset in presets:
        first.setdefault(preset.id, preset)
    contributed: Dict[int, List[EditorModeCandidate]] = {}
    for contribution in contributions:
        target = first.get(contribution.target_preset_id)
        if target is not None:
            contributed.setdefault(id(target), []).extend(_contributed_candidates(contribution))

    for preset in presets:
        accepted: Dict[str, EditorModeCandidate] = {}
        for candidate in [*_core_candidates(preset), *contributed.get(id(preset), [])]:
            held = accepted.get(candidate.name)
            if held is None:
                accepted[candidate.name] = candidate
                continue
            if held.plugin_id is None:
                reason = (
                    f"collides with the core editor mode '{candidate.name}' of preset '{preset.id}' - "
                    f"the core editor mode wins, this contribution is rejected"
                )
            else:
                reason = (
                    f"editor mode '{candidate.name}' on preset '{preset.id}' was already contributed by plugin "
                    f"'{held.plugin_id}' - first contributor wins, this contribution is rejected"
                )
            errors.setdefault(candidate.error_key, []).append(reason)

        built: Dict[str, ModeTemplate] = {}
        for name in sorted(accepted):
            candidate = accepted[name]
            mode, problems = build_editor_mode(candidate, preset, builders)
            if mode is None or problems:
                logger.warning(f"Skipped editor mode {candidate.error_key}: {'; '.join(problems)}")
                errors.setdefault(candidate.error_key, []).extend(problems)
                continue
            built[name] = mode
        preset.editor_modes = built


def clamp_strength(spec: EditorModeSpec, requested: Optional[float]) -> Optional[Tuple[str, float]]:
    strength = spec.strength
    if strength is None:
        return None
    value = strength["default"] if requested is None else min(strength["max"], max(strength["min"], float(requested)))
    return strength["field"], value
