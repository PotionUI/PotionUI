import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.features.elixirs.contracts import (
    Contracts,
    InputTarget,
    ModeContract,
)
from src.features.elixirs.vocabulary import MediaType, PORT_NAME_PATTERN, PortName
from src.features.presets.mode_labels import mode_display_name
from src.features.presets.quantity import mode_quantity_fields
from src.features.presets.templates import FieldTemplate, ModeTemplate, PresetTemplate
from src.platform.filesystem.yaml_io import load_yaml_file

logger = logging.getLogger(__name__)

OVERRIDE_FILE = "elixir.yml"
MEDIA_FIELD_TYPES: Dict[str, MediaType] = {"image": "image", "video": "video"}
UNSUPPORTED_MEDIA_FIELD_TYPES = frozenset({"audio", "media"})
OUTPUT_PORTS: Dict[str, MediaType] = {"images": "image", "videos": "video"}
GALLERY_PIPE = "gallery"
QUANTITY_FIELD = "quantity"
QUANTITY_FIELD_TYPES = frozenset({"stepper", "number", "integer", "slider"})
_PORT_NAME = re.compile(PORT_NAME_PATTERN)


class InputPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, min_length=1)
    media_type: Optional[MediaType] = None
    target: Optional[InputTarget] = None


class OutputPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, min_length=1)
    media_type: Optional[MediaType] = None
    selector: Optional[Dict[Literal["is_final", "is_derived"], bool]] = None


class ModeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    enabled: bool = True
    label: Optional[str] = Field(default=None, min_length=1)
    prompt: Optional[bool] = None
    quantity_field: Optional[str] = None
    inputs: Dict[PortName, Optional[InputPatch]] = Field(default_factory=dict)
    outputs: Dict[PortName, Optional[OutputPatch]] = Field(default_factory=dict)


@dataclass
class ModeResult:
    preset_id: str
    preset_name: str
    mode: str
    contract: Optional[ModeContract] = None
    problems: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    disabled: bool = False
    overridden: bool = False


@dataclass
class _Draft:
    label: str
    prompt: bool
    quantity_field: Optional[str] = None
    inputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    input_problems: List[str] = field(default_factory=list)
    output_problems: List[str] = field(default_factory=list)


def sentence_case(label: str) -> str:
    words = label.strip().split()
    cased = [word if len(word) > 1 and word.isupper() else word.lower() for word in words]
    return " ".join(cased)[:1].upper() + " ".join(cased)[1:]


def _walk(fields: Optional[List[FieldTemplate]], out: Dict[str, List[FieldTemplate]]) -> None:
    for spec in fields or []:
        if spec.name:
            out.setdefault(spec.name, []).append(spec)
        if isinstance(spec.children, list):
            _walk(spec.children, out)


def _mode_fields(mode: ModeTemplate) -> Dict[str, List[FieldTemplate]]:
    found: Dict[str, List[FieldTemplate]] = {}
    for form in mode.forms:
        _walk(form.fields, found)
    return found


def _flag(value: Any, default: bool) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
    return None


def _derive_inputs(template: PresetTemplate, mode_name: str, mode: ModeTemplate, draft: _Draft) -> None:
    director = ((template.vars or {}).get("video_director") or {}).get("preset_modes") or []
    for name, specs in _mode_fields(mode).items():
        kinds = {spec.type for spec in specs}
        if kinds & UNSUPPORTED_MEDIA_FIELD_TYPES:
            draft.input_problems.append(f"field {name} is a {sorted(kinds)[0]} picker, which has no elixir media type")
            continue
        if not kinds & MEDIA_FIELD_TYPES.keys():
            continue
        if len(kinds) > 1:
            draft.input_problems.append(f"field {name} has different types across the forms of the mode")
            continue
        if mode_name in director and all(spec.hidden_when_video_director for spec in specs):
            continue
        if not _PORT_NAME.match(name):
            draft.input_problems.append(f"field {name} cannot be a port name")
            continue
        spec = specs[0]
        draft.inputs[name] = {
            "label": sentence_case(spec.label or name.replace("_", " ")),
            "media_type": MEDIA_FIELD_TYPES[spec.type],
            "target": {"kind": "form_field", "field": name},
        }


def _derive_outputs(mode: ModeTemplate, draft: _Draft) -> None:
    for pipe in mode.pipes:
        if pipe.name != GALLERY_PIPE:
            continue
        configuration = pipe.configuration or {}
        enabled = _flag(pipe.enabled, True)
        derived = _flag(configuration.get("derived"), False)
        if derived is None:
            draft.output_problems.append("a gallery pipe has a templated derived flag")
            continue
        if not enabled or derived:
            continue
        for entry in pipe.input or []:
            kind = entry[0] if isinstance(entry, (list, tuple)) and entry else None
            if kind == "audio":
                draft.output_problems.append("the mode outputs audio, which has no elixir media type")
            elif kind in ("image", "video"):
                port = "images" if kind == "image" else "videos"
                draft.outputs[port] = {
                    "label": f"Final {port}",
                    "media_type": kind,
                    "selector": {"is_final": True, "is_derived": False},
                }
    if not draft.outputs and not draft.output_problems:
        draft.output_problems.append("the mode has no always-enabled final gallery with an image or video (galleries enabled by a form expression are ignored)")


