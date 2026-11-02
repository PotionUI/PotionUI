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
from src.features.models.exceptions import (
    InvalidModelMetadataException,
    ModelAlreadyAssignedException,
    ModelAssignmentException,
    ModelNotFoundException,
    ProviderFetchException,
)
from src.features.models.locator import ModelFileUnavailable
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
    "index_model_file",
    "model_type_dirs",
    "model_write_dir",
    "resolve_model_file",
    "model_for_path",
    "model_for_sha256",
    "visible_model_ids",
    "assign_model_to_user",
    "update_model_metadata",
    "run_provider_fetch",
    "InvalidModelMetadataException",
    "ModelAssignmentException",
    "ModelFileUnavailable",
    "ModelNotFoundException",
    "ProviderFetchException",
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


def _write_roots(container: Any) -> List[Path]:
    roots: List[Path] = []
    for model_type in MODEL_TYPES:
        try:
            roots.append(Path(container.model_roots.write_dir(model_type).path).resolve())
        except Exception:
            continue
    return roots


def index_model_file(path: "str | Path", *, user_id: Optional[str] = None) -> Dict[str, Any]:
    container = get_container()
    target = Path(path).resolve()
    if not target.is_file():
        raise ValueError(f"'{Path(path).name}' is not a file")
    if not any(target.is_relative_to(root) for root in _write_roots(container)):
        raise ValueError(f"'{Path(path).name}' is not inside a model folder")
    outcome = container.model_index_manager.indexing.index_path(str(target))
    model_id = outcome.get("model_id") or (outcome.get("duplicate_of") or {}).get("id")
    if model_id and user_id:
        try:
            container.model_index_manager.assignments.assign_model_to_user(model_id, user_id)
        except ModelAlreadyAssignedException:
            pass
    return outcome


def resolve_model_file(model_id: str) -> Path:
    return get_container().model_locator.path_for_model(model_id)


def model_for_path(path: str, *, admin: bool = False) -> Optional[Dict[str, Any]]:
    """The catalog model at `path`, or None. `admin=True` adds the operational
    fields (location, file size, hash ...) for a caller that has checked the user
    is an admin; never hand them to a regular user."""
    model = get_container().model_locator.model_for_path(path)
    return model.to_dict(include_providers=False, admin=admin) if model is not None else None


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


def model_for_sha256(
    sha256: str, *, size: Optional[int] = None, hash_candidates: str = "background", admin: bool = False
) -> Optional[Dict[str, Any]]:
    if not sha256:
        return None
    container = get_container()
    wanted = sha256.strip().lower()
    if size:
        model = container.model_index_manager.digests.find_by_digest(wanted, size, hash_candidates).model
    else:
        model = container.model_repository.get_by_sha256(wanted, include_providers=False)
    return model.to_dict(include_providers=False, admin=admin) if model is not None else None


def visible_model_ids(user_id: str) -> List[str]:
    """Ids of the models assigned to `user_id`, directly or through their groups."""
    if not user_id:
        return []
    return list(get_container().model_repository.get_available_model_ids_for_user(user_id))


def assign_model_to_user(model_id: str, user_id: str) -> bool:
    """Make a catalog model visible to one user. True when newly assigned, False
    when it already was; `ModelAssignmentException` when a hook blocks it or the
    model or the user is unknown."""
    try:
        get_container().model_index_manager.assignments.assign_model_to_user(model_id, user_id)
    except ModelAlreadyAssignedException:
        return False
    return True


def update_model_metadata(model_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """Set some of a model's shared attribute values, keeping the others; returns
    the values afterwards. `InvalidModelMetadataException` for a key or value the
    type's definitions refuse, `ModelNotFoundException` for an unknown model."""
    container = get_container()
    model = container.model_repository.get_by_id(model_id, include_providers=False, include_tags=False)
    if model is None:
        raise ModelNotFoundException(f"Model '{model_id}' not found")
    current = dict(model.model_metadata or {})
    merged = {**current, **dict(values)}
    if merged == current:
        return current
    container.model_index_manager.metadata.update_model_metadata(model_id, merged)
    return merged


async def run_provider_fetch(provider: str, model_ids: List[str]) -> Dict[str, Any]:
    """Fill the named models' empty description, triggers, tags and previews from
    a marketplace provider; returns `{"successful", "failed", "errors"}`. Nothing
    is fetched for an empty `model_ids`. `ProviderFetchException` for a provider
    that is unknown or not initialised, or when a `model_index.before_fetch_info`
    handler vetoes the fetch."""
    wanted = [model_id for model_id in model_ids if model_id]
    if not wanted:
        return {"successful": 0, "failed": 0, "errors": []}
    fetcher = get_container().model_index_manager.provider_info
    fetcher.fetch_provider_info(provider, model_ids=wanted)
    summary = await fetcher.run_provider_fetch(provider, model_ids=wanted)
    return dict(summary) if isinstance(summary, dict) else {"successful": 0, "failed": len(wanted), "errors": []}
