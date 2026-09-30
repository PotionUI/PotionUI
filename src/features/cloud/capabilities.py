from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.features.cloud.capability_rules import inputs_for_task, params_for_task
from src.features.cloud.contracts import CLOUD_ENGINE, TASK_KINDS, CloudModelSpec, MediaInputSpec, ParamSpec
from src.features.cloud.records import CloudCatalogEntry
from src.features.cloud.repository import CloudCatalogRepository
from src.features.models.exceptions import ModelNotFoundException
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE


@dataclass(frozen=True)
class ResolvedCloudModel:
    model_id: str
    slug: str
    backend_id: str
    driver: str
    entry: CloudCatalogEntry

    @property
    def spec(self) -> CloudModelSpec:
        return self.entry.spec


def param_payload(param: ParamSpec) -> Dict[str, Any]:
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
        "extra": param.name.startswith("x."),
    }


def input_payload(media: MediaInputSpec) -> Dict[str, Any]:
    return {
        "role": media.role,
        "modality": media.modality,
        "min_items": media.min_items,
        "max_items": media.max_items,
        "max_bytes": media.max_bytes,
        "formats": list(media.formats),
        "tasks": sorted(media.tasks),
    }


class CloudCapabilities:
    def __init__(self, backend_registry, repository: CloudCatalogRepository, model_repository, model_access_policy=None):
        self.backend_registry = backend_registry
        self.repository = repository
        self.model_repository = model_repository
        self.model_access_policy = model_access_policy

    def resolve(
        self, model_id: str, driver: Optional[str] = None, backend_id: Optional[str] = None
    ) -> Optional[ResolvedCloudModel]:
        model = self.model_repository.get_by_id(model_id, include_providers=False, include_tags=False)
        if model is None or model.model_type != CLOUD_MODEL_TYPE:
            return None
        backends = [
            backend for backend in self.backend_registry.get_backends_for_engine(CLOUD_ENGINE)
            if (not driver or backend.config.effective_driver == driver)
            and (not backend_id or backend.backend_id == backend_id)
        ]
        by_backend = {backend.backend_id: backend for backend in backends}
        entries = {
            entry.backend_id: entry
            for entry in self.repository.entries_for_slug(model.filename, list(by_backend))
        }
        for backend in backends:
            entry = entries.get(backend.backend_id)
            if entry is not None:
                return ResolvedCloudModel(model_id, model.filename, backend.backend_id, backend.config.effective_driver, entry)
        return None

    def spec_for(
        self, model_id: str, driver: Optional[str] = None, backend_id: Optional[str] = None
    ) -> Optional[CloudModelSpec]:
        resolved = self.resolve(model_id, driver, backend_id)
        return resolved.spec if resolved else None

    def payload(self, model_id: str, user: Any, driver: Optional[str] = None) -> Dict[str, Any]:
        if self.model_access_policy is not None:
            self.model_access_policy.verify_model_access(model_id, user)
        resolved = self.resolve(model_id, driver)
        if resolved is None:
            raise ModelNotFoundException(f"Model '{model_id}' is not an enabled cloud model")
        spec = resolved.spec
        tasks = [task for task in TASK_KINDS if task in spec.tasks]
        return {
            "model_id": model_id,
            "slug": resolved.slug,
            "label": resolved.entry.label,
            "vendor": spec.vendor,
            "description": spec.description,
            "driver": resolved.driver,
            "tasks": tasks,
            "outputs": sorted(spec.outputs),
            "max_outputs_per_job": spec.max_outputs_per_job,
            "deprecated": spec.deprecated_at is not None,
            "params": [param_payload(param) for param in spec.params],
            "inputs": [input_payload(media) for media in spec.inputs],
            "by_task": {
                task: {
                    "params": [param.name for param in params_for_task(spec, task)],
                    "inputs": [media.role for media in inputs_for_task(spec, task)],
                }
                for task in tasks
            },
        }
