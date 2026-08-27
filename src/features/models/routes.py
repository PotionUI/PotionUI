"""
Model controller - thin layer for HTTP handling.

This controller delegates all business logic to `src.features.models.operations`
(dispatching onto the `ModelIndexCollaborators` role objects) and handles:
- HTTP request/response serialization
- Exception mapping to HTTP status codes
- Response formatting for the API
"""

import asyncio
import logging
from typing import Optional, List, TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Query, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.models.dto import (
    ModelInfoFetchRequest,
    UpdateDescriptionRequest,
    UpdateTagsRequest,
    UpdatePromptingGuidanceRequest,
    UpdateModelMetadataRequest,
    UpdateModelUserAttributesRequest,
    CreateAttributeDefinitionRequest,
    UpdateAttributeDefinitionRequest,
    UpdateModelPreviewRequest,
    AddModelPreviewRequest,
    ReorderModelPreviewsRequest,
    DownloadModelRequest,
    RecommendationDownloadRequest,
    UserModelAssignmentRequest,
    ModelFavoriteRequest,
    ModelLibraryNameRequest,
    ApplyModelsLocationRequest,
)
from src.features.models import (
    ModelIndexCollaborators,
    ModelNotFoundException,
    ModelAccessDeniedException,
    ModelIndexingException,
    ProviderFetchException,
    InvalidTagException,
    InvalidModelMetadataException,
    ModelDownloadException,
    ModelAssignmentException,
)
from src.features.models import operations
from src.features.models.attributes.exceptions import (
    AttributeDefinitionNotFoundException,
    InvalidAttributeDefinitionException,
    SystemAttributeDefinitionException,
)
from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.location import ModelsLocationError
from src.features.models.catalog import ListModelsParams
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.downloads import DownloadQueue

logger = logging.getLogger(__name__)