def _derive(template: PresetTemplate, mode_name: str, mode: ModeTemplate) -> _Draft:
    promptless = (template.vars or {}).get("promptless_modes") or []
    draft = _Draft(label=mode_display_name(mode_name, mode.mode_label), prompt=mode_name not in promptless)
    fields = _mode_fields(mode)
    quantity = [
        name for name in mode_quantity_fields(mode) if name in fields and fields[name][0].type in QUANTITY_FIELD_TYPES
    ]
    if QUANTITY_FIELD in quantity or len(quantity) == 1:
        draft.quantity_field = QUANTITY_FIELD if QUANTITY_FIELD in quantity else quantity[0]
    _derive_inputs(template, mode_name, mode, draft)
    _derive_outputs(mode, draft)
    return draft


def override_path(template: PresetTemplate, mode_name: str) -> Path:
    return Path(template.path) / "modes" / mode_name / OVERRIDE_FILE


def read_override(path: Path) -> ModeOverride:
    document = load_yaml_file(path)
    return ModeOverride.model_validate(document if document is not None else {})


def _merge_ports(derived: Dict[str, Dict[str, Any]], patches: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    merged = {name: dict(port) for name, port in derived.items()}
    for name, patch in patches.items():
        if patch is None:
            merged.pop(name, None)
            continue
        changes = patch.model_dump(exclude_unset=True, exclude_none=True)
        merged[name] = {**merged.get(name, {}), **changes}
    return merged


def _failure_text(failure: Exception) -> str:
    if isinstance(failure, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'document'}: {item['msg'].removeprefix('Value error, ')}"
            for item in failure.errors()
        )
    return str(failure)


def mode_result(template: PresetTemplate, mode_name: str, mode: ModeTemplate) -> ModeResult:
    result = ModeResult(preset_id=template.id, preset_name=template.name, mode=mode_name)
    draft = _derive(template, mode_name, mode)
    override: Optional[ModeOverride] = None
    if mode.source_plugin is None:
        path = override_path(template, mode_name)
        if path.is_file():
            result.overridden = True
            try:
                override = read_override(path)
            except Exception as failure:
                result.errors.append(f"modes/{mode_name}/{OVERRIDE_FILE}: {_failure_text(failure)}")
                return result
    if override is not None and not override.enabled:
        result.disabled = True
        return result
    inputs, outputs = draft.inputs, draft.outputs
    problems = [*draft.input_problems, *draft.output_problems]
    label, prompt, quantity_field = draft.label, draft.prompt, draft.quantity_field
    if override is not None:
        label = override.label or label
        prompt = draft.prompt if override.prompt is None else override.prompt
        if "quantity_field" in override.model_fields_set:
            quantity_field = override.quantity_field
        inputs = _merge_ports(inputs, override.inputs)
        outputs = _merge_ports(outputs, override.outputs)
        if "inputs" in override.model_fields_set:
            problems = [problem for problem in problems if problem not in draft.input_problems]
        if "outputs" in override.model_fields_set:
            problems = [problem for problem in problems if problem not in draft.output_problems]
    if problems:
        result.problems = problems
        return result
    try:
        result.contract = ModeContract.model_validate(
            {"label": label, "prompt": prompt, "quantity_field": quantity_field, "inputs": inputs, "outputs": outputs}
        )
    except ValidationError as failure:
        text = _failure_text(failure)
        if override is not None:
            result.errors.append(f"modes/{mode_name}/{OVERRIDE_FILE}: {text}")
        else:
            result.problems.append(text)
    return result


def preset_results(template: PresetTemplate) -> List[ModeResult]:
    return [mode_result(template, name, mode) for name, mode in (template.modes or {}).items()]


def derived_contracts(templates: Iterable[PresetTemplate]) -> Contracts:
    contracts: Contracts = {}
    for template in templates:
        for result in preset_results(template):
            if result.contract is not None:
                contracts[(result.preset_id, result.mode)] = result.contract
            elif result.errors:
                logger.warning("Elixir contract skipped: %s", "; ".join(result.errors))
    return contracts


def _override_stamp(template: PresetTemplate) -> tuple:
    stamps = []
    for mode_name in template.modes or {}:
        try:
            stamps.append((mode_name, override_path(template, mode_name).stat().st_mtime_ns))
        except OSError:
            stamps.append((mode_name, None))
    return tuple(stamps)


class ContractCache:
    def __init__(self, templates: Callable[[], Iterable[PresetTemplate]]):
        self._templates = templates
        self._held: List[PresetTemplate] = []
        self._stamps: List[tuple] = []
        self._contracts: Optional[Contracts] = None

    def __call__(self) -> Contracts:
        templates = list(self._templates())
        stamps = [_override_stamp(template) for template in templates]
        same = (
            self._contracts is not None
            and len(templates) == len(self._held)
            and all(new is old for new, old in zip(templates, self._held))
            and stamps == self._stamps
        )
        if not same:
            self._contracts = derived_contracts(templates)
            self._held, self._stamps = templates, stamps
        return self._contracts
