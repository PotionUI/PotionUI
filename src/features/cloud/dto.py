from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.features.cloud.records import CloudCatalogEntry, CloudCatalogState


class ModelScopeRequest(BaseModel):
    preset_ids: List[str] = Field(default_factory=list, max_length=500)


class CatalogSelectionRequest(BaseModel):
    slugs: List[str] = Field(min_length=1, max_length=500)


def admin_entry_dict(entry: CloudCatalogEntry) -> dict[str, Any]:
    spec = entry.spec
    return {
        "slug": entry.slug,
        "provider_model_id": entry.provider_model_id,
        "label": entry.label,
        "vendor": entry.vendor,
        "description": spec.description,
        "tasks": entry.tasks,
        "outputs": entry.outputs,
        "enabled": entry.enabled,
        "suggested": entry.suggested,
        "available": entry.available,
        "missing_since": entry.missing_since,
        "deprecated": entry.deprecated_at is not None,
        "deprecated_at": entry.deprecated_at,
        "discovered_at": entry.discovered_at,
        "refreshed_at": entry.refreshed_at,
        "enabled_at": entry.enabled_at,
        "model_id": entry.model_id,
        "max_outputs_per_job": spec.max_outputs_per_job,
        "typical_seconds": spec.typical_seconds,
        "max_seconds": spec.max_seconds,
        "params": [
            {"name": param.name, "kind": param.kind, "label": param.label, "tasks": sorted(param.tasks)}
            for param in spec.params
        ],
        "inputs": [
            {"role": media.role, "modality": media.modality, "max_items": media.max_items, "tasks": sorted(media.tasks)}
            for media in spec.inputs
        ],
        "pricing": [
            {"unit": line.unit, "usd": str(line.usd), "applies_to": line.applies_to} for line in spec.pricing
        ],
    }


def admin_state_dict(state: Optional[CloudCatalogState]) -> Optional[dict[str, Any]]:
    if state is None:
        return None
    return {"refreshed_at": state.refreshed_at, "listed": state.listed, "skipped": state.skipped}
