"""
Library Controller

The user's private media library: list with filters, curate, delete, and copy
a generated file in from history. Thin route handlers delegating to
`LibraryManager`.

Every handler is authenticated and every operation is scoped to the requesting
user. A library item that belongs to someone else answers exactly like one that
does not exist - 404, never 403 - so a caller cannot probe for another user's
files (the `delete_upload` precedent in `src.features.media.routes`).
"""

import logging
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.library.dto import CopyFromGenerationRequest, SetLibraryTagsRequest
from src.features.library.manager import LibraryManager

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class LibraryController(BaseController):
    """Controller for library browsing, curation and history copies."""

    def __init__(self, library_manager: LibraryManager):
        super().__init__()
        self.manager = library_manager

    async def list_items(
        self,
        current_user,
        media_type: Optional[str] = None,
        tag_ids: Optional[str] = None,
        collection_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> APIResponse:
        """List the current user's library, newest first."""
        try:
            parsed_tag_ids = [t.strip() for t in tag_ids.split(',')] if tag_ids else None
            result = self.manager.list_items(
                current_user.id,
                media_type=media_type,
                tag_ids=parsed_tag_ids,
                collection_id=collection_id,
                search=search,
                limit=limit,
                offset=offset,
            )
            return self.success_response(data=result.model_dump())
        except ValueError as e:
            return self.error_response(error="invalid_filter", message=str(e))
        except Exception as e:
            self.logger.error(f"Failed to list library items: {e}")
            return self.error_response(error="list_failed", message="Failed to list library items")

    async def get_facets(self, current_user) -> APIResponse:
        """Per-media-type counts for the current user's library."""
        try:
            return self.success_response(data=self.manager.get_facets(current_user.id).model_dump())
        except Exception as e:
            self.logger.error(f"Failed to get library facets: {e}")
            return self.error_response(error="facets_failed", message="Failed to get library facets")

    async def get_item(self, item_id: str, current_user) -> APIResponse:
        """Get one library item owned by the current user."""
        try:
            return self.success_response(data=self.manager.get_item(item_id, current_user.id).model_dump())
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to get library item: {e}")
            return self.error_response(error="get_failed", message="Failed to get library item")

    async def delete_item(self, item_id: str, current_user) -> APIResponse:
        """Delete one library item owned by the current user."""
        try:
            self.manager.delete_item(item_id, current_user.id)
            return self.success_response(data={"id": item_id, "deleted": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to delete library item: {e}")
            return self.error_response(error="delete_failed", message="Failed to delete library item")

    async def get_tags(self, item_id: str, current_user) -> APIResponse:
        """List a library item's tags."""
        try:
            return self.success_response(data={"tags": self.manager.get_tags(item_id, current_user.id)})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to get library item tags: {e}")
            return self.error_response(error="get_tags_failed", message="Failed to get tags")

    async def set_tags(
        self,
        item_id: str,
        request: SetLibraryTagsRequest,
        current_user
    ) -> APIResponse:
        """Replace a library item's tags."""
        try:
            tags = self.manager.set_tags(item_id, request.tag_ids, current_user.id)
            return self.success_response(data={"tags": tags})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to set library item tags: {e}")
            return self.error_response(error="set_tags_failed", message="Failed to set tags")

    async def copy_from_generation(
        self,
        request: CopyFromGenerationRequest,
        current_user
    ) -> APIResponse:
        """Copy one of the user's generated files into their library."""
        try:
            item = self.manager.copy_generation_file(request.file_id, current_user.id)
            return self.success_response(data={"item": item.model_dump()})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            self.logger.error(f"Failed to copy file into library: {e}")
            return self.error_response(error="copy_failed", message="Failed to copy file into library")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.library_controller
    router = APIRouter(prefix="/api/library", tags=["Library"])

    @router.get("/items", response_model=APIResponse, summary="List Library Items")
    async def list_items(
        media_type: Optional[str] = Query(None, description="'image' | 'video' | 'audio'"),
        tag_ids: Optional[str] = Query(None, description="Comma-separated tag ids; an item must have ALL of them"),
        collection_id: Optional[str] = Query(None, description="Only items in this collection"),
        search: Optional[str] = Query(None, description="Substring match on the item's original filename"),
        limit: int = 50,
        offset: int = 0,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """List the current user's library items, newest first."""
        return await controller.list_items(
            current_user, media_type, tag_ids, collection_id, search, limit, offset
        )

    @router.get("/facets", response_model=APIResponse, summary="Library Facets")
    async def get_facets(
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Per-media-type counts for the current user's library."""
        return await controller.get_facets(current_user)

    @router.post("/items/from-generation", response_model=APIResponse, summary="Copy Generated File Into Library")
    async def copy_from_generation(
        request: CopyFromGenerationRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Copy a generated file into the library as a standalone resource."""
        return await controller.copy_from_generation(request, current_user)

    @router.get("/items/{item_id}", response_model=APIResponse, summary="Get Library Item")
    async def get_item(
        item_id: str,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Get one library item."""
        return await controller.get_item(item_id, current_user)

    @router.delete("/items/{item_id}", response_model=APIResponse, summary="Delete Library Item")
    async def delete_item(
        item_id: str,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete one library item (row, file and memberships)."""
        return await controller.delete_item(item_id, current_user)

    @router.get("/items/{item_id}/tags", response_model=APIResponse, summary="Get Library Item Tags")
    async def get_tags(
        item_id: str,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """List a library item's tags."""
        return await controller.get_tags(item_id, current_user)

    @router.put("/items/{item_id}/tags", response_model=APIResponse, summary="Set Library Item Tags")
    async def set_tags(
        item_id: str,
        request: SetLibraryTagsRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Replace a library item's tags."""
        return await controller.set_tags(item_id, request, current_user)

    return router
