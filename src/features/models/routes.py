"""
Model controller - thin layer for HTTP handling.

This controller delegates all business logic to `src.features.models.operations`
(dispatching onto the `ModelIndexCollaborators` role objects) and handles:
- HTTP request/response serialization
- Exception mapping to HTTP status codes
- Response formatting for the API
"""

import asyncio
from pathlib import Path
from typing import Any, Optional, List, TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Query, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.failure_text import failure_detail
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.models.dto import (
    ModelInfoFetchRequest,
    UpdateDescriptionRequest,
    UpdateTagsRequest,
    UpdatePromptingGuidanceRequest,
    SetModelTypeRequest,
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
    IndexingDigestsRequest,
    IndexingSettingsRequest,
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
from src.features.downloads.exceptions import DownloadNotFoundException
from src.features.downloads.utils import derived_download_name
from src.features.models.recommendations import RECOMMENDATION_EXTENSIONS, parse_ref
from src.features.models import operations
from src.features.models.exceptions import (
    ModelDigestUnavailableException,
    InvalidModelTypeException,
    ModelTypeConflictException,
    ModelTypeNotAssignableException,
)
from src.features.models.attributes.exceptions import (
    AttributeDefinitionNotFoundException,
    InvalidAttributeDefinitionException,
    SystemAttributeDefinitionException,
)
from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.catalog import ListModelsParams
from src.features.models.search_filter import InvalidModelSearch, parse_model_search
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.platform.security.user import User, AccountType
from src.features.content_safety.restrict import provider_flags_nsfw, restrict_model_list, strip_model_media

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.downloads import DownloadQueue


_NOT_FOUND = {ModelNotFoundException: Fail("model_not_found")}


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
        content_safety: Optional[Any] = None,
    ):
        super().__init__()
        self.content_safety = content_safety
        self.collaborators = model_index_manager
        self.user_model_meta_repository = user_model_meta_repository
        self.download_queue = download_queue
        self.attribute_definitions = attribute_definition_repository or AttributeDefinitionRepository()
        self.attributes_manager = model_attributes_manager or ModelAttributeDefinitionsEditor(
            self.attribute_definitions, UserModelAttributeRepository()
        )

    def _restricted(self, user: Optional[User]) -> bool:
        return bool(
            self.content_safety is not None and user is not None and self.content_safety.is_restricted(user.id)
        )

    # --- Query Endpoints ---

    @guarded(
        {InvalidModelSearch: Fail("invalid_model_search", status=422)},
        Fail("list_models_failed", "Failed to list models", detail=failure_detail),
        admin="user",
    )
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
        in_any_collection: bool = False,
        include_descendants: bool = True,
        unsorted: bool = False,
        q_mode: Optional[str] = None,
        indexed_from: Optional[str] = None,
        indexed_to: Optional[str] = None,
        used: Optional[str] = None,
        min_uses: Optional[int] = None,
        last_used_from: Optional[str] = None,
        last_used_to: Optional[str] = None,
    ) -> APIResponse:
        search, search_filter = parse_model_search(
            search=search,
            q_mode=q_mode,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
            used=used,
            min_uses=min_uses,
            last_used_from=last_used_from,
            last_used_to=last_used_to,
        )
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
            in_any_collection=in_any_collection,
            include_descendants=include_descendants,
            unsorted=unsorted,
            search_filter=search_filter,
        )

        data = await operations.list_models(self.collaborators, params, user)
        if self._restricted(user):
            visible = restrict_model_list(data.get("models") or [])
            removed = len(data.get("models") or []) - len(visible)
            data = {**data, "models": visible, "total": max(0, data.get("total", 0) - removed)}
        return self.success_response(data=data)

    @guarded(
        fallback=Fail("get_model_availability_failed", "Failed to get model availability", detail=failure_detail),
        admin="user",
    )
    async def get_model_availability(self, model_id: str, user: User = None) -> APIResponse:
        if not user or user.account_type != AccountType.ADMIN:
            return self.error_response(
                error="forbidden",
                message="Model availability is available to administrators only",
                status_code=403,
            )
        data = await operations.get_model_availability(self.collaborators, model_id)
        return self.success_response(data=data)

    @guarded(fallback=Fail("get_stats_failed", "Failed to get model stats", detail=failure_detail))
    async def get_model_stats(self) -> APIResponse:
        stats = await operations.get_model_stats(self.collaborators)
        return self.success_response(data=stats)

    @guarded(
        {InvalidModelSearch: Fail("invalid_model_search", status=422)},
        Fail("get_types_failed", "Failed to get model types", detail=failure_detail),
        admin="user",
    )
    async def get_model_types(
        self,
        user: User,
        user_scoped: bool = False,
        include_empty: bool = False,
        model_type: Optional[str] = None,
        tag_ids: Optional[str] = None,
        search: Optional[str] = None,
        assignment_filter: Optional[str] = None,
        assigned_user_id: Optional[str] = None,
        assigned_group_id: Optional[str] = None,
        favorites_only: bool = False,
        collection_id: Optional[str] = None,
        in_any_collection: bool = False,
        include_descendants: bool = True,
        unsorted: bool = False,
        q_mode: Optional[str] = None,
        indexed_from: Optional[str] = None,
        indexed_to: Optional[str] = None,
        used: Optional[str] = None,
        min_uses: Optional[int] = None,
        last_used_from: Optional[str] = None,
        last_used_to: Optional[str] = None,
        include_tag_counts: bool = False,
    ) -> APIResponse:
        search, search_filter = parse_model_search(
            search=search,
            q_mode=q_mode,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
            used=used,
            min_uses=min_uses,
            last_used_from=last_used_from,
            last_used_to=last_used_to,
        )
        parsed_tag_ids = [tid.strip() for tid in tag_ids.split(',') if tid.strip()] if tag_ids else None
        faceted = bool(
            parsed_tag_ids or search or not search_filter.is_empty or assignment_filter
            or favorites_only or collection_id or in_any_collection or unsorted or model_type
        )
        facets = ListModelsParams(
            model_type=model_type,
            tag_ids=parsed_tag_ids,
            search=search,
            assignment_filter=assignment_filter,
            assigned_user_id=assigned_user_id,
            assigned_group_id=assigned_group_id,
            favorites_only=favorites_only,
            collection_id=collection_id,
            in_any_collection=in_any_collection,
            include_descendants=include_descendants,
            unsorted=unsorted,
            search_filter=search_filter,
        ) if faceted else None
        data = await operations.get_model_types(
            self.collaborators, user, user_scoped, include_empty, facets, include_tag_counts
        )
        return self.success_response(data=data)

    @guarded(_NOT_FOUND, Fail("get_model_failed", "Failed to get model", detail=failure_detail))
    async def get_model_by_hash(self, sha256: str) -> APIResponse:
        data = await operations.get_model_by_hash(self.collaborators, sha256)
        return self.success_response(data=data)

    @guarded(_NOT_FOUND, Fail("get_model_failed", "Failed to get model", detail=failure_detail), admin="user")
    async def get_model_by_id(self, model_id: str, user: User = None) -> APIResponse:
        is_admin = bool(user and user.account_type == AccountType.ADMIN)
        data = await operations.get_model_by_id(self.collaborators, model_id, user=user, admin=is_admin)
        if self._restricted(user):
            full = self.collaborators.catalog.model_repo.get_by_id(model_id, include_providers=True)
            if full is None or provider_flags_nsfw(full.providers):
                raise ModelNotFoundException(f"Model '{model_id}' not found")
            data = {**data, "model": strip_model_media(data["model"])}
        return self.success_response(data=data)

    @guarded(
        {**_NOT_FOUND, ModelAccessDeniedException: Fail("access_denied")},
        Fail("get_generations_failed", "Failed to get model generations", detail=failure_detail),
        admin="user",
    )
    async def get_model_generations(
        self,
        model_id: str,
        user: User,
        limit: int = 20,
        offset: int = 0
    ) -> APIResponse:
        data = await operations.get_model_generations(self.collaborators, model_id, user, limit, offset)
        if self._restricted(user):
            viewable = self.content_safety.viewable_generation_ids(
                user.id, [g["id"] for g in data["generations"]]
            )
            kept = [g for g in data["generations"] if g["id"] in viewable]
            data = {**data, "generations": kept, "total": max(0, data["total"] - (len(data["generations"]) - len(kept)))}
        return self.success_response(data=data)

    # --- Indexing Endpoints ---

    @guarded(fallback=Fail("count_unindexed_failed", "Failed to count unindexed models", detail=failure_detail))
    async def count_unindexed_models(self) -> APIResponse:
        data = await operations.count_unindexed(self.collaborators)
        return self.success_response(data=data)

    @guarded(fallback=Fail("get_indexing_status_failed", "Failed to get indexing status", detail=failure_detail))
    async def get_indexing_status(self) -> APIResponse:
        data = await operations.get_indexing_status(self.collaborators)
        return self.success_response(data=data)

    @guarded(
        {ModelIndexingException: Fail("indexing_blocked")},
        Fail("indexing_failed", "Failed to start indexing", detail=failure_detail),
    )
    async def index_models(self) -> APIResponse:
        data = operations.rescan_indexing(self.collaborators, trigger="manual")
        return self.success_response(data=data)

    @guarded(fallback=Fail("indexing_settings_failed", "Failed to update indexing settings", detail=failure_detail))
    async def update_indexing_settings(self, request: IndexingSettingsRequest) -> APIResponse:
        data = await asyncio.to_thread(
            operations.update_indexing_settings, self.collaborators, request.level, request.policy
        )
        return self.success_response(data=data)

    @guarded(fallback=Fail("indexing_pause_failed", "Failed to pause indexing", detail=failure_detail))
    async def pause_indexing(self) -> APIResponse:
        data = await asyncio.to_thread(operations.pause_indexing, self.collaborators)
        return self.success_response(data=data)

    @guarded(fallback=Fail("indexing_resume_failed", "Failed to resume indexing", detail=failure_detail))
    async def resume_indexing(self) -> APIResponse:
        data = await asyncio.to_thread(operations.resume_indexing, self.collaborators)
        return self.success_response(data=data)

    @guarded(fallback=Fail("pending_digests_failed", "Failed to list the files waiting to be indexed", detail=failure_detail))
    async def list_pending_digests(
        self, query: str, model_type: Optional[str], root_id: Optional[str], offset: int, limit: int
    ) -> APIResponse:
        data = await asyncio.to_thread(
            operations.list_pending_digests, self.collaborators, query, model_type, root_id, offset, limit
        )
        return self.success_response(data=data)

    @guarded(fallback=Fail("queue_digests_failed", "Failed to queue the files for indexing", detail=failure_detail))
    async def queue_digests(self, request: IndexingDigestsRequest) -> APIResponse:
        data = await asyncio.to_thread(
            operations.queue_digests, self.collaborators, request.mode, request.model_ids, request.query,
            request.model_type, request.exclude_model_ids,
        )
        return self.success_response(data=data)

    @guarded(
        {
            ModelNotFoundException: Fail("model_not_found", status=404),
            ModelDigestUnavailableException: Fail("model_digest_unavailable", status=422),
        },
        Fail("model_digest_failed", "Failed to request the content hash", detail=failure_detail),
    )
    async def request_model_digest(self, model_id: str, retry: bool = False) -> APIResponse:
        data = await asyncio.to_thread(operations.request_model_digest, self.collaborators, model_id, retry)
        return self.success_response(data=data)

    @guarded(
        {**_NOT_FOUND, ModelIndexingException: Fail("delete_blocked")},
        Fail("delete_failed", "Failed to delete model", detail=failure_detail),
    )
    async def delete_model(self, model_id: str) -> APIResponse:
        data = operations.delete_model(self.collaborators, model_id)
        return self.success_response(data=data)

    @guarded(fallback=Fail("cleanup_failed", "Failed to cleanup models", detail=failure_detail))
    async def cleanup_deleted_models(self) -> APIResponse:
        data = operations.cleanup_deleted_models(self.collaborators)
        return self.success_response(data=data)

    # --- Provider Endpoints ---

    @guarded(
        {ProviderFetchException: Fail("provider_error")},
        Fail("provider_fetch_failed", "Failed to start provider fetch", detail=failure_detail),
    )
    async def fetch_model_info(
        self,
        background_tasks: BackgroundTasks,
        request: ModelInfoFetchRequest
    ) -> APIResponse:
        data = operations.fetch_provider_info(self.collaborators, 
            provider=request.provider,
            model_ids=request.model_ids,
            force_refresh=request.force_refresh or False
        )

        if request.wait and request.model_ids:
            result = await operations.run_provider_fetch(
                self.collaborators,
                provider=request.provider,
                model_ids=request.model_ids,
                force_refresh=request.force_refresh or False,
            )
            completed_data = {**data, "status": "completed", **result}
            completed_data["message"] = f"{request.provider.title()} info fetch completed"
            return self.success_response(data=completed_data)

        background_tasks.add_task(
            operations.run_provider_fetch,
            self.collaborators,
            provider=request.provider,
            model_ids=request.model_ids,
            force_refresh=request.force_refresh or False,
        )
        return self.success_response(data=data)

    @guarded(
        {**_NOT_FOUND, InvalidTagException: Fail("invalid_tag")},
        Fail("update_tags_failed", "Failed to update tags", detail=failure_detail),
    )
    async def update_model_tags(
        self,
        model_id: str,
        request: UpdateTagsRequest
    ) -> APIResponse:
        data = operations.update_model_tags(self.collaborators, model_id, request.tag_ids)
        return self.success_response(data=data)

    @guarded(_NOT_FOUND, Fail("update_description_failed", "Failed to update description", detail=failure_detail))
    async def update_model_description(
        self,
        model_id: str,
        request: UpdateDescriptionRequest
    ) -> APIResponse:
        data = operations.update_model_description(self.collaborators, model_id, request.description)
        return self.success_response(data=data)

    @guarded(
        {
            ModelNotFoundException: Fail("model_not_found", status=404),
            (InvalidModelTypeException, ModelTypeNotAssignableException): Fail("model_type_not_assignable", status=422),
            ModelTypeConflictException: Fail("model_type_conflict", status=409),
        },
    )
    async def set_model_type(self, model_id: str, request: SetModelTypeRequest, user: User) -> APIResponse:
        data = await asyncio.to_thread(
            operations.set_model_type, self.collaborators, model_id, request.model_type, user.id
        )
        return self.success_response(data=data)

    @guarded(
        {
            ModelNotFoundException: Fail("model_not_found", status=404),
            ModelTypeNotAssignableException: Fail("model_type_not_assignable", status=422),
            ModelTypeConflictException: Fail("model_type_conflict", status=409),
        },
    )
    async def reset_model_type(self, model_id: str) -> APIResponse:
        data = await asyncio.to_thread(operations.reset_model_type, self.collaborators, model_id)
        return self.success_response(data=data)

    @guarded(_NOT_FOUND, Fail("update_prompting_guidance_failed", "Failed to update prompting guidance", detail=failure_detail))
    async def update_model_prompting_guidance(
        self,
        model_id: str,
        request: UpdatePromptingGuidanceRequest
    ) -> APIResponse:
        data = operations.update_model_prompting_guidance(self.collaborators, model_id, request.prompting_guidance)
        return self.success_response(data=data)

    @guarded(
        {**_NOT_FOUND, InvalidModelMetadataException: Fail("invalid_model_metadata")},
        Fail("update_metadata_failed", "Failed to update metadata", detail=failure_detail),
    )
    async def update_model_metadata(
        self,
        model_id: str,
        request: UpdateModelMetadataRequest
    ) -> APIResponse:
        data = operations.update_model_metadata(self.collaborators, model_id, request.values)
        return self.success_response(data=data)

    @guarded(
        {InvalidModelMetadataException: Fail("invalid_model_metadata")},
        Fail("update_user_attributes_failed", "Failed to update user attributes", detail=failure_detail),
        admin="user",
    )
    async def update_model_user_attributes(
        self,
        model_id: str,
        request: UpdateModelUserAttributesRequest,
        user: User,
    ) -> APIResponse:
        values = self.attributes_manager.update_user_values(model_id, user.id, request.values)
        self.collaborators.metadata.notify_metadata_changed(model_id, ["attributes"], user.id)
        return self.success_response(data={"values": values})

    # --- Attribute definitions (admin-managed, GET is open to any active user) ---

    @guarded(fallback=Fail("list_attributes_failed", "Failed to list attribute definitions", detail=failure_detail), admin="user")
    async def list_attribute_definitions(self, user: User) -> APIResponse:
        definitions = self.attribute_definitions.list_all()
        is_admin = user.account_type == AccountType.ADMIN
        visible = [d for d in definitions if is_admin or not d.admin_only]
        return self.success_response(data={"definitions": [d.to_dict() for d in visible]})

    @guarded(
        {InvalidAttributeDefinitionException: Fail("invalid_attribute_definition")},
        Fail("create_attribute_failed", "Failed to create attribute definition", detail=failure_detail),
    )
    async def create_attribute_definition(self, request: CreateAttributeDefinitionRequest) -> APIResponse:
        definition = self.attributes_manager.create(request.model_dump())
        return self.success_response(data={"definition": definition.to_dict()})

    @guarded(
        {
            AttributeDefinitionNotFoundException: Fail("attribute_not_found"),
            (InvalidAttributeDefinitionException, SystemAttributeDefinitionException): Fail("invalid_attribute_definition"),
        },
        Fail("update_attribute_failed", "Failed to update attribute definition", detail=failure_detail),
    )
    async def update_attribute_definition(
        self, definition_id: str, request: UpdateAttributeDefinitionRequest
    ) -> APIResponse:
        definition = self.attributes_manager.update(definition_id, request.model_dump(exclude_unset=True))
        return self.success_response(data={"definition": definition.to_dict()})

    @guarded(
        {
            AttributeDefinitionNotFoundException: Fail("attribute_not_found"),
            SystemAttributeDefinitionException: Fail("system_attribute_definition"),
        },
        Fail("delete_attribute_failed", "Failed to delete attribute definition", detail=failure_detail),
    )
    async def delete_attribute_definition(self, definition_id: str) -> APIResponse:
        self.attributes_manager.delete(definition_id)
        return self.success_response(data={"message": "Attribute definition deleted"})

    @guarded(
        {**_NOT_FOUND, ModelIndexingException: Fail("invalid_preview")},
        Fail("update_preview_failed", "Failed to update preview", detail=failure_detail),
    )
    async def update_model_preview(
        self,
        model_id: str,
        request: UpdateModelPreviewRequest,
        user_id: Optional[str] = None,
    ) -> APIResponse:
        preview = request.preview.model_dump() if request.preview else None
        data = operations.update_model_preview(self.collaborators, model_id, preview, user_id)
        return self.success_response(data=data)

    @guarded(_NOT_FOUND, Fail("list_previews_failed", "Failed to list previews", detail=failure_detail), admin="user")
    async def list_model_previews(self, model_id: str, user: User) -> APIResponse:
        previews = operations.list_model_previews_for_user(self.collaborators, model_id, user)
        if self._restricted(user):
            previews = []
        return self.success_response(data={"previews": previews})

    @guarded(
        {**_NOT_FOUND, ModelIndexingException: Fail("invalid_preview")},
        Fail("add_preview_failed", "Failed to add preview", detail=failure_detail),
    )
    async def add_model_preview(
        self,
        model_id: str,
        request: AddModelPreviewRequest,
        user_id: Optional[str] = None,
    ) -> APIResponse:
        data = operations.add_model_preview(self.collaborators, model_id, request.preview.model_dump(), user_id)
        return self.success_response(data=data)

    @guarded(_NOT_FOUND, Fail("delete_preview_failed", "Failed to delete preview", detail=failure_detail))
    async def delete_model_preview(self, model_id: str, preview_id: str) -> APIResponse:
        data = operations.delete_model_preview(self.collaborators, model_id, preview_id)
        return self.success_response(data=data)

    @guarded(
        {**_NOT_FOUND, ModelIndexingException: Fail("invalid_reorder")},
        Fail("reorder_previews_failed", "Failed to reorder previews", detail=failure_detail),
    )
    async def reorder_model_previews(
        self, model_id: str, request: ReorderModelPreviewsRequest
    ) -> APIResponse:
        data = operations.reorder_model_previews(self.collaborators, model_id, request.ordered_ids)
        return self.success_response(data=data)

    # --- Library Endpoints ---

    @guarded(
        {ValueError: Fail("set_favorite_failed")},
        Fail("set_favorite_failed", "Failed to update favorite", detail=failure_detail),
        admin="user",
    )
    async def set_model_favorite(
        self,
        model_id: str,
        request: ModelFavoriteRequest,
        user: User
    ) -> APIResponse:
        meta = self.user_model_meta_repository.set_favorite(user.id, model_id, request.is_favorite)
        return self.success_response(data={
            "message": "Model favorite updated successfully",
            "meta": meta.to_dict()
        })

    @guarded(
        {ValueError: Fail("set_library_name_failed")},
        Fail("set_library_name_failed", "Failed to update library name", detail=failure_detail),
        admin="user",
    )
    async def set_model_library_name(
        self,
        model_id: str,
        request: ModelLibraryNameRequest,
        user: User
    ) -> APIResponse:
        name = request.name.strip() or None if request.name is not None else None
        meta = self.user_model_meta_repository.set_custom_name(user.id, model_id, name)
        self.collaborators.metadata.notify_metadata_changed(model_id, ["name"], user.id)
        return self.success_response(data={
            "message": "Model library name updated successfully",
            "meta": meta.to_dict()
        })

    # --- Thumbnail Endpoints ---

    @guarded(fallback=Fail("thumbnail_generation_failed", "Failed to start thumbnail generation", detail=failure_detail))
    async def generate_video_thumbnails(
        self,
        background_tasks: BackgroundTasks,
        model_ids: Optional[List[str]] = None
    ) -> APIResponse:
        data = operations.start_thumbnail_generation(self.collaborators, model_ids)

        async def run_task():
            await operations.run_thumbnail_generation(self.collaborators, model_ids)

        background_tasks.add_task(asyncio.run, run_task())
        return self.success_response(data=data)

    # --- User Assignment Endpoints ---

    @guarded(fallback=Fail("get_assignments_failed", "Failed to get user model assignments", detail=failure_detail))
    async def get_user_model_assignments(self, user_id: str) -> APIResponse:
        data = operations.get_user_model_assignments(self.collaborators, user_id)
        return self.success_response(data=data)

    @guarded(fallback=Fail("get_assignments_failed", "Failed to get model assignments", detail=failure_detail))
    async def get_model_assignments(self, model_id: str) -> APIResponse:
        data = operations.get_model_assignments(self.collaborators, model_id)
        return self.success_response(data=data)

    @guarded(fallback=Fail("get_assignment_summary_failed", "Failed to get model assignment summary", detail=failure_detail))
    async def get_model_assignment_summary(self) -> APIResponse:
        data = operations.get_model_assignment_summary(self.collaborators)
        return self.success_response(data=data)

    @guarded(
        {ModelAssignmentException: Fail("assignment_failed")},
        Fail("assignment_failed", "Failed to assign model to user", detail=failure_detail),
    )
    async def assign_model_to_user(
        self,
        request: UserModelAssignmentRequest
    ) -> APIResponse:
        data = operations.assign_model_to_user(self.collaborators, request.model_id, request.user_id)
        return self.success_response(data=data)

    @guarded(
        {ModelAssignmentException: Fail("unassignment_failed")},
        Fail("unassignment_failed", "Failed to unassign model from user", detail=failure_detail),
    )
    async def unassign_model_from_user(
        self,
        user_id: str,
        model_id: str
    ) -> APIResponse:
        data = operations.unassign_model_from_user(self.collaborators, model_id, user_id)
        return self.success_response(data=data)

    # --- Download Endpoints ---

    @guarded(
        {ModelDownloadException: Fail("download_blocked")},
        Fail("download_failed", "Failed to start download", detail=failure_detail),
    )
    async def download_and_index_model(
        self,
        background_tasks: BackgroundTasks,
        request: DownloadModelRequest
    ) -> APIResponse:
        data = operations.start_download_and_index(self.collaborators, 
            name=request.name,
            link=request.link,
            size=request.size,
            sha256=request.sha256,
            model_type=request.model_type or 'checkpoint'
        )

        async def run_task():
            await operations.run_download_and_index(self.collaborators, 
                name=request.name,
                link=request.link,
                sha256=request.sha256,
                model_type=request.model_type or 'checkpoint'
            )

        background_tasks.add_task(asyncio.run, run_task())
        return self.success_response(data=data)

    # --- Recommendation Downloads (v2, provider-gated - see docs/presets.md) ---

    @staticmethod
    def _parse_ref(ref: Optional[str]):
        return parse_ref(ref)

    @staticmethod
    def _recommendation_filename(name: str) -> Optional[str]:
        if Path(name).suffix.lower() not in RECOMMENDATION_EXTENSIONS:
            return None
        return derived_download_name(name)

    @guarded(fallback=Fail("download_failed", "Failed to queue download", detail=failure_detail), admin="current_user")
    async def queue_recommendation_download(
        self,
        request: RecommendationDownloadRequest,
        current_user: User
    ) -> APIResponse:
        if current_user.account_type != AccountType.ADMIN:
            return self.error_api_response(
                error="permission_denied", message="Admin access required"
            )

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

        download = await self.download_queue.queue_model_download(
            url=url,
            filename=self._recommendation_filename(request.name),
            model_type=request.model_type,
            checksum_sha256=request.sha256,
            provider_id=request.provider,
            created_by=current_user.id,
        )

        return self.success_response(data={"download_id": download.id})

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

    @guarded(
        {DownloadNotFoundException: Fail("download_not_found")},
        Fail("download_status_failed", "Failed to get download status", detail=failure_detail),
    )
    async def get_recommendation_download(self, download_id: str) -> APIResponse:
        download = await asyncio.to_thread(self.download_queue.get_download, download_id)
        status = self._STATUS_MAP.get(download.status.value, download.status.value)
        return self.success_response(data={
            "status": status,
            "progress": download.progress if status != "pending" else None,
            "error": download.error_message,
            "gated": download.gated,
            "gated_repo": download.gated_repo,
        })


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
        include_descendants: bool = Query(True, description="With collection_id: also include items of its sub-collections"),
        unsorted: bool = Query(False, description="Only models in none of the current user's collections"),
        q_mode: Optional[str] = Query(None, description="How `search` matches: 'substring' (default) or 'regex'"),
        indexed_from: Optional[str] = Query(None, description="Indexed on or after this day (YYYY-MM-DD, UTC)"),
        indexed_to: Optional[str] = Query(None, description="Indexed on or before this day (YYYY-MM-DD, UTC)"),
        used: Optional[str] = Query(None, description="Usage filter: 'any', 'used' or 'never'"),
        min_uses: Optional[int] = Query(None, description="Only models used in at least this many generations"),
        last_used_from: Optional[str] = Query(None, description="Last used on or after this day (YYYY-MM-DD, UTC)"),
        last_used_to: Optional[str] = Query(None, description="Last used on or before this day (YYYY-MM-DD, UTC)"),
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
            in_any_collection=in_any_collection,
            include_descendants=include_descendants,
            unsorted=unsorted,
            q_mode=q_mode,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
            used=used,
            min_uses=min_uses,
            last_used_from=last_used_from,
            last_used_to=last_used_to,
        )


    @router.get("/stats", response_model=APIResponse, summary="Get Model Stats")
    async def get_model_stats(current_user: User = Depends(get_current_active_user)):
        """Get model indexing statistics."""
        return await controller.get_model_stats()


    @router.get("/types", response_model=APIResponse, summary="Get Model Types")
    async def get_model_types(
        user_scoped: bool = Query(False, description="When true, counts only models assigned to the current user"),
        include_empty: bool = Query(False, description="When true (admin, non-user_scoped only), also include known model types with zero indexed models"),
        model_type: Optional[str] = Query(None, description="Narrows `tag_counts` only; type counts always ignore it"),
        tag_ids: Optional[str] = Query(None, description="Comma-separated tag IDs every counted model must carry"),
        search: Optional[str] = Query(None, description="Same as the list endpoint's `search`"),
        assignment_filter: Optional[str] = Query(None, description="Filter by assignment: 'assigned' or 'unassigned'"),
        assigned_user_id: Optional[str] = Query(None, description="User ID for assignment filtering"),
        assigned_group_id: Optional[str] = Query(None, description="Group ID for assignment filtering"),
        favorites_only: bool = Query(False, description="Only count models favorited by the current user"),
        collection_id: Optional[str] = Query(None, description="Only count models in this model collection"),
        in_any_collection: bool = Query(False, description="Only count models in any of the current user's collections"),
        include_descendants: bool = Query(True, description="With collection_id: also include items of its sub-collections"),
        unsorted: bool = Query(False, description="Only models in none of the current user's collections"),
        q_mode: Optional[str] = Query(None, description="How `search` matches: 'substring' (default) or 'regex'"),
        indexed_from: Optional[str] = Query(None, description="Indexed on or after this day (YYYY-MM-DD, UTC)"),
        indexed_to: Optional[str] = Query(None, description="Indexed on or before this day (YYYY-MM-DD, UTC)"),
        used: Optional[str] = Query(None, description="Usage filter: 'any', 'used' or 'never'"),
        min_uses: Optional[int] = Query(None, description="Only models used in at least this many generations"),
        last_used_from: Optional[str] = Query(None, description="Last used on or after this day (YYYY-MM-DD, UTC)"),
        last_used_to: Optional[str] = Query(None, description="Last used on or before this day (YYYY-MM-DD, UTC)"),
        include_tag_counts: bool = Query(False, description="Also return `tag_counts`: matching models per tag id"),
        current_user: User = Depends(get_current_active_user)
    ):
        """Get available model types and their counts."""
        return await controller.get_model_types(
            current_user,
            user_scoped,
            include_empty,
            model_type=model_type,
            tag_ids=tag_ids,
            search=search,
            assignment_filter=assignment_filter,
            assigned_user_id=assigned_user_id,
            assigned_group_id=assigned_group_id,
            favorites_only=favorites_only,
            collection_id=collection_id,
            in_any_collection=in_any_collection,
            include_descendants=include_descendants,
            unsorted=unsorted,
            q_mode=q_mode,
            indexed_from=indexed_from,
            indexed_to=indexed_to,
            used=used,
            min_uses=min_uses,
            last_used_from=last_used_from,
            last_used_to=last_used_to,
            include_tag_counts=include_tag_counts,
        )


    @router.get("/unindexed-count", response_model=APIResponse, summary="Count Unindexed Models")
    async def count_unindexed_models(current_user: User = Depends(get_current_admin_user)):
        """Count model files on disk not yet indexed, by type - no hashing, no writes."""
        return await controller.count_unindexed_models()


    @router.get("/indexing/status", response_model=APIResponse, summary="Get Indexing Status")
    async def get_indexing_status(current_user: User = Depends(get_current_admin_user)):
        return await controller.get_indexing_status()


    @router.put("/indexing/settings", response_model=APIResponse, summary="Update Indexing Settings")
    async def update_indexing_settings(
        request: IndexingSettingsRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.update_indexing_settings(request)


    @router.post("/indexing/pause", response_model=APIResponse, summary="Pause Background Indexing")
    async def pause_indexing(current_user: User = Depends(get_current_admin_user)):
        return await controller.pause_indexing()


    @router.post("/indexing/resume", response_model=APIResponse, summary="Resume Background Indexing")
    async def resume_indexing(current_user: User = Depends(get_current_admin_user)):
        return await controller.resume_indexing()


    @router.get("/indexing/pending", response_model=APIResponse, summary="List Files Waiting To Be Indexed")
    async def list_pending_digests(
        query: str = Query("", description="Case-insensitive filename substring"),
        model_type: Optional[str] = Query(None, description="Only files of this model type"),
        root_id: Optional[str] = Query(None, description="Only files under this model root"),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.list_pending_digests(query, model_type, root_id, offset, limit)


    @router.post("/indexing/digests", response_model=APIResponse, summary="Queue Files For Indexing")
    async def queue_digests(
        request: IndexingDigestsRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.queue_digests(request)


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
        current_user: User = Depends(get_current_admin_user)
    ):
        """Start model indexing process."""
        return await controller.index_models()


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


    @router.post("/{model_id}/digest", response_model=APIResponse, summary="Request A Model's Content Hash")
    async def request_model_digest(
        model_id: str,
        retry: bool = Query(False, description="Try again a file that failed to be read"),
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.request_model_digest(model_id, retry)


    @router.put("/{model_id}/type", response_model=APIResponse, summary="Set Model Type")
    async def set_model_type(
        model_id: str,
        request: SetModelTypeRequest,
        current_user: User = Depends(get_current_admin_user)
    ):
        return await controller.set_model_type(model_id, request, current_user)


    @router.delete("/{model_id}/type", response_model=APIResponse, summary="Reset Model Type to Automatic")
    async def reset_model_type(
        model_id: str,
        current_user: User = Depends(get_current_admin_user)
    ):
        return await controller.reset_model_type(model_id)


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
