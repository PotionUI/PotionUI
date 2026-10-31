from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from src.features.models.recommendations import Recommendation, parse_recommendations
from src.features.presets.templates import FieldTemplate, ModeTemplate

MODEL_FIELD_TYPES = frozenset({"model", "models"})

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def install_recipe_id(preset_name: str) -> str:
    slug = _SLUG_RE.sub("-", (preset_name or "").lower()).strip("-")
    return f"modes-{slug or 'preset'}"


def _walk(fields: Optional[List[FieldTemplate]], out: Dict[str, FieldTemplate]) -> None:
    for spec in fields or []:
        if spec.name and spec.name not in out:
            out[spec.name] = spec
        if isinstance(spec.children, list):
            _walk(spec.children, out)


def mode_inventory(mode: Optional[ModeTemplate]) -> Dict[str, FieldTemplate]:
    inventory: Dict[str, FieldTemplate] = {}
    for form in (mode.forms if mode is not None else None) or []:
        _walk(form.fields, inventory)
    return inventory


def model_fields(mode: Optional[ModeTemplate]) -> Dict[str, FieldTemplate]:
    return {name: spec for name, spec in mode_inventory(mode).items() if spec.type in MODEL_FIELD_TYPES}


def field_model_type(spec: FieldTemplate) -> str:
    configuration = spec.configuration or {}
    model_type = configuration.get("model_type")
    if isinstance(model_type, str) and model_type:
        return model_type
    model_types = configuration.get("model_types")
    if isinstance(model_types, list) and model_types and isinstance(model_types[0], str):
        return model_types[0]
    return "checkpoint"


def field_recommendations(spec: FieldTemplate) -> List[Recommendation]:
    return parse_recommendations((spec.configuration or {}).get("recommendations"))


def field_candidates(spec: FieldTemplate) -> List[Dict[str, Any]]:
    model_type = field_model_type(spec)
    return [
        {
            "model_type": model_type,
            "filename": rec.filename,
            "sha256": rec.sha256,
            "size_bytes": rec.size_bytes,
        }
        for rec in field_recommendations(spec)
    ]


def requirement_field_names(spec: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    field_name = spec.get("field")
    if isinstance(field_name, str) and field_name:
        names.append(field_name)
    any_of = spec.get("any_of")
    if isinstance(any_of, list):
        names.extend(name for name in any_of if isinstance(name, str) and name)
    return names


def resolve_requirement_fields(spec: Dict[str, Any], mode: ModeTemplate, mode_name: Optional[str] = None) -> Dict[str, Any]:
    names = requirement_field_names(spec)
    if spec.get("type") != "model" or not names:
        return spec
    fields = model_fields(mode)
    return {
        **spec,
        "candidates": [
            {
                "field": name,
                "mode": mode_name,
                "model_type": field_model_type(fields[name]) if name in fields else None,
                "files": field_candidates(fields[name]) if name in fields else [],
            }
            for name in names
        ],
    }


def mode_requirement_fields(mode: ModeTemplate) -> Dict[str, Iterable[str]]:
    required: List[str] = []
    groups: List[List[str]] = []
    for form in mode.forms or []:
        for spec in form.requirements or []:
            if spec.get("type") != "model" or spec.get("optional"):
                continue
            field_name = spec.get("field")
            if isinstance(field_name, str) and field_name and field_name not in required:
                required.append(field_name)
            any_of = spec.get("any_of")
            if isinstance(any_of, list):
                group = [name for name in any_of if isinstance(name, str) and name]
                if group and group not in groups:
                    groups.append(group)
    return {"required": required, "any_of": groups}
