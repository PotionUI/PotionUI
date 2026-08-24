"""Generation history operations coordinator.

This module provides the GenerationHistoryManager class that orchestrates all
generation history-related business logic. It is a thin facade: reads and
validation are handled by GenerationHistoryQuery, and mutations, uploads and
file IO by GenerationHistoryArchive. It is kept (not dissolved) because the
generation routes hold it as a single handle and reach through it for a dozen
unrelated read and write methods.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

from src.platform.plugins import PluginRegistry
from src.features.generation.repository import GenerationRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.history_archive import GenerationHistoryArchive

if TYPE_CHECKING:
    from src.platform.filesystem import FileStore
    from src.platform.settings.settings import SettingsManager
    from src.features.media_index.manager import MediaIndexManager
    from src.features.media_index.repository import MediaIndexRepository
    from src.features.presets.name_resolver import PresetNameResolver

logger = logging.getLogger(__name__)


class GenerationHistoryManager:
    """
    Orchestrates generation history operations.

    Combines repository access, file management, tag operations,
    and plugin hook execution into cohesive generation workflows.
    """

    def __init__(
        self,
        generation_repo: GenerationRepository,
        file_service: 'FileStore',
        plugin_registry: PluginRegistry,
        media_index_repository: Optional['MediaIndexRepository'] = None,
        settings_manager: Optional['SettingsManager'] = None,
        media_index_manager: Optional['MediaIndexManager'] = None,
        preset_name_resolver: Optional['PresetNameResolver'] = None
    ):
        """Initialize GenerationHistoryManager.

        Args:
            generation_repo: Repository for generation data access
            file_service: Service for file operations
            plugin_registry: Plugin registry for hook execution
            media_index_repository: Attaches system tags/rating scores to
                history payloads (optional; payloads omit them when absent)
            settings_manager: Resolves the NSFW blur threshold for the per-file
                ``nsfw`` flag (optional; a built-in default applies)
            media_index_manager: Backs semantic (visual) history search
                (optional; ``semantic_query`` returns no results when absent)
            preset_name_resolver: Resolves preset ids to their YAML display
                names (optional; ids are shown verbatim when absent)
        """
        self.generation_repo = generation_repo
        self.file_service = file_service
        self.plugins = plugin_registry

        self._query = GenerationHistoryQuery(
            generation_repo, file_service, media_index_repository, settings_manager,
            media_index_manager, preset_name_resolver
        )
        self._archive = GenerationHistoryArchive(
            generation_repo, file_service, plugin_registry, self._query
        )

    @property
    def query(self) -> GenerationHistoryQuery:
        """The read/validation side, for callers - like the routes
        controller - that call `GenerationHistoryQuery` directly rather than
        through one of this facade's forwarders."""
        return self._query

    # --- Reads (GenerationHistoryQuery) ---
    #
    # `get_history`/`get_by_id`/`get_tags` stay here as forwarders: besides
    # the generation routes controller, `OrganizeGalleryTool`
    # (src/features/llm/tools/builtin/organize_gallery_tool.py) reaches them
    # through `ToolContext.generation_history_manager`, which has no route to
    # a standalone `GenerationHistoryQuery`. The routes-only reads
    # (`get_facets`/`get_params`/`count_generations_by_tags`) and the
    # validation helpers below were removed - the routes controller and
    # `GenerationHistoryManager`'s own tests call `GenerationHistoryQuery`
    # directly for those.

    def get_history(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        include_tags: bool = True,
        media_type: Optional[str] = None,
        search: Optional[str] = None,
        mode: Optional[str] = None,
        preset_id: Optional[str] = None,
        model_name: Optional[str] = None,
        min_rating: Optional[int] = None,
        favorites_only: bool = False,
        collection_id: Optional[str] = None,
        used_phrasebook_value_id: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        system_tag: Optional[str] = None,
        semantic_query: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._query.get_history(
            user_id, limit, offset, status, created_from, created_to,
            completed_from, completed_to, tag_ids, include_tags, media_type,
            search, mode, preset_id, model_name, min_rating, favorites_only,
            collection_id, used_phrasebook_value_id, sort_by, sort_dir,
            system_tag=system_tag, semantic_query=semantic_query
        )

    def get_by_id(
        self,
        generation_id: str,
        user_id: str,
        include_files: bool = True
    ) -> Dict[str, Any]:
        return self._query.get_by_id(generation_id, user_id, include_files)

    def get_tags(self, generation_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self._query.get_tags(generation_id, user_id)

    # --- Writes / file IO (GenerationHistoryArchive) ---

    def set_rating(self, generation_id: str, rating: int, user_id: str) -> int:
        return self._archive.set_rating(generation_id, rating, user_id)

    def set_favorite(self, generation_id: str, is_favorite: bool, user_id: str) -> bool:
        return self._archive.set_favorite(generation_id, is_favorite, user_id)

    def delete(self, generation_id: str, user_id: str) -> Dict[str, Any]:
        return self._archive.delete(generation_id, user_id)

    def bulk_delete_by_tags(self, tag_ids: List[str], user_id: str) -> Dict[str, Any]:
        return self._archive.bulk_delete_by_tags(tag_ids, user_id)

    def bulk_delete(self, generation_ids: List[str], user_id: str) -> Dict[str, Any]:
        return self._archive.bulk_delete(generation_ids, user_id)

    def export_zip(
        self,
        generation_ids: List[str],
        user_id: str,
        strip_metadata: bool = False
    ) -> Tuple[bytes, str]:
        return self._archive.export_zip(generation_ids, user_id, strip_metadata)

    async def upload_generations(
        self,
        files: List,
        tag_ids: List[str],
        user_id: str
    ) -> Dict[str, Any]:
        return await self._archive.upload_generations(files, tag_ids, user_id)

    def export_bundle(self, generation_id: str, user_id: str) -> Tuple[bytes, str]:
        return self._archive.export_bundle(generation_id, user_id)

    def import_bundle(self, content: bytes) -> Dict[str, Any]:
        return self._archive.import_bundle(content)

    def update_tags(
        self,
        generation_id: str,
        tag_ids: List[str],
        user_id: str
    ) -> List[Dict[str, Any]]:
        return self._archive.update_tags(generation_id, tag_ids, user_id)

    def remove_tag(self, generation_id: str, tag_id: str, user_id: str) -> bool:
        return self._archive.remove_tag(generation_id, tag_id, user_id)
