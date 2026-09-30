from typing import Any, Dict, List, Sequence

from src.features.cloud.contracts import CLOUD_ENGINE
from src.features.cloud.errors import CloudScopeInvalid
from src.features.cloud.scope_repository import ModelPresetScopeRepository
from src.features.models.exceptions import ModelNotFoundException
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE


def driver_of_slug(slug: str) -> str:
    return f"{CLOUD_ENGINE}.{slug.split('~', 1)[0]}"


class CloudModelScopes:
    def __init__(self, repository: ModelPresetScopeRepository, preset_loader, model_repository):
        self.repository = repository
        self.preset_loader = preset_loader
        self.model_repository = model_repository

    def _cloud_model(self, model_id: str) -> Any:
        model = self.model_repository.get_by_id(model_id, include_providers=True, include_tags=False)
        if model is None or model.model_type != CLOUD_MODEL_TYPE:
            raise ModelNotFoundException(f"Model '{model_id}' is not a cloud model")
        return model

    def _presets_by_id(self) -> Dict[str, Any]:
        self.preset_loader._ensure_loaded()
        return {preset.id: preset for preset in self.preset_loader.presets}

    @staticmethod
    def _usable_by(preset: Any, driver: str) -> bool:
        return preset.engine == CLOUD_ENGINE and getattr(preset, "driver", None) == driver

    def describe(self, model_id: str) -> Dict[str, Any]:
        model = self._cloud_model(model_id)
        driver = driver_of_slug(model.filename)
        presets = self._presets_by_id()
        preset_ids = self.repository.get(model_id)
        return {
            "model_id": model_id,
            "slug": model.filename,
            "label": model.display_name,
            "driver": driver,
            "scoped": bool(preset_ids),
            "preset_ids": preset_ids,
            "presets": [self._entry(preset_id, presets.get(preset_id), driver) for preset_id in preset_ids],
            "candidates": [
                {"id": preset.id, "title": preset.name}
                for preset in sorted(presets.values(), key=lambda p: (p.name or "").lower())
                if self._usable_by(preset, driver)
            ],
        }

    def replace(self, model_id: str, preset_ids: Sequence[str]) -> Dict[str, Any]:
        model = self._cloud_model(model_id)
        driver = driver_of_slug(model.filename)
        presets = self._presets_by_id()
        problems: List[str] = []
        for preset_id in dict.fromkeys(preset_ids):
            preset = presets.get(preset_id)
            if preset is None:
                problems.append(f"Preset '{preset_id}' does not exist.")
            elif not self._usable_by(preset, driver):
                problems.append(f"Preset '{preset.name}' cannot use this model: it runs on {preset.engine}"
                                f"{'/' + preset.driver if getattr(preset, 'driver', None) else ''}, not {driver}.")
        if problems:
            raise CloudScopeInvalid(problems)
        self.repository.replace(model_id, preset_ids)
        return self.describe(model_id)

    @staticmethod
    def _entry(preset_id: str, preset: Any, driver: str) -> Dict[str, Any]:
        if preset is None:
            return {"id": preset_id, "title": None, "engine": None, "driver": None, "missing": True, "compatible": False}
        return {
            "id": preset_id,
            "title": preset.name,
            "engine": preset.engine,
            "driver": getattr(preset, "driver", None),
            "missing": False,
            "compatible": CloudModelScopes._usable_by(preset, driver),
        }
