import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from src.features.models.form_refs import is_model_ref, make_model_ref, model_id_of
from src.features.presets import operations

logger = logging.getLogger(__name__)

LORA_FIELD_TYPE = "lora_picker"

LORA_VALUE_FORM = (
    "rows [{model, strength?}]; model = LoRA id, `model:<id>`, file name or library-relative path "
    "(search_models type=lora)"
)

_ID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE)
_REFERENCE_KEYS = ("model", "modelPath", "model_id", "id", "path", "filename")
_STRENGTH_KEYS = ("strength", "weight")
_ALIAS_KEYS = set(_REFERENCE_KEYS) | set(_STRENGTH_KEYS)

ACCEPTED_FORMS = (
    "Accepted references: the model id, `model:<id>`, the file name (name.safetensors) or the "
    "library-relative path (folder/name.safetensors), sent as {\"model\": <reference>, \"strength\": 1.0}."
)


@dataclass
class LoraResolution:
    overrides: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _lora_fields(node: Any, found: Dict[str, Dict[str, Any]]) -> None:
    if isinstance(node, dict):
        name = node.get("name")
        if node.get("type") == LORA_FIELD_TYPE and isinstance(name, str):
            found[name] = node.get("configuration") or {}
        for child in node.get("children") or []:
            _lora_fields(child, found)
    elif isinstance(node, list):
        for child in node:
            _lora_fields(child, found)