class ModelController(BaseController):
    """Thin controller for model endpoints.

    Delegates all business logic to `src.features.models.operations` and
    handles HTTP-specific concerns.
    """

    def __init__(
        self,
        model_index_manager: ModelIndexCollaborators,
        user_model_meta_repository: UserModelMetaRepository,
        download_queue: "DownloadQueue",
        attribute_definition_repository: Optional[AttributeDefinitionRepository] = None,
        model_attributes_manager: Optional[ModelAttributeDefinitionsEditor] = None,
    ):
        super().__init__()
        self.collaborators = model_index_manager
        self.user_model_meta_repository = user_model_meta_repository
        self.download_queue = download_queue
        self.attribute_definitions = attribute_definition_repository or AttributeDefinitionRepository()
        self.attributes_manager = model_attributes_manager or ModelAttributeDefinitionsEditor(
            self.attribute_definitions, UserModelAttributeRepository()
        )

    # --- Query Endpoints ---

    async def list_models(
        self,
        user: User,
        model_type: Optional[str] = None,
        tag_ids: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "indexed_at",
        sort_order: str = "desc",
        limit: Optional[int] = 20,
        offset: int = 0,
        include_tags: bool = True,
        all_models: bool = False,
        assignment_filter: Optional[str] = None,
        assigned_user_id: Optional[str] = None,
        assigned_group_id: Optional[str] = None,
        favorites_only: bool = False,
        collection_id: Optional[str] = None,
        in_any_collection: bool = False
    ) -> APIResponse:
        """List all indexed models with optional filtering."""
        try:
            # Parse tag_ids if provided
            parsed_tag_ids = None
            if tag_ids:
                parsed_tag_ids = [tid.strip() for tid in tag_ids.split(',') if tid.strip()]

            params = ListModelsParams(
                model_type=model_type,
                tag_ids=parsed_tag_ids,
                search=search,
                sort_by=sort_by,
                sort_order=sort_order,
                limit=limit,
                offset=offset,
                include_tags=include_tags,
                all_models=all_models,
                assignment_filter=assignment_filter,
                assigned_user_id=assigned_user_id,
                assigned_group_id=assigned_group_id,
                favorites_only=favorites_only,
                collection_id=collection_id,
                in_any_collection=in_any_collection
            )

            data = operations.list_models(self.collaborators, params, user)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error listing models: {e}")
            return self.error_api_response(
                error="list_models_failed",
                message=f"Failed to list models: {str(e)}"
            )

    async def get_model_availability(self, model_id: str, user: User = None) -> APIResponse:
        """Which backends can load this model, and the name each one needs.

        Admin-only: backend topology is operational detail, not something a generating
        user needs or should be able to enumerate.
        """
        if not user or user.account_type != AccountType.ADMIN:
            return self.error_response(
                error="forbidden",
                message="Model availability is available to administrators only",
                status_code=403,
            )
        try:
            return self.success_response(data=operations.get_model_availability(self.collaborators, model_id))
        except Exception as e:
            logger.exception(f"Error getting availability for model {model_id}: {e}")
            return self.error_api_response(
                error="get_model_availability_failed",
                message=f"Failed to get model availability: {str(e)}"
            )

    async def get_model_stats(self) -> APIResponse:
        """Get model indexing statistics."""
        try:
            stats = operations.get_model_stats(self.collaborators)
            return self.success_response(data=stats)
        except Exception as e:
            logger.exception(f"Error getting model stats: {e}")
            return self.error_api_response(
                error="get_stats_failed",
                message=f"Failed to get model stats: {str(e)}"
            )

    async def get_model_types(
        self,
        user: User,
        user_scoped: bool = False,
        include_empty: bool = False
    ) -> APIResponse:
        """Get available model types and their counts."""
        try:
            data = operations.get_model_types(self.collaborators, user, user_scoped, include_empty)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting model types: {e}")
            return self.error_api_response(
                error="get_types_failed",
                message=f"Failed to get model types: {str(e)}"
            )

    async def get_model_by_hash(self, sha256: str) -> APIResponse:
        """Get model by SHA256 hash."""
        try:
            data = operations.get_model_by_hash(self.collaborators, sha256)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error getting model by hash {sha256}: {e}")
            return self.error_api_response(
                error="get_model_failed",
                message=f"Failed to get model: {str(e)}"
            )

    async def get_model_by_id(self, model_id: str, user: User = None) -> APIResponse:
        """Get specific model by ID."""
        try:
            is_admin = bool(user and user.account_type == AccountType.ADMIN)
            data = operations.get_model_by_id(self.collaborators, model_id, user=user, admin=is_admin)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error getting model {model_id}: {e}")
            return self.error_api_response(
                error="get_model_failed",
                message=f"Failed to get model: {str(e)}"
            )

    async def get_model_generations(
        self,
        model_id: str,
        user: User,
        limit: int = 20,
        offset: int = 0
    ) -> APIResponse:
        """Get generations that used a specific model."""
        try:
            data = operations.get_model_generations(self.collaborators, model_id, user, limit, offset)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except ModelAccessDeniedException as e:
            return self.error_api_response(
                error="access_denied",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error getting generations for model {model_id}: {e}")
            return self.error_api_response(
                error="get_generations_failed",
                message=f"Failed to get model generations: {str(e)}"
            )

    # --- Indexing Endpoints ---

    async def count_unindexed_models(self) -> APIResponse:
        """Count model files on disk not yet indexed, by type. No hashing, no writes."""
        try:
            data = operations.count_unindexed(self.collaborators)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error counting unindexed models: {e}")
            return self.error_api_response(
                error="count_unindexed_failed",
                message=f"Failed to count unindexed models: {str(e)}"
            )

    async def index_models(self, background_tasks: BackgroundTasks) -> APIResponse:
        """Start model indexing process."""
        try:
            data = operations.start_indexing(self.collaborators)
            background_tasks.add_task(operations.run_indexing, self.collaborators)
            return self.success_response(data=data)
        except ModelIndexingException as e:
            return self.error_api_response(
                error="indexing_blocked",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error starting model indexing: {e}")
            return self.error_api_response(
                error="indexing_failed",
                message=f"Failed to start indexing: {str(e)}"
            )

    async def delete_model(self, model_id: str) -> APIResponse:
        """Delete model from index (does not delete file)."""
        try:
            data = operations.delete_model(self.collaborators, model_id)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except ModelIndexingException as e:
            return self.error_api_response(
                error="delete_blocked",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error deleting model {model_id}: {e}")
            return self.error_api_response(
                error="delete_failed",
                message=f"Failed to delete model: {str(e)}"
            )

    async def cleanup_deleted_models(self) -> APIResponse:
        """Remove models from index that no longer exist on disk."""
        try:
            data = operations.cleanup_deleted_models(self.collaborators)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error during cleanup: {e}")
            return self.error_api_response(
                error="cleanup_failed",
                message=f"Failed to cleanup models: {str(e)}"
            )

    # --- Models location ---

    async def get_models_location(self) -> APIResponse:
        """Current external models location, per-type overrides, and symlink state."""
        try:
            data = operations.get_models_location(self.collaborators)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting models location: {e}")
            return self.error_api_response(
                error="models_location_failed",
                message=f"Failed to get models location: {str(e)}"
            )

    async def apply_models_location(
        self,
        background_tasks: BackgroundTasks,
        request: ApplyModelsLocationRequest,
    ) -> APIResponse:
        """Point the models directory's symlinks at an external location, then re-index."""
        try:
            data = operations.apply_models_location(self.collaborators, request.external_path, request.overrides)
        except ModelsLocationError as e:
            return self.error_api_response(
                error="models_location_blocked",
                message=e.reason
            )
        except Exception as e:
            logger.exception(f"Error applying models location: {e}")
            return self.error_api_response(
                error="models_location_apply_failed",
                message=f"Failed to apply models location: {str(e)}"
            )

        # Reuses the existing index job (src/features/models/indexing_coordinator.py) -
        # the symlinks now point somewhere new, so the DB needs to catch up with
        # what's reachable through them.
        try:
            operations.start_indexing(self.collaborators)
            background_tasks.add_task(operations.run_indexing, self.collaborators)
        except ModelIndexingException as e:
            logger.warning(f"Models location applied but re-index was blocked: {e}")

        return self.success_response(data=data)

    # --- Provider Endpoints ---

    async def fetch_model_info(
        self,
        background_tasks: BackgroundTasks,
        request: ModelInfoFetchRequest
    ) -> APIResponse:
        """Fetch model metadata from marketplace providers."""
        try:
            data = operations.fetch_provider_info(self.collaborators, 
                provider=request.provider,
                model_ids=request.model_ids,
                force_refresh=request.force_refresh or False
            )

            # Run provider fetch in background
            def background_wrapper():
                asyncio.run(operations.run_provider_fetch(self.collaborators, 
                    provider=request.provider,
                    model_ids=request.model_ids,
                    force_refresh=request.force_refresh or False
                ))

            background_tasks.add_task(background_wrapper)
            return self.success_response(data=data)
        except ProviderFetchException as e:
            return self.error_api_response(
                error="provider_error",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error starting provider fetch: {e}")
            return self.error_api_response(
                error="provider_fetch_failed",
                message=f"Failed to start provider fetch: {str(e)}"
            )

    async def update_model_tags(
        self,
        model_id: str,
        request: UpdateTagsRequest
    ) -> APIResponse:
        """Update tags for a model."""
        try:
            data = operations.update_model_tags(self.collaborators, model_id, request.tag_ids)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except InvalidTagException as e:
            return self.error_api_response(
                error="invalid_tag",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating tags for model {model_id}: {e}")
            return self.error_api_response(
                error="update_tags_failed",
                message=f"Failed to update tags: {str(e)}"
            )

    async def update_model_description(
        self,
        model_id: str,
        request: UpdateDescriptionRequest
    ) -> APIResponse:
        """Update description for a model."""
        try:
            data = operations.update_model_description(self.collaborators, model_id, request.description)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating description for model {model_id}: {e}")
            return self.error_api_response(
                error="update_description_failed",
                message=f"Failed to update description: {str(e)}"
            )

    async def update_model_prompting_guidance(
        self,
        model_id: str,
        request: UpdatePromptingGuidanceRequest
    ) -> APIResponse:
        """Update the admin-authored prompting guidance for a model."""
        try:
            data = operations.update_model_prompting_guidance(self.collaborators, model_id, request.prompting_guidance)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating prompting guidance for model {model_id}: {e}")
            return self.error_api_response(
                error="update_prompting_guidance_failed",
                message=f"Failed to update prompting guidance: {str(e)}"
            )

    async def update_model_metadata(
        self,
        model_id: str,
        request: UpdateModelMetadataRequest
    ) -> APIResponse:
        """Update a model's shared attribute values."""
        try:
            data = operations.update_model_metadata(self.collaborators, model_id, request.values)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except InvalidModelMetadataException as e:
            return self.error_api_response(
                error="invalid_model_metadata",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating metadata for model {model_id}: {e}")
            return self.error_api_response(
                error="update_metadata_failed",
                message=f"Failed to update metadata: {str(e)}"
            )

    async def update_model_user_attributes(
        self,
        model_id: str,
        request: UpdateModelUserAttributesRequest,
        user: User,
    ) -> APIResponse:
        """Update the caller's per-user attribute value overlay for a model."""
        try:
            values = self.attributes_manager.update_user_values(model_id, user.id, request.values)
            return self.success_response(data={"values": values})
        except InvalidModelMetadataException as e:
            return self.error_api_response(
                error="invalid_model_metadata",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating user attributes for model {model_id}: {e}")
            return self.error_api_response(
                error="update_user_attributes_failed",
                message=f"Failed to update user attributes: {str(e)}"
            )

    # --- Attribute definitions (admin-managed, GET is open to any active user) ---

    async def list_attribute_definitions(self, user: User) -> APIResponse:
        """List attribute definitions. Non-admins never see `admin_only` ones."""
        try:
            definitions = self.attribute_definitions.list_all()
            is_admin = user.account_type == AccountType.ADMIN
            visible = [d for d in definitions if is_admin or not d.admin_only]
            return self.success_response(data={"definitions": [d.to_dict() for d in visible]})
        except Exception as e:
            logger.exception(f"Error listing attribute definitions: {e}")
            return self.error_api_response(
                error="list_attributes_failed",
                message=f"Failed to list attribute definitions: {str(e)}"
            )

    async def create_attribute_definition(self, request: CreateAttributeDefinitionRequest) -> APIResponse:
        try:
            definition = self.attributes_manager.create(request.model_dump())
            return self.success_response(data={"definition": definition.to_dict()})
        except InvalidAttributeDefinitionException as e:
            return self.error_api_response(error="invalid_attribute_definition", message=str(e))
        except Exception as e:
            logger.exception(f"Error creating attribute definition: {e}")
            return self.error_api_response(
                error="create_attribute_failed",
                message=f"Failed to create attribute definition: {str(e)}"
            )

    async def update_attribute_definition(
        self, definition_id: str, request: UpdateAttributeDefinitionRequest
    ) -> APIResponse:
        try:
            definition = self.attributes_manager.update(definition_id, request.model_dump(exclude_unset=True))
            return self.success_response(data={"definition": definition.to_dict()})
        except AttributeDefinitionNotFoundException as e:
            return self.error_api_response(error="attribute_not_found", message=str(e))
        except (InvalidAttributeDefinitionException, SystemAttributeDefinitionException) as e:
            return self.error_api_response(error="invalid_attribute_definition", message=str(e))
        except Exception as e:
            logger.exception(f"Error updating attribute definition {definition_id}: {e}")
            return self.error_api_response(
                error="update_attribute_failed",
                message=f"Failed to update attribute definition: {str(e)}"
            )

    async def delete_attribute_definition(self, definition_id: str) -> APIResponse:
        try:
            self.attributes_manager.delete(definition_id)
            return self.success_response(data={"message": "Attribute definition deleted"})
        except AttributeDefinitionNotFoundException as e:
            return self.error_api_response(error="attribute_not_found", message=str(e))
        except SystemAttributeDefinitionException as e:
            return self.error_api_response(error="system_attribute_definition", message=str(e))
        except Exception as e:
            logger.exception(f"Error deleting attribute definition {definition_id}: {e}")
            return self.error_api_response(
                error="delete_attribute_failed",
                message=f"Failed to delete attribute definition: {str(e)}"
            )

    async def update_model_preview(
        self,
        model_id: str,
        request: UpdateModelPreviewRequest,
        user_id: Optional[str] = None,
    ) -> APIResponse:
        """Set or clear the admin-set preview media for a model."""
        try:
            preview = request.preview.model_dump() if request.preview else None
            data = operations.update_model_preview(self.collaborators, model_id, preview, user_id)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(
                error="model_not_found",
                message=str(e)
            )
        except ModelIndexingException as e:
            return self.error_api_response(
                error="invalid_preview",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating preview for model {model_id}: {e}")
            return self.error_api_response(
                error="update_preview_failed",
                message=f"Failed to update preview: {str(e)}"
            )

    async def list_model_previews(self, model_id: str, user: User) -> APIResponse:
        """List a model's admin-set previews, ordered (position 0 = primary).

        Any user who can reach the model sees its previews - not admin
        only. A model outside the caller's access reports the same
        "model_not_found" error as a missing one (404-not-403).
        """
        try:
            previews = operations.list_model_previews_for_user(self.collaborators, model_id, user)
            return self.success_response(data={"previews": previews})
        except ModelNotFoundException as e:
            return self.error_api_response(error="model_not_found", message=str(e))
        except Exception as e:
            logger.exception(f"Error listing previews for model {model_id}: {e}")
            return self.error_api_response(
                error="list_previews_failed",
                message=f"Failed to list previews: {str(e)}"
            )

    async def add_model_preview(
        self,
        model_id: str,
        request: AddModelPreviewRequest,
        user_id: Optional[str] = None,
    ) -> APIResponse:
        """Append one preview to a model's preview list."""
        try:
            data = operations.add_model_preview(self.collaborators, model_id, request.preview.model_dump(), user_id)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(error="model_not_found", message=str(e))
        except ModelIndexingException as e:
            return self.error_api_response(error="invalid_preview", message=str(e))
        except Exception as e:
            logger.exception(f"Error adding preview for model {model_id}: {e}")
            return self.error_api_response(
                error="add_preview_failed",
                message=f"Failed to add preview: {str(e)}"
            )

    async def delete_model_preview(self, model_id: str, preview_id: str) -> APIResponse:
        """Remove one preview from a model's preview list."""
        try:
            data = operations.delete_model_preview(self.collaborators, model_id, preview_id)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(error="model_not_found", message=str(e))
        except Exception as e:
            logger.exception(f"Error deleting preview {preview_id} for model {model_id}: {e}")
            return self.error_api_response(
                error="delete_preview_failed",
                message=f"Failed to delete preview: {str(e)}"
            )

    async def reorder_model_previews(
        self, model_id: str, request: ReorderModelPreviewsRequest
    ) -> APIResponse:
        """Reorder a model's preview list."""
        try:
            data = operations.reorder_model_previews(self.collaborators, model_id, request.ordered_ids)
            return self.success_response(data=data)
        except ModelNotFoundException as e:
            return self.error_api_response(error="model_not_found", message=str(e))
        except ModelIndexingException as e:
            return self.error_api_response(error="invalid_reorder", message=str(e))
        except Exception as e:
            logger.exception(f"Error reordering previews for model {model_id}: {e}")
            return self.error_api_response(
                error="reorder_previews_failed",
                message=f"Failed to reorder previews: {str(e)}"
            )

    # --- Library Endpoints ---

    async def set_model_favorite(
        self,
        model_id: str,
        request: ModelFavoriteRequest,
        user: User
    ) -> APIResponse:
        """Set or clear the favorite flag for a model (scoped to the current user)."""
        try:
            meta = self.user_model_meta_repository.set_favorite(user.id, model_id, request.is_favorite)
            return self.success_response(data={
                "message": "Model favorite updated successfully",
                "meta": meta.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="set_favorite_failed", message=str(e))
        except Exception as e:
            logger.exception(f"Error setting favorite for model {model_id}: {e}")
            return self.error_api_response(
                error="set_favorite_failed",
                message=f"Failed to update favorite: {str(e)}"
            )

    async def set_model_library_name(
        self,
        model_id: str,
        request: ModelLibraryNameRequest,
        user: User
    ) -> APIResponse:
        """Set or clear a per-user custom display name for a model."""
        try:
            # Empty/whitespace name normalizes to a cleared custom name.
            name = request.name.strip() or None if request.name is not None else None
            meta = self.user_model_meta_repository.set_custom_name(user.id, model_id, name)
            return self.success_response(data={
                "message": "Model library name updated successfully",
                "meta": meta.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="set_library_name_failed", message=str(e))
        except Exception as e:
            logger.exception(f"Error setting library name for model {model_id}: {e}")
            return self.error_api_response(
                error="set_library_name_failed",
                message=f"Failed to update library name: {str(e)}"
            )

    # --- Thumbnail Endpoints ---

    async def generate_video_thumbnails(
        self,
        background_tasks: BackgroundTasks,
        model_ids: Optional[List[str]] = None
    ) -> APIResponse:
        """Generate thumbnails from videos for models that don't have images."""
        try:
            data = operations.start_thumbnail_generation(self.collaborators, model_ids)

            # Run thumbnail generation in background
            async def run_task():
                await operations.run_thumbnail_generation(self.collaborators, model_ids)

            background_tasks.add_task(asyncio.run, run_task())
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error starting video thumbnail generation: {e}")
            return self.error_api_response(
                error="thumbnail_generation_failed",
                message=f"Failed to start thumbnail generation: {str(e)}"
            )

    # --- User Assignment Endpoints ---

    async def get_user_model_assignments(self, user_id: str) -> APIResponse:
        """Get model assignments for a user (admin only)."""
        try:
            data = operations.get_user_model_assignments(self.collaborators, user_id)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting user model assignments: {e}")
            return self.error_api_response(
                error="get_assignments_failed",
                message=f"Failed to get user model assignments: {str(e)}"
            )

    async def get_model_assignments(self, model_id: str) -> APIResponse:
        """Get the users directly assigned to a model (admin only)."""
        try:
            data = operations.get_model_assignments(self.collaborators, model_id)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting model assignments: {e}")
            return self.error_api_response(
                error="get_assignments_failed",
                message=f"Failed to get model assignments: {str(e)}"
            )

    async def get_model_assignment_summary(self) -> APIResponse:
        """Direct-user and group assignment counts for every model (admin only)."""
        try:
            data = operations.get_model_assignment_summary(self.collaborators)
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting model assignment summary: {e}")
            return self.error_api_response(
                error="get_assignment_summary_failed",
                message=f"Failed to get model assignment summary: {str(e)}"
            )

    async def assign_model_to_user(
        self,
        request: UserModelAssignmentRequest
    ) -> APIResponse:
        """Assign a model to a user (admin only)."""
        try:
            data = operations.assign_model_to_user(self.collaborators, request.model_id, request.user_id)
            return self.success_response(data=data)
        except ModelAssignmentException as e:
            return self.error_api_response(
                error="assignment_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error assigning model to user: {e}")
            return self.error_api_response(
                error="assignment_failed",
                message=f"Failed to assign model to user: {str(e)}"
            )

    async def unassign_model_from_user(
        self,
        user_id: str,
        model_id: str
    ) -> APIResponse:
        """Unassign a model from a user (admin only)."""
        try:
            data = operations.unassign_model_from_user(self.collaborators, model_id, user_id)
            return self.success_response(data=data)
        except ModelAssignmentException as e:
            return self.error_api_response(
                error="unassignment_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error unassigning model from user: {e}")
            return self.error_api_response(
                error="unassignment_failed",
                message=f"Failed to unassign model from user: {str(e)}"
            )

    # --- Download Endpoints ---

    async def download_and_index_model(
        self,
        background_tasks: BackgroundTasks,
        request: DownloadModelRequest
    ) -> APIResponse:
        """Download a model from a URL and index it."""
        try:
            data = operations.start_download_and_index(self.collaborators, 
                name=request.name,
                link=request.link,
                size=request.size,
                sha256=request.sha256,
                model_type=request.model_type or 'checkpoint'
            )

            # Run download and index in background
            async def run_task():
                await operations.run_download_and_index(self.collaborators, 
                    name=request.name,
                    link=request.link,
                    sha256=request.sha256,
                    model_type=request.model_type or 'checkpoint'
                )

            background_tasks.add_task(asyncio.run, run_task())
            return self.success_response(data=data)
        except ModelDownloadException as e:
            return self.error_api_response(
                error="download_blocked",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error starting download: {e}")
            return self.error_api_response(
                error="download_failed",
                message=f"Failed to start download: {str(e)}"
            )

    # --- Recommendation Downloads (v2, provider-gated - see docs/presets.md) ---

    @staticmethod
    def _parse_ref(ref: Optional[str]):
        """`ref` is an opaque, provider-native string (see `RecommendationDownloadRequest`).
        Resolve it to the `(provider_model_id, provider_version_id)` pair every
        `MarketplaceProviderBase.get_download_url` implementation expects, trying
        three conventions in order (not a validated shape - a provider is free to
        invent its own `ref` JSON keys; these are just the ones known providers
        already use):

        1. `{"provider_model_id", "provider_version_id"}` - already-final, generic,
           passed straight through (e.g. civitai's ref: `{model_id, version_id}` -
           see below - or any future provider that just hands back these two).
        2. `{"model_id", "version_id"}` - civitai's natural shape, mapped directly
           (`model_id`->`provider_model_id`, `version_id`->`provider_version_id`).
        3. `{"repo", "file", "revision"?}` - huggingface's natural shape. Its
           `get_download_url` wants `provider_version_id` as `"{revision}@{file}"`
           (`content/plugins/marketplace/huggingface-provider/provider/huggingface_provider.py);
           this is the one provider-specific mapping shim, since core has no
           business knowing HF's version-id string format otherwise.
        4. Not JSON / no dict / none of the above keys present: the whole string is
           `provider_model_id` with no version - so a provider whose `ref` really is
           just a bare ID still works with zero ceremony.
        """
        if not ref:
            return None, None

        try:
            import json
            parsed = json.loads(ref)
        except (ValueError, TypeError):
            parsed = None

        if isinstance(parsed, dict):
            if parsed.get("provider_model_id"):
                return parsed.get("provider_model_id"), parsed.get("provider_version_id")
            if parsed.get("model_id"):
                return parsed.get("model_id"), parsed.get("version_id")
            if parsed.get("repo"):
                revision = parsed.get("revision") or "main"
                file_path = parsed.get("file")
                version_id = f"{revision}@{file_path}" if file_path else None
                return parsed.get("repo"), version_id

        return ref, None

    async def queue_recommendation_download(
        self,
        request: RecommendationDownloadRequest,
        current_user: User
    ) -> APIResponse:
        """Queue a `model` field recommendation for download (admin only).

        Resolves a provider-backed recommendation's URL via the provider registry
        (using `ref`'s conventional `provider_model_id`/`provider_version_id` keys),
        or uses `link` directly for a provider-less one, then hands off to the
        core download queue's existing worker machinery.
        """
        if current_user.account_type != AccountType.ADMIN:
            return self.error_api_response(
                error="permission_denied", message="Admin access required"
            )

        try:
            url = request.link
            if request.provider:
                from src.features.providers.registry import ensure_providers_discovered
                provider_registry = await ensure_providers_discovered()
                provider_model_id, provider_version_id = self._parse_ref(request.ref)
                url = await provider_registry.get_download_url(
                    request.provider,
                    provider_model_id,
                    provider_version_id,
                )
                if not url:
                    return self.error_api_response(
                        error="download_url_unresolved",
                        message=f"Could not resolve a download URL from provider '{request.provider}'"
                    )

            if not url:
                return self.error_api_response(
                    error="missing_source", message="Either 'provider' + 'ref' or 'link' is required"
                )

            # `DownloadQueue.queue_model_download` resolves `model_type` against
            # the configured model depot itself (TYPE_DIR_MAP, contained inside the
            # depot) - no need to duplicate that resolution here.
            download = await self.download_queue.queue_model_download(
                url=url,
                model_type=request.model_type,
                checksum_sha256=request.sha256,
                provider_id=request.provider,
                created_by=current_user.id,
            )

            return self.success_response(data={"download_id": download.id})
        except Exception as e:
            logger.exception(f"Error queueing recommendation download: {e}")
            return self.error_api_response(
                error="download_failed", message=f"Failed to queue download: {str(e)}"
            )

    # The download queue's own DownloadStatus is richer (pending/downloading/
    # paused/completed/failed/cancelled) than this endpoint's documented contract
    # (`pending|running|completed|failed`) - collapse it rather than leaking the
    # queue's internal vocabulary to callers of this endpoint.
    _STATUS_MAP = {
        "pending": "pending",
        "downloading": "running",
        "paused": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "failed",
    }

    async def get_recommendation_download(self, download_id: str) -> APIResponse:
        """Poll a recommendation download's status/progress by ID."""
        try:
            download = self.download_queue.get_download(download_id)
            status = self._STATUS_MAP.get(download.status.value, download.status.value)
            return self.success_response(data={
                "status": status,
                "progress": download.progress if status != "pending" else None,
                "error": download.error_message,
            })
        except Exception as e:
            return self.error_api_response(error="download_not_found", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.model_controller
    router = APIRouter(prefix="/api/models", tags=["Models"])

    # ============ Router Endpoints ============

    @router.get("", response_model=APIResponse, summary="List Models")
    async def list_models(
        model_type: Optional[str] = Query(None, description="Filter by model type"),
        tag_ids: Optional[str] = Query(None, description="Comma-separated tag IDs to filter by"),
        search: Optional[str] = Query(None, description="Search models by name (case-insensitive)"),
        sort_by: Optional[str] = Query("indexed_at", description="Field to sort by"),
        sort_order: Optional[str] = Query("desc", description="Sort order: 'asc' or 'desc'"),
        limit: Optional[int] = Query(20, description="Limit number of results"),
        offset: int = Query(0, description="Offset for pagination"),
        include_tags: bool = Query(True, description="Include tags"),
        all_models: bool = Query(False, description="Return all models (admin only)"),
        assignment_filter: Optional[str] = Query(None, description="Filter by assignment: 'assigned' or 'unassigned'"),
        assigned_user_id: Optional[str] = Query(None, description="User ID for assignment filtering"),
        assigned_group_id: Optional[str] = Query(None, description="Group ID for assignment filtering"),
        favorites_only: bool = Query(False, description="Only return models favorited by the current user"),
        collection_id: Optional[str] = Query(None, description="Only return models in this model collection"),
        in_any_collection: bool = Query(False, description="Only return models that belong to any of the current user's collections"),
        current_user: User = Depends(get_current_active_user)
    ):
        """List all indexed models with optional filtering."""
        return await controller.list_models(
            user=current_user,
            model_type=model_type,
            tag_ids=tag_ids,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
            include_tags=include_tags,
            all_models=all_models,
            assignment_filter=assignment_filter,
            assigned_user_id=assigned_user_id,
            assigned_group_id=assigned_group_id,
            favorites_only=favorites_only,
            collection_id=collection_id,
            in_any_collection=in_any_collection
        )


    @router.get("/stats", response_model=APIResponse, summary="Get Model Stats")
    async def get_model_stats(current_user: User = Depends(get_current_active_user)):
        """Get model indexing statistics."""
        return await controller.get_model_stats()


    @router.get("/types", response_model=APIResponse, summary="Get Model Types")
    async def get_model_types(
        user_scoped: bool = Query(False, description="When true, counts only models assigned to the current user"),
        include_empty: bool = Query(False, description="When true (admin, non-user_scoped only), also include known model types with zero indexed models"),
        current_user: User = Depends(get_current_active_user)
    ):
        """Get available model types and their counts."""
        return await controller.get_model_types(current_user, user_scoped, include_empty)


    @router.get("/unindexed-count", response_model=APIResponse, summary="Count Unindexed Models")
    async def count_unindexed_models(current_user: User = Depends(get_current_admin_user)):
        """Count model files on disk not yet indexed, by type - no hashing, no writes."""
        return await controller.count_unindexed_models()


    # Static, so it must be registered before the "/{model_id}" catch-all below -
    # "attributes" would otherwise be dispatched to it as a model id.
    @router.get("/attributes", response_model=APIResponse, summary="List Attribute Definitions")
    async def list_attribute_definitions(current_user: User = Depends(get_current_active_user)):
        """List model attribute definitions (core + plugin + admin-authored).
        `admin_only` definitions are filtered out for non-admins."""
        return await controller.list_attribute_definitions(current_user)


    @router.post("/attributes", response_model=APIResponse, summary="Create Attribute Definition")
    async def create_attribute_definition(
        request: CreateAttributeDefinitionRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Create a model attribute definition (admin only)."""
        return await controller.create_attribute_definition(request)


    @router.put("/attributes/{definition_id}", response_model=APIResponse, summary="Update Attribute Definition")
    async def update_attribute_definition(
        definition_id: str,
        request: UpdateAttributeDefinitionRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Update a model attribute definition (admin only). On a system
        definition, `key`/`field_type` are immutable."""
        return await controller.update_attribute_definition(definition_id, request)


    @router.delete("/attributes/{definition_id}", response_model=APIResponse, summary="Delete Attribute Definition")
    async def delete_attribute_definition(
        definition_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Delete a model attribute definition (admin only). Rejected for system definitions."""
        return await controller.delete_attribute_definition(definition_id)


    @router.get("/hash/{sha256}", response_model=APIResponse, summary="Get Model by Hash")
    async def get_model_by_hash(
        sha256: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Get model by SHA256 hash."""
        return await controller.get_model_by_hash(sha256)


    @router.get("/user-assignments/{user_id}", response_model=APIResponse, summary="Get User Model Assignments")
    async def get_user_model_assignments(
        user_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Get model assignments for a user (admin only)."""
        return await controller.get_user_model_assignments(user_id)


    @router.get("/{model_id}/generations", response_model=APIResponse, summary="Get Model Generations")
    async def get_model_generations(
        model_id: str,
        limit: int = Query(20, description="Limit number of results"),
        offset: int = Query(0, description="Offset for pagination"),
        current_user: User = Depends(get_current_active_user)
    ):
        """Get generations that used a specific model."""
        return await controller.get_model_generations(
            model_id, current_user, limit, offset
        )


    @router.get("/{model_id}/availability", response_model=APIResponse, summary="Get Model Availability")
    async def get_model_availability(
        model_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """List the backends that can load this model, with each one's ref, size and confidence."""
        return await controller.get_model_availability(model_id, current_user)


    @router.get("/{model_id}/assignments", response_model=APIResponse, summary="Get Model Assignments")
    async def get_model_assignments(
        model_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Get the users directly assigned to a model (admin only)."""
        return await controller.get_model_assignments(model_id)


    # Static, so it must be registered before the "/{model_id}" catch-all below
    # (see that route's comment) - "assignment-summary" would otherwise be
    # dispatched to it as a model id.
    @router.get("/assignment-summary", response_model=APIResponse, summary="Get Model Assignment Summary")
    async def get_model_assignment_summary(current_user: User = Depends(get_current_admin_user)):
        """Direct-user and group assignment counts for every model (admin only)."""
        return await controller.get_model_assignment_summary()




    @router.get("/location", response_model=APIResponse, summary="Get Models Location")
    async def get_models_location(current_user: User = Depends(get_current_admin_user)):
        """Current external models location, per-type overrides, and symlink state (admin only)."""
        return await controller.get_models_location()


    @router.post("/location/apply", response_model=APIResponse, summary="Apply Models Location")
    async def apply_models_location(
        background_tasks: BackgroundTasks,
        request: ApplyModelsLocationRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Point the models directory's symlinks at an external location, then re-index (admin only)."""
        return await controller.apply_models_location(background_tasks, request)


    # Registered after every static /... sibling: FastAPI dispatches in
    # registration order, so this catch-all must come last among GETs or it
    # swallows them as model ids ("Model 'location' not found").
    @router.get("/{model_id}", response_model=APIResponse, summary="Get Model by ID")
    async def get_model_by_id(
        model_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Get specific model by ID."""
        return await controller.get_model_by_id(model_id, current_user)


    @router.post("/index", response_model=APIResponse, summary="Index Models")
    async def index_models(
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Start model indexing process."""
        return await controller.index_models(background_tasks)


    @router.post("/info/fetch", response_model=APIResponse, summary="Fetch Provider Info")
    async def fetch_model_info(
        background_tasks: BackgroundTasks,
        request: ModelInfoFetchRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Fetch model metadata from marketplace providers."""
        return await controller.fetch_model_info(background_tasks, request)


    @router.post("/cleanup", response_model=APIResponse, summary="Cleanup Deleted Models")
    async def cleanup_deleted_models(current_user: User = Depends(get_current_admin_user)):
        """Remove models from index that no longer exist on disk."""
        return await controller.cleanup_deleted_models()


    @router.post("/thumbnails/generate", response_model=APIResponse, summary="Generate Thumbnails")
    async def generate_video_thumbnails(
        background_tasks: BackgroundTasks,
        model_ids: Optional[List[str]] = Query(None, description="Specific model IDs to process (if empty, processes all models)"),
        current_user: User = Depends(get_current_admin_user)
    ):
        """Generate thumbnails from videos for models that don't have images."""
        return await controller.generate_video_thumbnails(background_tasks, model_ids)


    @router.post("/user-assignments", response_model=APIResponse, summary="Assign Model to User")
    async def assign_model_to_user(
        request: UserModelAssignmentRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Assign a model to a user (admin only)."""
        return await controller.assign_model_to_user(request)


    @router.post("/downloads", response_model=APIResponse, summary="Queue Recommendation Download")
    async def queue_recommendation_download(
        request: RecommendationDownloadRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Queue a `model` field recommendation for download (admin only). Provider-gated -
        see docs/presets.md "recommendations"."""
        return await controller.queue_recommendation_download(request, current_user)


    @router.get("/downloads/{download_id}", response_model=APIResponse, summary="Get Recommendation Download Status")
    async def get_recommendation_download(
        download_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Poll a recommendation download's status/progress by ID."""
        return await controller.get_recommendation_download(download_id)


    @router.post("/download", response_model=APIResponse, summary="Download and Index Model")
    async def download_and_index_model(
        background_tasks: BackgroundTasks,
        request: DownloadModelRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Download a model from a URL and index it."""
        return await controller.download_and_index_model(background_tasks, request)


    @router.put("/{model_id}/tags", response_model=APIResponse, summary="Update Model Tags")
    async def update_model_tags(
        model_id: str,
        request: UpdateTagsRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Update tags for a model (admin only)."""
        return await controller.update_model_tags(model_id, request)


    @router.put("/{model_id}/description", response_model=APIResponse, summary="Update Model Description")
    async def update_model_description(
        model_id: str,
        request: UpdateDescriptionRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Update description for a model (admin only)."""
        return await controller.update_model_description(model_id, request)


    @router.put("/{model_id}/prompting-guidance", response_model=APIResponse, summary="Update Model Prompting Guidance")
    async def update_model_prompting_guidance(
        model_id: str,
        request: UpdatePromptingGuidanceRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Update the admin-authored prompting guidance for a model (admin only)."""
        return await controller.update_model_prompting_guidance(model_id, request)


    @router.put("/{model_id}/preview", response_model=APIResponse, summary="Update Model Preview")
    async def update_model_preview(
        model_id: str,
        request: UpdateModelPreviewRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Set or clear a model's admin-set preview media (admin only)."""
        return await controller.update_model_preview(model_id, request, current_user.id)


    @router.get("/{model_id}/previews", response_model=APIResponse, summary="List Model Previews")
    async def list_model_previews(
        model_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """List a model's admin-set previews, ordered (position 0 = primary).

        Any user with access to the model may list its previews - viewing a
        model's own admin-set media isn't an operational/admin concern (see
        `add_model_preview`/`delete_model_preview`/`reorder_model_previews`
        below, which stay admin-only since they mutate the list).
        """
        return await controller.list_model_previews(model_id, current_user)


    @router.post("/{model_id}/previews", response_model=APIResponse, summary="Add Model Preview")
    async def add_model_preview(
        model_id: str,
        request: AddModelPreviewRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Append one preview to a model's preview list (admin only)."""
        return await controller.add_model_preview(model_id, request, current_user.id)


    @router.delete("/{model_id}/previews/{preview_id}", response_model=APIResponse, summary="Delete Model Preview")
    async def delete_model_preview(
        model_id: str,
        preview_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Remove one preview from a model's preview list (admin only)."""
        return await controller.delete_model_preview(model_id, preview_id)


    @router.put("/{model_id}/previews/order", response_model=APIResponse, summary="Reorder Model Previews")
    async def reorder_model_previews(
        model_id: str,
        request: ReorderModelPreviewsRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Reorder a model's preview list (admin only)."""
        return await controller.reorder_model_previews(model_id, request)


    @router.put("/{model_id}/metadata", response_model=APIResponse, summary="Update Model Metadata")
    async def update_model_metadata(
        model_id: str,
        request: UpdateModelMetadataRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Update a model's shared attribute values (admin only)."""
        return await controller.update_model_metadata(model_id, request)


    @router.put("/{model_id}/attributes/user", response_model=APIResponse, summary="Update Model User Attributes")
    async def update_model_user_attributes(
        model_id: str,
        request: UpdateModelUserAttributesRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Update the caller's per-user attribute value overlay for a model."""
        return await controller.update_model_user_attributes(model_id, request, current_user)


    @router.put("/{model_id}/favorite", response_model=APIResponse, summary="Set Model Favorite")
    async def set_model_favorite(
        model_id: str,
        request: ModelFavoriteRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Set or clear the favorite flag for a model (scoped to the current user)."""
        return await controller.set_model_favorite(model_id, request, current_user)


    @router.put("/{model_id}/library-name", response_model=APIResponse, summary="Set Model Library Name")
    async def set_model_library_name(
        model_id: str,
        request: ModelLibraryNameRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Set or clear a per-user custom display name for a model."""
        return await controller.set_model_library_name(model_id, request, current_user)


    @router.delete("/user-assignments/{user_id}/{model_id}", response_model=APIResponse, summary="Unassign Model from User")
    async def unassign_model_from_user(
        user_id: str,
        model_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Unassign a model from a user (admin only)."""
        return await controller.unassign_model_from_user(user_id, model_id)


    @router.delete("/{model_id}", response_model=APIResponse, summary="Delete Model")
    async def delete_model(
        model_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        """Delete model from index (does not delete file)."""
        return await controller.delete_model(model_id)


    return router
