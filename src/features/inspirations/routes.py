"""Inspirations Controller

Cross-user publishing of generations: a snapshot of chosen output files plus
their generating params, with comments, saves, per-user collections, and a
save-to-library reuse path.

House rule followed here: GET handlers read straight from
`InspirationRepository`; only mutations go through `InspirationManager`.
"""

import logging
import mimetypes
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType

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
from src.features.inspirations.manager import InspirationManager
from src.features.inspirations.repository import InspirationRepository
from src.features.inspirations.storage import inspiration_media_key

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.platform.filesystem.file_store import FileStore
    from src.platform.filesystem.storage_driver import FileStorageDriver

logger = logging.getLogger(__name__)


class InspirationController(BaseController):
    """Controller for the Inspirations module."""

    def __init__(
        self,
        inspiration_manager: InspirationManager,
        inspiration_repository: InspirationRepository,
        file_store: "FileStore",
        storage_driver: "FileStorageDriver",
    ):
        super().__init__()
        self.manager = inspiration_manager
        self.repository = inspiration_repository
        self.file_store = file_store
        self.storage_driver = storage_driver

    @staticmethod
    def _not_found_status(message: str) -> int:
        return 404 if "not found" in message.lower() else 400

    # ========== Feed ==========

    async def list_feed(
        self,
        current_user,
        query: Optional[str],
        limit: int,
        offset: int,
        collection_id: Optional[str],
        author_id: Optional[str],
        saved: Optional[bool],
    ) -> APIResponse:
        try:
            limit = max(1, min(limit, 100))
            offset = max(0, offset)
            items, total = self.repository.list_feed(
                viewer_id=current_user.id,
                query=query,
                limit=limit,
                offset=offset,
                collection_id=collection_id,
                author_id=author_id,
                saved=saved,
            )
            return self.success_response(data={
                "items": [inspiration_to_dto(i) for i in items],
                "total": total,
            })
        except Exception as e:
            self.logger.error(f"Failed to list inspirations feed: {e}")
            return self.error_response(error="list_failed", message="Failed to list inspirations")

    async def get_inspiration(self, inspiration_id: str, current_user) -> APIResponse:
        try:
            insp = self.repository.get_by_id(inspiration_id, viewer_id=current_user.id)
            if not insp:
                raise ValueError("Inspiration not found")
            return self.success_response(data={"inspiration": inspiration_to_dto(insp)})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to get inspiration: {e}")
            return self.error_response(error="get_failed", message="Failed to get inspiration")

    async def get_params(self, inspiration_id: str, current_user) -> APIResponse:
        try:
            insp = self.repository.get_by_id(inspiration_id)
            if not insp:
                raise ValueError("Inspiration not found")
            return self.success_response(data={
                "form_data": insp.params_snapshot.get("form_data", {}),
                "preset_id": insp.preset_id,
                "preset_name": insp.preset_name,
                "mode": insp.params_snapshot.get("mode"),
                "omitted_fields": insp.params_snapshot.get("omitted_fields", []),
            })
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to get inspiration params: {e}")
            return self.error_response(error="get_params_failed", message="Failed to get inspiration params")

    # ========== Publish / delete ==========

    async def publish(self, request: PublishInspirationRequest, current_user) -> APIResponse:
        try:
            insp = self.manager.publish(
                current_user.id,
                request.generation_id,
                request.filenames,
                request.title,
                request.description,
            )
            return self.success_response(data={"inspiration": inspiration_to_dto(insp)})
        except ValueError as e:
            return self.error_response(
                error="publish_failed", message=str(e), status_code=self._not_found_status(str(e))
            )
        except Exception as e:
            self.logger.error(f"Failed to publish inspiration: {e}")
            return self.error_response(error="publish_failed", message="Failed to publish inspiration")

    async def delete_inspiration(self, inspiration_id: str, current_user) -> APIResponse:
        try:
            self.manager.delete(
                inspiration_id, current_user.id, is_admin=(current_user.account_type == AccountType.ADMIN)
            )
            return self.success_response(data={"id": inspiration_id, "deleted": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to delete inspiration: {e}")
            return self.error_response(error="delete_failed", message="Failed to delete inspiration")

    # ========== Saves ==========

    async def save_to_library(self, inspiration_id: str, current_user) -> APIResponse:
        try:
            save_count = self.manager.save_to_library(inspiration_id, current_user.id)
            return self.success_response(data={"saved": True, "save_count": save_count})
        except ValueError as e:
            return self.error_response(
                error="save_failed", message=str(e), status_code=self._not_found_status(str(e))
            )
        except Exception as e:
            self.logger.error(f"Failed to save inspiration to library: {e}")
            return self.error_response(error="save_failed", message="Failed to save inspiration to library")

    async def unsave(self, inspiration_id: str, current_user) -> APIResponse:
        try:
            save_count = self.manager.unsave(inspiration_id, current_user.id)
            return self.success_response(data={"saved": False, "save_count": save_count})
        except Exception as e:
            self.logger.error(f"Failed to unsave inspiration: {e}")
            return self.error_response(error="unsave_failed", message="Failed to unsave inspiration")

    # ========== Comments ==========

    async def list_comments(self, inspiration_id: str, current_user) -> APIResponse:
        try:
            insp = self.repository.get_by_id(inspiration_id)
            if not insp:
                raise ValueError("Inspiration not found")
            comments = self.repository.list_comments(inspiration_id)
            return self.success_response(data={"items": [comment_to_dto(c) for c in comments]})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to list inspiration comments: {e}")
            return self.error_response(error="list_comments_failed", message="Failed to list comments")

    async def add_comment(self, inspiration_id: str, request: CommentCreateRequest, current_user) -> APIResponse:
        try:
            comment = self.manager.add_comment(inspiration_id, current_user.id, request.body)
            return self.success_response(data={"comment": comment_to_dto(comment)})
        except ValueError as e:
            return self.error_response(
                error="comment_failed", message=str(e), status_code=self._not_found_status(str(e))
            )
        except Exception as e:
            self.logger.error(f"Failed to add inspiration comment: {e}")
            return self.error_response(error="comment_failed", message="Failed to add comment")

    async def delete_comment(self, comment_id: str, current_user) -> APIResponse:
        try:
            self.manager.delete_comment(
                comment_id, current_user.id, is_admin=(current_user.account_type == AccountType.ADMIN)
            )
            return self.success_response(data={"id": comment_id, "deleted": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to delete inspiration comment: {e}")
            return self.error_response(error="delete_failed", message="Failed to delete comment")

    # ========== Collections ==========

    async def list_collections(self, current_user) -> APIResponse:
        try:
            collections = self.repository.list_collections(current_user.id)
            return self.success_response(data={"items": [collection_to_dto(c) for c in collections]})
        except Exception as e:
            self.logger.error(f"Failed to list inspiration collections: {e}")
            return self.error_response(error="list_collections_failed", message="Failed to list collections")

    async def create_collection(self, request: CreateInspirationCollectionRequest, current_user) -> APIResponse:
        try:
            collection = self.manager.create_collection(current_user.id, request.name, request.parent_id)
            return self.success_response(data={"collection": collection_to_dto(collection)})
        except ValueError as e:
            return self.error_response(
                error="create_collection_failed", message=str(e), status_code=self._not_found_status(str(e))
            )
        except Exception as e:
            self.logger.error(f"Failed to create inspiration collection: {e}")
            return self.error_response(error="create_collection_failed", message="Failed to create collection")

    async def update_collection(
        self, collection_id: str, request: UpdateInspirationCollectionRequest, current_user
    ) -> APIResponse:
        try:
            parent_id_set = "parent_id" in request.model_fields_set
            collection = self.manager.update_collection(
                collection_id,
                current_user.id,
                name=request.name,
                parent_id=request.parent_id,
                parent_id_set=parent_id_set,
            )
            return self.success_response(data={"collection": collection_to_dto(collection)})
        except ValueError as e:
            return self.error_response(
                error="update_collection_failed", message=str(e), status_code=self._not_found_status(str(e))
            )
        except Exception as e:
            self.logger.error(f"Failed to update inspiration collection: {e}")
            return self.error_response(error="update_collection_failed", message="Failed to update collection")

    async def delete_collection(self, collection_id: str, current_user) -> APIResponse:
        try:
            self.manager.delete_collection(collection_id, current_user.id)
            return self.success_response(data={"id": collection_id, "deleted": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to delete inspiration collection: {e}")
            return self.error_response(error="delete_collection_failed", message="Failed to delete collection")

    async def add_collection_item(
        self, collection_id: str, request: InspirationCollectionItemRequest, current_user
    ) -> APIResponse:
        try:
            self.manager.add_item(collection_id, current_user.id, request.inspiration_id)
            return self.success_response(data={"added": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to add item to inspiration collection: {e}")
            return self.error_response(error="add_item_failed", message="Failed to add item to collection")

    async def remove_collection_item(self, collection_id: str, inspiration_id: str, current_user) -> APIResponse:
        try:
            self.manager.remove_item(collection_id, current_user.id, inspiration_id)
            return self.success_response(data={"removed": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to remove item from inspiration collection: {e}")
            return self.error_response(error="remove_item_failed", message="Failed to remove item from collection")

    # ========== Media serving ==========

    async def serve_media(self, inspiration_id: str, filename: str):
        """Serve one of an inspiration's copied media files.

        Mirrors `MediaController.serve_uploaded_media`'s posture: no
        authentication, a `ValueError` -> `error_api_response` on any miss.
        """
        try:
            insp = self.repository.get_by_id(inspiration_id)
            if not insp or not any(entry.get("filename") == filename for entry in insp.media):
                raise ValueError("File not found")

            key = inspiration_media_key(inspiration_id, filename)
            if not self.storage_driver.exists(key):
                raise ValueError("File not found")

            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            local_path = self.storage_driver.local_path(key)
            if local_path is not None:
                return FileResponse(path=str(local_path), media_type=media_type, filename=filename)
            return Response(content=self.storage_driver.get_bytes(key), media_type=media_type)
        except ValueError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error serving inspiration media: {e}")
            return self.error_api_response(error="server_error", message="Failed to serve media")


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
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_feed(
            current_user, query, limit, offset, collection_id, author_id, saved
        )

    @router.post("", response_model=APIResponse, summary="Publish Inspiration")
    async def publish(
        request: PublishInspirationRequest,
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.publish(request, current_user)

    @router.get("/collections", response_model=APIResponse, summary="List My Inspiration Collections")
    async def list_collections(current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_collections(current_user)

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

    @router.get("/{inspiration_id}/{filename}", summary="Serve Inspiration Media")
    async def serve_media(inspiration_id: str, filename: str):
        return await controller.serve_media(inspiration_id, filename)

    return router