def lora_field_configurations(schema_properties: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    if isinstance(schema_properties, dict):
        for spec in schema_properties.values():
            _lora_fields(spec, found)
    return found


def _reference_of(row: Any) -> Any:
    if isinstance(row, str):
        return row
    if not isinstance(row, dict):
        return None
    for key in _REFERENCE_KEYS:
        candidate = row.get(key)
        if isinstance(candidate, dict):
            candidate = _reference_of(candidate)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _model_types(config: Dict[str, Any]) -> List[str]:
    types = config.get("model_types")
    if isinstance(types, list) and types:
        return [str(item) for item in types]
    return [str(config.get("model_type") or "lora")]


def _describe(model: Any) -> str:
    return f"{model.id} ({model.filename})"


def _usable(model: Any, types: List[str]) -> bool:
    return bool(getattr(model, "is_available", True)) and model.model_type in types


def _candidates(reference: str, types: List[str], manager: Any) -> Tuple[List[Any], Optional[Any]]:
    repo = manager.model_repo
    if is_model_ref(reference):
        reference = model_id_of(reference)
    if _ID_PATTERN.match(reference):
        model = repo.get_by_id(reference, include_providers=False, include_tags=True)
        return ([model] if model else []), model
    normalized = reference.replace("\\", "/")
    if "/" in normalized:
        try:
            located = manager.locator.model_for_path(reference)
        except Exception:
            logger.debug("path lookup failed for %r", reference, exc_info=True)
            located = None
        if located is not None and _usable(located, types):
            return [located], None
    filename = normalized.rsplit("/", 1)[-1]
    if not filename:
        return [], None
    return [model for model in repo.get_by_filename(filename) if _usable(model, types)], None


def _tag_ids(model: Any) -> Set[str]:
    return {getattr(tag, "id", None) for tag in (getattr(model, "tags", None) or [])} - {None}


def _tag_names(model: Any) -> List[str]:
    return [str(getattr(tag, "name", "")) for tag in (getattr(model, "tags", None) or []) if getattr(tag, "name", "")]


def _resolve(
    field_name: str, reference: str, config: Dict[str, Any], manager: Any, warnings: List[str]
) -> Tuple[Optional[Any], Optional[str]]:
    types = _model_types(config)
    candidates, by_id = _candidates(reference, types, manager)
    if by_id is not None and not _usable(by_id, types):
        if not getattr(by_id, "is_available", True):
            return None, f"'{field_name}': {_describe(by_id)} is not available: its file is missing from the library."
        return None, f"'{field_name}': {_describe(by_id)} is a {by_id.model_type} model, not a {'/'.join(types)}."
    if by_id is not None:
        candidates = [by_id]
    if not candidates:
        return None, f"'{field_name}': no installed LoRA matches {reference!r}. {ACCEPTED_FORMS}"
    if len(candidates) > 1:
        listed = ", ".join(_describe(model) for model in candidates)
        return None, (
            f"'{field_name}': {reference!r} matches {len(candidates)} installed LoRAs: {listed}. "
            "Send the `model:<id>` of the one you mean."
        )
    model = candidates[0]
    if by_id is None:
        model = manager.model_repo.get_by_id(model.id, include_providers=False, include_tags=True) or model

    required = config.get("filter_tags")
    if isinstance(required, list) and required:
        own = _tag_ids(model)
        if not own & set(required):
            wanted = ", ".join(str(tag) for tag in required)
            if not own:
                return None, (
                    f"'{field_name}': LoRA {_describe(model)} has no base-model tag, and this preset's LoRA "
                    f"picker only lists LoRAs tagged with one of the tag ids [{wanted}], so the app would not "
                    "offer it either. Tag it in Models (open the model, add the base-model tag) or pick it "
                    "once in the app."
                )
            return None, (
                f"'{field_name}': LoRA {_describe(model)} is tagged {', '.join(_tag_names(model)) or 'for another base model'}, "
                f"but this preset's LoRA picker only lists LoRAs tagged with one of the tag ids [{wanted}]: "
                "it belongs to another base model."
            )
    elif not _tag_ids(model):
        warnings.append(
            f"LoRA {_describe(model)} has no base-model tag, so its compatibility with this preset is "
            "unknown; a LoRA made for another model can fail or do nothing."
        )
    return model, None


def _as_rows(value: Any) -> Optional[List[Any]]:
    if isinstance(value, (str, dict)):
        return [value]
    if isinstance(value, list):
        return value
    return None


def _normalized_row(field_name: str, row: Any, model: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    entry: Dict[str, Any] = {"model": make_model_ref(model.id)}
    if isinstance(row, dict):
        for key in _STRENGTH_KEYS:
            if key in row and row[key] is not None:
                strength = row[key]
                if isinstance(strength, bool) or not isinstance(strength, (int, float)):
                    return None, f"'{field_name}': strength for {_describe(model)} must be a number, got {strength!r}."
                entry["strength"] = strength
                break
        for key, extra in row.items():
            if key not in _ALIAS_KEYS:
                entry[key] = extra
    return entry, None


def resolve_lora_overrides(
    preset_collaborators: Any,
    model_index_manager: Any,
    preset_id: str,
    mode: str,
    proposed: Dict[str, Any],
) -> LoraResolution:
    result = LoraResolution(overrides=dict(proposed or {}))
    if not proposed or preset_collaborators is None or model_index_manager is None:
        return result
    try:
        schema_data = operations.get_form_schema(preset_collaborators, preset_id, mode=mode)
        configs = lora_field_configurations(schema_data.get("form_schema", {}).get("properties", {}))
    except Exception as exc:
        logger.debug("could not load form schema for LoRA resolution: %s", exc)
        return result

    for field_name, config in configs.items():
        if field_name not in proposed:
            continue
        value = proposed[field_name]
        if value is None or value == "" or value == []:
            continue
        rows = _as_rows(value)
        if rows is None:
            result.errors.append(
                f"'{field_name}': expected a list of LoRA rows, got {type(value).__name__}. {ACCEPTED_FORMS}"
            )
            continue
        resolved: List[Dict[str, Any]] = []
        for row in rows:
            reference = _reference_of(row)
            if reference is None:
                result.errors.append(f"'{field_name}': a LoRA row has no model reference. {ACCEPTED_FORMS}")
                continue
            model, error = _resolve(field_name, reference, config, model_index_manager, result.warnings)
            if error:
                result.errors.append(error)
                continue
            entry, error = _normalized_row(field_name, row, model)
            if error:
                result.errors.append(error)
                continue
            resolved.append(entry)
        result.overrides[field_name] = resolved
    return result
