"""Frozen collaborators bundle for the models operations layer.

Model index operations fan out to eight focused role classes (access policy,
catalog, indexing coordinator, metadata editor, provider-info fetcher,
assignment service, jobs, location manager), plus the raw repositories many
outside callers reach through directly (`model_repo`/`tag_repo` - the LLM
tool context, resource providers, and prompt enhancement read these straight
off the bundle they're handed, exactly as they did off the old
`ModelIndexManager` facade). Bundling them once here - built in the
composition root and passed to `operations` functions, `ModelController`,
and every wide external consumer as a single object - avoids threading eight
role objects through every call site. A plain, frozen data holder (no
behavior beyond field access), matching `PromptDatabaseCollaborators` (see
`src.features.prompt_database.collaborators` - the reference shape for a
wide-collaborator dissolution).
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.assignments import ModelAssignmentService
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.catalog import ModelCatalog
from src.features.models.indexer import model_scanner
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from src.features.models.jobs import ModelJobs
from src.features.models.location import ModelsRelocator
from src.features.models.metadata_editor import ModelMetadataEditor
from src.features.models.provider_info import ProviderInfoFetcher
from src.features.models.repository import ModelRepository
from src.features.tags.repository import TagRepository
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.plugins import PluginRegistry
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

if TYPE_CHECKING:
    from src.features.downloads import DownloadQueue


@dataclass(frozen=True)
class ModelIndexCollaborators:
    # Kept as top-level fields because outside collaborators (LLM tools,
    # resource providers, prompt enhancement) read `model_repo`/`tag_repo`
    # straight off the bundle they are handed.
    model_repo: ModelRepository
    tag_repo: TagRepository
    plugins: PluginRegistry
    access: ModelAccessPolicy
    catalog: ModelCatalog
    indexing: ModelIndexingCoordinator
    metadata: ModelMetadataEditor
    provider_info: ProviderInfoFetcher
    assignments: ModelAssignmentService
    jobs: ModelJobs
    location: ModelsRelocator


def build_model_index_collaborators(
    model_repository: ModelRepository,
    tag_repository: TagRepository,
    plugin_registry: PluginRegistry,
    settings: "Settings",
    download_queue: "DownloadQueue",
    models_root: Optional[Path] = None,
    generation_active: Optional[Callable[[], bool]] = None,
    storage_driver: Optional[FileStorageDriver] = None,
    attribute_definition_repository: Optional[AttributeDefinitionRepository] = None,
    user_attribute_repository: Optional[UserModelAttributeRepository] = None,
) -> ModelIndexCollaborators:
    """Build the eight role objects and bundle them - the constructor logic
    the old `ModelIndexManager.__init__` owned."""
    access = ModelAccessPolicy(model_repository)
    return ModelIndexCollaborators(
        model_repo=model_repository,
        tag_repo=tag_repository,
        plugins=plugin_registry,
        access=access,
        catalog=ModelCatalog(model_repository, access, model_scanner, user_attribute_repository),
        indexing=ModelIndexingCoordinator(model_repository, plugin_registry, model_scanner),
        metadata=ModelMetadataEditor(
            model_repository, tag_repository, plugin_registry, settings,
            storage_driver=storage_driver,
            attribute_definition_repository=attribute_definition_repository,
        ),
        provider_info=ProviderInfoFetcher(model_repository, plugin_registry),
        assignments=ModelAssignmentService(model_repository, plugin_registry, access),
        jobs=ModelJobs(model_repository, plugin_registry, model_scanner, download_queue),
        location=ModelsRelocator(
            models_root or Path(model_scanner.models_dir),
            SettingRepository(),
            generation_active,
        ),
    )
