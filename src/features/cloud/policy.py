from typing import Any, List

from src.features.cloud.capability_rules import find_input, media_items, media_problems, mode_task
from src.features.cloud.repository import CloudCatalogRepository
from src.features.models.form_refs import collect_model_ids
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE


class CloudPolicyViolation(ValueError):
    pass


def _words(text: str) -> str:
    return text.replace("_", " ")


class CloudGenerationPolicy:
    def __init__(self, repository: CloudCatalogRepository, model_repository):
        self.repository = repository
        self.model_repository = model_repository

    def check(self, preset_template: Any, mode: str, bound: Any, backend: Any) -> None:
        task = mode_task(preset_template, mode)
        problems: List[str] = []
        for model in self._cloud_models(bound):
            problems.extend(self._model_problems(model, task, bound, backend))
        if problems:
            raise CloudPolicyViolation(" ".join(problems))

    def _cloud_models(self, bound: Any) -> List[Any]:
        models: List[Any] = []
        seen: set = set()
        for value in bound.values.values():
            for model_id in collect_model_ids(value):
                if model_id in seen:
                    continue
                seen.add(model_id)
                model = self.model_repository.get_by_id(model_id, include_providers=True, include_tags=False)
                if model is not None and model.model_type == CLOUD_MODEL_TYPE:
                    models.append(model)
        return models

    def _model_problems(self, model: Any, task: Any, bound: Any, backend: Any) -> List[str]:
        model_id = model.id
        name = model.display_name
        entries = self.repository.get_many(backend.backend_id, [model.filename])
        entry = entries[0] if entries else None
        if entry is None or not entry.enabled or not entry.available:
            return [f"'{name}' is not enabled on {backend.name}. Ask an administrator to enable it."]
        spec = entry.spec
        if task is not None and task not in spec.tasks:
            return [f"'{name}' cannot do {_words(task)}. Choose a model that supports it."]
        problems: List[str] = []
        for capability in bound.capabilities:
            role = capability["input"]
            if role is None or not self._holds(bound, capability["model_field"], model_id):
                continue
            media = find_input(spec, role, task)
            if media is None:
                continue
            for problem in media_problems(media, media_items(bound.values.get(capability["field"]))):
                problems.append(f"'{name}' {problem}.")
        return problems

    @staticmethod
    def _holds(bound: Any, model_field: str, model_id: str) -> bool:
        return model_id in collect_model_ids(bound.values.get(model_field))
