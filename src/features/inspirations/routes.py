"""Inspirations Controller

Cross-user publishing of generations: a snapshot of chosen output files plus
their generating params, with comments, saves, per-user collections, and a
save-to-library reuse path.

House rule followed here: GET handlers read straight from
`collaborators.repository`; only mutations go through
`src.features.inspirations.operations`.
"""

import asyncio
import logging
import mimetypes
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from src.platform.http.errors import NotFoundError
from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.api_scopes import ApiScope, key_scope
from src.platform.security.current_user import get_current_active_user, require_media_viewer
from src.platform.security.user import AccountType

from src.features.inspirations.collaborators import InspirationCollaborators
from src.features.inspirations.dto import (
    PublishInspirationRequest,
    CommentCreateRequest,
    CreateInspirationCollectionRequest,
    UpdateInspirationCollectionRequest,
    InspirationCollectionItemRequest,
    inspiration_to_dto,
    comment_to_dto,
    collection_to_dto,
)
from src.features.inspirations import operations
from src.features.inspirations.storage import inspiration_media_key

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class InspirationController(BaseController):
    """Controller for the Inspirations module."""

    def __init__(self, collaborators: InspirationCollaborators):
        super().__init__()
        self.collaborators = collaborators

    def _viewable(self, viewer_id: str, inspirations: list) -> list:
        content_safety = self.collaborators.content_safety
        if content_safety is None or not content_safety.is_restricted(viewer_id):
            return inspirations
        allowed = content_safety.viewable_generation_ids(
            viewer_id, [i.source_generation_id for i in inspirations]
        )
        return [i for i in inspirations if i.source_generation_id in allowed]

    # ========== Feed ==========

    @guarded(fallback=Fail("list_failed", "Failed to list inspirations", 400))
    async def list_feed(
        self,
        current_user,
        query: Optional[str],
        limit: int,
        offset: int,
        collection_id: Optional[str],
        author_id: Optional[str],
        saved: Optional[bool],
        include_descendants: bool = True,
        unsorted: bool = False,
    ) -> APIResponse:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        items, total = self.collaborators.repository.list_feed(
            viewer_id=current_user.id,
            query=query,
            limit=limit,
            offset=offset,
            collection_id=collection_id,
            author_id=author_id,
            saved=saved,
            include_descendants=include_descendants,
            unsorted=unsorted,
        )
        visible = self._viewable(current_user.id, items)
        return self.success_response(data={
            "items": [inspiration_to_dto(i) for i in visible],
            "total": max(0, total - (len(items) - len(visible))),
        })

    @guarded(fallback=Fail("get_failed", "Failed to get inspiration", 400))
    async def get_inspiration(self, inspiration_id: str, current_user) -> APIResponse:
        insp = self.collaborators.repository.get_by_id(inspiration_id, viewer_id=current_user.id)
        if not insp or not self._viewable(current_user.id, [insp]):
            raise NotFoundError("not_found", "Inspiration not found")
        return self.success_response(data={"inspiration": inspiration_to_dto(insp)})

    @guarded(fallback=Fail("get_params_failed", "Failed to get inspiration params", 400))
    async def get_params(self, inspiration_id: str, current_user) -> APIResponse:
        insp = self.collaborators.repository.get_by_id(inspiration_id)
        if not insp or not self._viewable(current_user.id, [insp]):
            raise NotFoundError("not_found", "Inspiration not found")
        return self.success_response(data={
            "form_data": insp.params_snapshot.get("form_data", {}),
            "preset_id": insp.preset_id,
            "preset_name": insp.preset_name,
            "mode": insp.params_snapshot.get("mode"),
            "omitted_fields": insp.params_snapshot.get("omitted_fields", []),
        })

    # ========== Publish / delete ==========

    @guarded(fallback=Fail("publish_failed", "Failed to publish inspiration", 400))
    async def publish(self, request: PublishInspirationRequest, current_user) -> APIResponse:
        insp = operations.publish(
            self.collaborators,
            current_user.id,
            request.generation_id,
            request.filenames,
            request.title,
            request.description,
        )
        return self.success_response(data={"inspiration": inspiration_to_dto(insp)})

    @guarded(fallback=Fail("delete_failed", "Failed to delete inspiration", 400))
    async def delete_inspiration(self, inspiration_id: str, current_user) -> APIResponse:
        operations.delete(
            self.collaborators, inspiration_id, current_user.id,
            is_admin=(current_user.account_type == AccountType.ADMIN)
        )
        return self.success_response(data={"id": inspiration_id, "deleted": True})

    # ========== Saves ==========

    @guarded(fallback=Fail("save_failed", "Failed to save inspiration to library", 400))
    async def save_to_library(self, inspiration_id: str, current_user) -> APIResponse:
        save_count = await asyncio.to_thread(
            operations.save_to_library, self.collaborators, inspiration_id, current_user.id
        )
        return self.success_response(data={"saved": True, "save_count": save_count})

    @guarded(fallback=Fail("unsave_failed", "Failed to unsave inspiration", 400))
    async def unsave(self, inspiration_id: str, current_user) -> APIResponse:
        save_count = operations.unsave(self.collaborators, inspiration_id, current_user.id)
        return self.success_response(data={"saved": False, "save_count": save_count})

    # ========== Comments ==========

    @guarded(fallback=Fail("list_comments_failed", "Failed to list comments", 400))
    async def list_comments(self, inspiration_id: str, current_user) -> APIResponse:
        insp = self.collaborators.repository.get_by_id(inspiration_id)
        if not insp:
            raise NotFoundError("not_found", "Inspiration not found")
        comments = self.collaborators.repository.list_comments(inspiration_id)
        return self.success_response(data={"items": [comment_to_dto(c) for c in comments]})

    @guarded(fallback=Fail("comment_failed", "Failed to add comment", 400))
    async def add_comment(self, inspiration_id: str, request: CommentCreateRequest, current_user) -> APIResponse:
        comment = operations.add_comment(self.collaborators, inspiration_id, current_user.id, request.body)
        return self.success_response(data={"comment": comment_to_dto(comment)})

    @guarded(fallback=Fail("delete_failed", "Failed to delete comment", 400))
    async def delete_comment(self, comment_id: str, current_user) -> APIResponse:
        operations.delete_comment(
            self.collaborators, comment_id, current_user.id,
            is_admin=(current_user.account_type == AccountType.ADMIN)
        )
        return self.success_response(data={"id": comment_id, "deleted": True})

    # ========== Collections ==========

    @guarded(fallback=Fail("list_collections_failed", "Failed to list collections", 400))
    async def list_collections(self, current_user, include_descendants: bool = True) -> APIResponse:
        repository = self.collaborators.repository
        collections = repository.list_collections(current_user.id, include_descendants)
        return self.success_response(data={
            "items": [collection_to_dto(c) for c in collections],
            "smart_counts": repository.smart_counts(current_user.id),
        })

    @guarded(fallback=Fail("create_collection_failed", "Failed to create collection", 400))
    async def create_collection(self, request: CreateInspirationCollectionRequest, current_user) -> APIResponse:
        collection = operations.create_collection(self.collaborators, current_user.id, request.name, request.parent_id)
        return self.success_response(data={"collection": collection_to_dto(collection)})

    @guarded(fallback=Fail("update_collection_failed", "Failed to update collection", 400))
    async def update_collection(
        self, collection_id: str, request: UpdateInspirationCollectionRequest, current_user
    ) -> APIResponse:
        parent_id_set = "parent_id" in request.model_fields_set
        collection = operations.update_collection(
            self.collaborators,
            collection_id,
            current_user.id,
            name=request.name,
            parent_id=request.parent_id,
            parent_id_set=parent_id_set,
        )
        return self.success_response(data={"collection": collection_to_dto(collection)})

    @guarded(fallback=Fail("delete_collection_failed", "Failed to delete collection", 400))
    async def delete_collection(self, collection_id: str, current_user) -> APIResponse:
        operations.delete_collection(self.collaborators, collection_id, current_user.id)
        return self.success_response(data={"id": collection_id, "deleted": True})

    @guarded(fallback=Fail("add_item_failed", "Failed to add item to collection", 400))
    async def add_collection_item(
        self, collection_id: str, request: InspirationCollectionItemRequest, current_user
    ) -> APIResponse:
        operations.add_item(self.collaborators, collection_id, current_user.id, request.inspiration_id)
        return self.success_response(data={"added": True})

    @guarded(fallback=Fail("remove_item_failed", "Failed to remove item from collection", 400))
    async def remove_collection_item(self, collection_id: str, inspiration_id: str, current_user) -> APIResponse:
        operations.remove_item(self.collaborators, collection_id, current_user.id, inspiration_id)
        return self.success_response(data={"removed": True})

    # ========== Media serving ==========

    async def serve_media(self, inspiration_id: str, filename: str, viewer):
        try:
            insp = self.collaborators.repository.get_by_id(inspiration_id)
            if not insp or not any(entry.get("filename") == filename for entry in insp.media):
                raise ValueError("File not found")
            if not self._viewable(viewer.id, [insp]):
                raise ValueError("File not found")

            key = inspiration_media_key(inspiration_id, filename)
            if not self.collaborators.storage_driver.exists(key):
                raise ValueError("File not found")

            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            local_path = self.collaborators.storage_driver.local_path(key)
            if local_path is not None:
                return FileResponse(path=str(local_path), media_type=media_type, filename=filename)
            return Response(content=self.collaborators.storage_driver.get_bytes(key), media_type=media_type)
        except ValueError:
            raise HTTPException(status_code=404, detail="Media not found")
        except Exception as e:
            logger.error(f"Error serving inspiration media: {e}")
            raise HTTPException(status_code=500, detail="Failed to serve media")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.inspiration_controller
    router = APIRouter(prefix="/api/inspirations", tags=["Inspirations"])

    @router.get("", response_model=APIResponse, summary="List Inspirations Feed")
    async def list_feed(
        query: Optional[str] = Query(None),
        limit: int = Query(50),
        offset: int = Query(0),
        collection_id: Optional[str] = Query(None),
        author_id: Optional[str] = Query(None),
        saved: Optional[bool] = Query(None),
        include_descendants: bool = Query(True),
        unsorted: bool = Query(False),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_feed(
            current_user, query, limit, offset, collection_id, author_id, saved,
            include_descendants, unsorted,
        )

    @router.post("", response_model=APIResponse, summary="Publish Inspiration")
    async def publish(
        request: PublishInspirationRequest,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.publish(request, current_user)

    @router.get("/collections", response_model=APIResponse, summary="List My Inspiration Collections")
    async def list_collections(
        include_descendants: bool = Query(True),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_collections(current_user, include_descendants)

    @router.post("/collections", response_model=APIResponse, summary="Create Inspiration Collection")
    async def create_collection(
        request: CreateInspirationCollectionRequest,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.create_collection(request, current_user)

    @router.put("/collections/{collection_id}", response_model=APIResponse, summary="Update Inspiration Collection")
    async def update_collection(
        collection_id: str,
        request: UpdateInspirationCollectionRequest,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.update_collection(collection_id, request, current_user)

    @router.delete("/collections/{collection_id}", response_model=APIResponse, summary="Delete Inspiration Collection")
    async def delete_collection(
        collection_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.delete_collection(collection_id, current_user)

    @router.post(
        "/collections/{collection_id}/items", response_model=APIResponse, summary="Add Inspiration To Collection"
    )
    async def add_collection_item(
        collection_id: str,
        request: InspirationCollectionItemRequest,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.add_collection_item(collection_id, request, current_user)

    @router.delete(
        "/collections/{collection_id}/items/{inspiration_id}",
        response_model=APIResponse,
        summary="Remove Inspiration From Collection",
    )
    async def remove_collection_item(
        collection_id: str,
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.remove_collection_item(collection_id, inspiration_id, current_user)

    @router.get("/{inspiration_id}", response_model=APIResponse, summary="Get Inspiration")
    async def get_inspiration(
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.get_inspiration(inspiration_id, current_user)

    @router.delete("/{inspiration_id}", response_model=APIResponse, summary="Delete Inspiration")
    async def delete_inspiration(
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.delete_inspiration(inspiration_id, current_user)

    @router.get("/{inspiration_id}/params", response_model=APIResponse, summary="Get Inspiration Params Snapshot")
    async def get_params(
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.get_params(inspiration_id, current_user)

    @router.post("/{inspiration_id}/save-to-library", response_model=APIResponse, summary="Save Inspiration To Library")
    async def save_to_library(
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.save_to_library(inspiration_id, current_user)

    @router.delete("/{inspiration_id}/save", response_model=APIResponse, summary="Unsave Inspiration")
    async def unsave(
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.unsave(inspiration_id, current_user)

    @router.get("/{inspiration_id}/comments", response_model=APIResponse, summary="List Inspiration Comments")
    async def list_comments(
        inspiration_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_comments(inspiration_id, current_user)

    @router.post("/{inspiration_id}/comments", response_model=APIResponse, summary="Add Inspiration Comment")
    async def add_comment(
        inspiration_id: str,
        request: CommentCreateRequest,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.add_comment(inspiration_id, request, current_user)

    @router.delete(
        "/{inspiration_id}/comments/{comment_id}", response_model=APIResponse, summary="Delete Inspiration Comment"
    )
    async def delete_comment(
        inspiration_id: str,
        comment_id: str,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.delete_comment(comment_id, current_user)

    return router


def build_media_router(container: "AppContainer") -> APIRouter:
    controller = container.inspiration_controller
    router = APIRouter(prefix="/api/media/inspirations", tags=["Inspirations"])

    @router.get("/{inspiration_id}/{filename}", summary="Serve Inspiration Media", dependencies=[key_scope({ApiScope.READ, ApiScope.MCP})])
    async def serve_media(inspiration_id: str, filename: str, viewer=Depends(require_media_viewer)):
        return await controller.serve_media(inspiration_id, filename, viewer)

    return router
