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

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.models.attributes.well_known import WellKnownModelAttribute as WellKnownModelMetadataField
from src.platform.filesystem.model_types import MODEL_DIRECTORY_ALIASES, MODEL_TYPES, type_for_folder_name
from src.platform.plugins.runtime_registries import get_container
from src.platform.runtime.model_headers import (
    ClassifierMatch,
    FamilyMatch,
    HeaderView,
    ModelClassifierDefinition,
    TensorInfo,
    model_classifier_registry as _model_classifier_registry,
)

__all__ = [
    "WellKnownModelMetadataField",
    "get_model_provider_info",
    "model_type_dirs",
    "model_write_dir",
    "resolve_model_file",
    "model_for_path",
    "MODEL_DIRECTORY_ALIASES",
    "type_for_folder_name",
    "MODEL_TYPES",
    "HeaderView",
    "TensorInfo",
    "FamilyMatch",
    "ModelClassifierCatalog",
    "model_classifier_registry",
]


class ModelClassifierCatalog:
    def __init__(self, registry) -> None:
        self._registry = registry

    def get(self, key: str) -> Optional[ModelClassifierDefinition]:
        return self._registry.get(key)

    def definitions(self) -> tuple:
        return self._registry.definitions()

    def fingerprint(self) -> str:
        return self._registry.fingerprint()

    def classify(self, view: HeaderView) -> Optional[ClassifierMatch]:
        return self._registry.classify(view)


model_classifier_registry = ModelClassifierCatalog(_model_classifier_registry)


def model_type_dirs(model_type: str) -> List[Path]:
    return [type_dir.path for type_dir in get_container().model_roots.type_dirs(model_type)]


def model_write_dir(model_type: str) -> Path:
    return get_container().model_roots.write_dir(model_type).path


def resolve_model_file(model_id: str) -> Path:
    return get_container().model_locator.path_for_model(model_id)


def model_for_path(path: str) -> Optional[Dict[str, Any]]:
    model = get_container().model_locator.model_for_path(path)
    return model.to_dict(include_providers=False) if model is not None else None


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
