"""Model index coordinator - the single entry point onto the model-index feature.

`ModelIndexManager` is a thin facade: it owns no logic of its own, but composes
the role classes that do (access policy, catalog, indexing, metadata editing,
provider fetch, assignments, jobs) and delegates each public operation to the
right one. It stays a single injectable object because many collaborators depend
on it as one handle - the `ModelController`, the LLM tool context, the automation
services and the resource providers all receive this manager and reach through it
(including its `model_repo`/`tag_repo` attributes) rather than wiring seven
separate objects.

`ListModelsParams` and `TYPE_DIR_MAP` are re-exported here so their existing
import sites (`src.features.models.manager`) keep resolving after the split.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from src.platform.plugins import PluginRegistry
from src.features.models.repository import ModelRepository
from src.features.tags.repository import TagRepository
from src.features.models.indexer import model_scanner

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.exceptions import ModelAccessDeniedException, ModelNotFoundException
from src.features.models.catalog import ModelCatalog, ListModelsParams
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from src.features.models.location import ModelsLocationManager
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.metadata_editor import ModelMetadataEditor
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.settings.settings import SettingsManager
from src.platform.settings.repository import SettingRepository
from src.features.models.provider_info import ProviderInfoFetcher
from src.features.models.assignments import ModelAssignmentService
from src.features.models.jobs import ModelJobs, TYPE_DIR_MAP
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.features.downloads import DownloadManager

logger = logging.getLogger(__name__)

__all__ = ["ModelIndexManager", "ListModelsParams", "TYPE_DIR_MAP"]


class ModelIndexManager:
    """Coordinates model index operations by delegating to focused role classes."""

    def __init__(
        self,
        model_repository: ModelRepository,
        tag_repository: TagRepository,
        plugin_registry: PluginRegistry,
        settings_manager: "SettingsManager",
        download_manager: "DownloadManager",
        models_root: Optional[Path] = None,
        generation_active: Optional[Callable[[], bool]] = None,
        storage_driver: Optional[FileStorageDriver] = None,
        attribute_definition_repository: Optional[AttributeDefinitionRepository] = None,
        user_attribute_repository: Optional[UserModelAttributeRepository] = None,
    ):
        # Kept as public attributes because collaborators (LLM tools, resource
        # providers, prompt enhancement) read `model_repo`/`tag_repo` straight
        # off the manager they are handed.
        self.model_repo = model_repository
        self.tag_repo = tag_repository
        self.plugins = plugin_registry

        self._access = ModelAccessPolicy(model_repository)
        self._catalog = ModelCatalog(model_repository, self._access, model_scanner, user_attribute_repository)
        self._indexing = ModelIndexingCoordinator(model_repository, plugin_registry, model_scanner)
        self._metadata = ModelMetadataEditor(
            model_repository, tag_repository, plugin_registry, settings_manager,
            storage_driver=storage_driver,
            attribute_definition_repository=attribute_definition_repository,
        )
        self._provider_info = ProviderInfoFetcher(model_repository, plugin_registry)
        self._assignments = ModelAssignmentService(model_repository, plugin_registry, self._access)
        self._jobs = ModelJobs(model_repository, plugin_registry, model_scanner, download_manager)
        self._location = ModelsLocationManager(
            models_root or Path(model_scanner.models_dir),
            SettingRepository(),
            generation_active,
        )

    # ========== Catalog / queries ==========

    def list_models(self, params: ListModelsParams, user: User) -> Dict[str, Any]:
        return self._catalog.list_models(params, user)

    def get_model_availability(self, model_id: str) -> Dict[str, Any]:
        return self._catalog.get_model_availability(model_id)

    def get_model_stats(self) -> Dict[str, Any]:
        return self._catalog.get_model_stats()

    def get_model_types(self, user: User, user_scoped: bool = False, include_empty: bool = False) -> Dict[str, Any]:
        return self._catalog.get_model_types(user, user_scoped, include_empty)

    def get_model_by_hash(self, sha256: str) -> Dict[str, Any]:
        return self._catalog.get_model_by_hash(sha256)

    def get_model_by_id(self, model_id: str, user: Optional[User] = None, admin: bool = False) -> Dict[str, Any]:
        return self._catalog.get_model_by_id(model_id, user, admin)

    def get_model_generations(
        self,
        model_id: str,
        user: User,
        limit: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        return self._catalog.get_model_generations(model_id, user, limit, offset)

    # ========== Indexing ==========

    def start_indexing(self) -> Dict[str, Any]:
        return self._indexing.start_indexing()

    def run_indexing(self) -> None:
        return self._indexing.run_indexing()

    def cleanup_deleted_models(self) -> Dict[str, Any]:
        return self._indexing.cleanup_deleted_models()

    # ========== Models location ==========

    def get_models_location(self) -> Dict[str, Any]:
        return self._location.get_config()

    def apply_models_location(self, external_path: str, overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return self._location.apply(external_path, overrides)

    # ========== Metadata editing ==========

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        return self._metadata.delete_model(model_id)

    def update_model_tags(self, model_id: str, tag_ids: List[str]) -> Dict[str, Any]:
        return self._metadata.update_model_tags(model_id, tag_ids)

    def update_model_description(self, model_id: str, description: str) -> Dict[str, Any]:
        return self._metadata.update_model_description(model_id, description)

    def update_model_prompting_guidance(self, model_id: str, prompting_guidance: str) -> Dict[str, Any]:
        return self._metadata.update_model_prompting_guidance(model_id, prompting_guidance)

    def update_model_metadata(self, model_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        return self._metadata.update_model_metadata(model_id, values)

    def update_model_preview(
        self,
        model_id: str,
        preview_input: Optional[Dict[str, Any]],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._metadata.update_model_preview(model_id, preview_input, user_id)

    def list_model_previews(self, model_id: str) -> List[Dict[str, Any]]:
        return self._metadata.list_model_previews(model_id)

    def list_model_previews_for_user(self, model_id: str, user: User) -> List[Dict[str, Any]]:
        """List a model's previews for any caller who can reach the model.

        Mirrors `get_model_generations`'s access check instead of the
        admin gate. A denied and a missing model both surface as
        ModelNotFoundException (house 404-not-403 idiom) - the caller can't
        use this endpoint to probe which model ids exist.
        """
        try:
            self._access.verify_model_access(model_id, user)
        except ModelAccessDeniedException:
            raise ModelNotFoundException(f"Model '{model_id}' not found")
        return self._metadata.list_model_previews(model_id)

    def add_model_preview(
        self, model_id: str, preview_input: Dict[str, Any], user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._metadata.add_model_preview(model_id, preview_input, user_id)

    def delete_model_preview(self, model_id: str, preview_id: str) -> Dict[str, Any]:
        return self._metadata.delete_model_preview(model_id, preview_id)

    def reorder_model_previews(self, model_id: str, ordered_ids: List[str]) -> Dict[str, Any]:
        return self._metadata.reorder_model_previews(model_id, ordered_ids)

    # ========== Provider info ==========

    def fetch_provider_info(
        self,
        provider: str,
        model_ids: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        return self._provider_info.fetch_provider_info(provider, model_ids, force_refresh)

    async def run_provider_fetch(
        self,
        provider: str,
        model_ids: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> None:
        return await self._provider_info.run_provider_fetch(provider, model_ids, force_refresh)

    # ========== Assignments ==========

    def get_user_model_assignments(self, user_id: str) -> Dict[str, Any]:
        return self._assignments.get_user_model_assignments(user_id)

    def assign_model_to_user(self, model_id: str, user_id: str) -> Dict[str, Any]:
        return self._assignments.assign_model_to_user(model_id, user_id)

    def unassign_model_from_user(self, model_id: str, user_id: str) -> Dict[str, Any]:
        return self._assignments.unassign_model_from_user(model_id, user_id)

    def get_model_assignments(self, model_id: str) -> Dict[str, Any]:
        return self._assignments.get_model_assignments(model_id)

    def get_model_assignment_summary(self) -> Dict[str, Dict[str, int]]:
        return self._assignments.get_assignment_summary()

    # ========== Jobs ==========

    def start_thumbnail_generation(self, model_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        return self._jobs.start_thumbnail_generation(model_ids)

    async def run_thumbnail_generation(self, model_ids: Optional[List[str]] = None) -> None:
        return await self._jobs.run_thumbnail_generation(model_ids)

    def start_download_and_index(
        self,
        name: str,
        link: str,
        size: str,
        sha256: str,
        model_type: str = 'checkpoint'
    ) -> Dict[str, Any]:
        return self._jobs.start_download_and_index(name, link, size, sha256, model_type)

    async def run_download_and_index(
        self,
        name: str,
        link: str,
        sha256: str,
        model_type: str = 'checkpoint'
    ) -> None:
        return await self._jobs.run_download_and_index(name, link, sha256, model_type)
