import json
from decimal import Decimal
from typing import Any, Mapping

from src.features.cloud.contracts import (
    CloudModelSpec,
    MediaInputSpec,
    ParamSpec,
    PriceLine,
)

SPEC_VERSION = 1


def spec_to_dict(spec: CloudModelSpec) -> dict[str, Any]:
    return {
        "provider_model_id": spec.provider_model_id,
        "label": spec.label,
        "vendor": spec.vendor,
        "description": spec.description,
        "tasks": sorted(spec.tasks),
        "outputs": sorted(spec.outputs),
        "params": [_param_to_dict(param) for param in spec.params],
        "inputs": [_input_to_dict(media) for media in spec.inputs],
        "max_outputs_per_job": spec.max_outputs_per_job,
        "pricing": [_price_to_dict(line) for line in spec.pricing],
        "typical_seconds": spec.typical_seconds,
        "max_seconds": spec.max_seconds,
        "deprecated_at": spec.deprecated_at,
        "raw": dict(spec.raw),
    }


def spec_to_json(spec: CloudModelSpec) -> str:
    return json.dumps(spec_to_dict(spec), default=str, sort_keys=True)


def spec_from_dict(data: Mapping[str, Any]) -> CloudModelSpec:
    return CloudModelSpec(
        provider_model_id=data["provider_model_id"],
        label=data["label"],
        vendor=data.get("vendor"),
        description=data.get("description"),
        tasks=frozenset(data.get("tasks") or ()),
        outputs=frozenset(data.get("outputs") or ()),
        params=tuple(_param_from_dict(item) for item in data.get("params") or ()),
        inputs=tuple(_input_from_dict(item) for item in data.get("inputs") or ()),
        max_outputs_per_job=data.get("max_outputs_per_job", 1),
        pricing=tuple(_price_from_dict(item) for item in data.get("pricing") or ()),
        typical_seconds=data.get("typical_seconds"),
        max_seconds=data.get("max_seconds"),
        deprecated_at=data.get("deprecated_at"),
        raw=dict(data.get("raw") or {}),
    )


def spec_from_json(text: str) -> CloudModelSpec:
    return spec_from_dict(json.loads(text))


def _param_to_dict(param: ParamSpec) -> dict[str, Any]:
    return {
        "name": param.name,
        "kind": param.kind,
        "values": list(param.values),
        "minimum": param.minimum,
        "maximum": param.maximum,
        "step": param.step,
        "integer": param.integer,
        "default": param.default,
        "required": param.required,
        "label": param.label,
        "description": param.description,
        "tasks": sorted(param.tasks),
    }


def _param_from_dict(data: Mapping[str, Any]) -> ParamSpec:
    return ParamSpec(
        name=data["name"],
        kind=data["kind"],
        values=tuple(data.get("values") or ()),
        minimum=data.get("minimum"),
        maximum=data.get("maximum"),
        step=data.get("step"),
        integer=data.get("integer", True),
        default=data.get("default"),
        required=data.get("required", False),
        label=data.get("label"),
        description=data.get("description"),
        tasks=frozenset(data.get("tasks") or ()),
    )


def _input_to_dict(media: MediaInputSpec) -> dict[str, Any]:
    return {
        "role": media.role,
        "modality": media.modality,
        "min_items": media.min_items,
        "max_items": media.max_items,
        "max_bytes": media.max_bytes,
        "formats": list(media.formats),
        "tasks": sorted(media.tasks),
    }


def _input_from_dict(data: Mapping[str, Any]) -> MediaInputSpec:
    return MediaInputSpec(
        role=data["role"],
        modality=data["modality"],
        min_items=data.get("min_items", 0),
        max_items=data.get("max_items", 1),
        max_bytes=data.get("max_bytes"),
        formats=tuple(data.get("formats") or ()),
        tasks=frozenset(data.get("tasks") or ()),
    )


def _price_to_dict(line: PriceLine) -> dict[str, Any]:
    return {"unit": line.unit, "usd": str(line.usd), "applies_to": line.applies_to}


def _price_from_dict(data: Mapping[str, Any]) -> PriceLine:
    return PriceLine(unit=data["unit"], usd=Decimal(str(data["usd"])), applies_to=data.get("applies_to"))
