from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.features.recipes.executors._artifact_lookup import find_artifact_model
from src.features.recipes.executors._async_bridge import run_sync
from src.features.recipes.executors._provider_credentials import resolve_provider_registry
from src.features.recipes.executors.artifacts_fetch import resolve_download_url
from src.features.recipes.schema import Recipe, RecipeArtifact
from src.features.recipes.executors._slot_description import describe_slot
from src.features.recipes.variants import choose_variant
from src.platform.runtime.gpu_profile import GpuProfile, detect_gpu_profile


class VariantDownloadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RecipeSlotVariants:
    def __init__(
        self,
        recipe_catalog: Any,
        model_repository: Any,
        download_queue: Any = None,
        gpu_profile_provider: Optional[Callable[[], GpuProfile]] = None,
        provider_registry_factory: Any = None,
    ):
        self.recipe_catalog = recipe_catalog
        self.model_repository = model_repository
        self.download_queue = download_queue
        self._gpu_profile_provider = gpu_profile_provider or detect_gpu_profile
        self._provider_registry_factory = provider_registry_factory

    def _slot_view(self, recipe: Recipe, artifact: RecipeArtifact, gpu: GpuProfile) -> Dict[str, Any]:
        slot, _, _ = describe_slot(self.model_repository, artifact, gpu)
        ideal = choose_variant(artifact, gpu)
        slot["recipe_id"] = recipe.id
        slot["recipe_name"] = recipe.name
        slot["suggested_variant_id"] = ideal.variant_id
        slot["suggested_reason"] = ideal.reason
        return slot

    def for_preset(self, preset_id: str, model_type: Optional[str] = None) -> Dict[str, Any]:
        gpu = self._gpu_profile_provider()
        slots: List[Dict[str, Any]] = []
        for recipe in self.recipe_catalog.recipes_for_preset(preset_id):
            for artifact in recipe.artifacts:
                if not artifact.variants:
                    continue
                if model_type and artifact.model_type != model_type:
                    continue
                slots.append(self._slot_view(recipe, artifact, gpu))
        return {"gpu": gpu.to_dict(), "slots": slots}

    def _matches(self, model: Any, artifact: RecipeArtifact) -> Optional[str]:
        sha = (getattr(model, "sha256", None) or "").lower()
        for variant in artifact.variants:
            if getattr(model, "model_type", None) == artifact.model_type and getattr(model, "filename", None) == variant.filename:
                return variant.id
            checksum = variant.checksum.value if variant.checksum else None
            if sha and checksum and checksum.lower() == sha:
                return variant.id
        return None

    def for_model(self, model: Any) -> Dict[str, Any]:
        gpu = self._gpu_profile_provider()
        for recipe in self.recipe_catalog.list_recipes():
            for artifact in recipe.artifacts:
                if not artifact.variants:
                    continue
                variant_id = self._matches(model, artifact)
                if variant_id is None:
                    continue
                slot = self._slot_view(recipe, artifact, gpu)
                slot["current_variant_id"] = variant_id
                return {"gpu": gpu.to_dict(), "slot": slot}
        return {"gpu": gpu.to_dict(), "slot": None}

    def for_model_id(self, model_id: str) -> Optional[Dict[str, Any]]:
        model = self.model_repository.get_by_id(model_id)
        if model is None:
            return None
        return self.for_model(model)

    def _get_provider_registry(self):
        return resolve_provider_registry(self._provider_registry_factory)

    def queue_download(self, recipe_id: str, artifact_id: str, variant_id: str, created_by: Optional[str]) -> Dict[str, Any]:
        recipe = self.recipe_catalog.get_recipe(recipe_id)
        artifact = recipe.get_artifact(artifact_id) if recipe is not None else None
        if artifact is None or artifact.get_variant(variant_id) is None:
            raise VariantDownloadError("variant_not_found", "That recipe doesn't offer this variant.")
        if self.download_queue is None:
            raise VariantDownloadError("no_download_queue", "The download queue is not available on this instance.")
        resolved = artifact.resolve(variant_id)
        if find_artifact_model(self.model_repository, resolved) is not None:
            raise VariantDownloadError("already_installed", "This variant is already installed.")
        url, note = resolve_download_url(resolved, self._get_provider_registry)
        if not url:
            raise VariantDownloadError(
                "download_url_unresolved",
                f"Couldn't work out where to download '{resolved.display_name or resolved.filename}' from ({note}).",
            )
        try:
            download = run_sync(
                self.download_queue.queue_model_download(
                    url=url,
                    model_type=resolved.model_type,
                    filename=resolved.filename,
                    checksum_sha256=resolved.checksum.value if resolved.checksum else None,
                    provider_id=(resolved.provider_hint or {}).get("source"),
                    created_by=created_by,
                )
            )
        except Exception as exc:
            raise VariantDownloadError("download_failed", f"The download failed to start: {exc}") from exc
        return {"download_id": download.id, "filename": resolved.filename}
