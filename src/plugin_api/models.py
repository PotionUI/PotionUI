"""Model attribute key identifiers, and reading a model's marketplace link.

`WellKnownModelMetadataField` names the attribute keys core seeds as system
definitions (e.g. a LoRA's `strength`, `triggers`) so a plugin references the
constant instead of retyping the string literal - useful when a plugin reads a
model's `model_metadata` values rather than declaring its own attribute via the
manifest `model_metadata_fields:` section.

`get_model_provider_info` reads a model's `providers` table row - the link a
provider plugin recorded (e.g. from a hash lookup) between a catalog model and
its id on that marketplace.
"""

from typing import Any, Dict, Optional

from src.features.models.attributes.well_known import WellKnownModelAttribute as WellKnownModelMetadataField
from src.platform.plugins.runtime_registries import get_container

__all__ = [
    "WellKnownModelMetadataField",
    "get_model_provider_info",
]


def get_model_provider_info(model_id: str, provider: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The catalog model `model_id`'s link to a marketplace provider -
    `{"provider", "provider_model_id", "provider_version_id", "model_name"}`,
    or `None` when the model has no such row (never indexed, or indexed
    without a provider match). `provider` narrows to one provider id (e.g.
    `"civitai"`); omitted, the first linked provider wins."""
    infos = get_container().model_repository.get_providers(model_id, provider=provider)
    if not infos:
        return None
    info = infos[0]
    return {
        "provider": info.provider,
        "provider_model_id": info.provider_model_id,
        "provider_version_id": info.provider_version_id,
        "model_name": info.name,
    }
