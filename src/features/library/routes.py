"""
Library Controller

The user's private media library: list with filters, curate, delete, and copy
a generated file in from history. Thin route handlers delegating to
`src.features.library.operations` against a held `LibraryCollaborators`
bundle.

Every handler is authenticated and every operation is scoped to the requesting
user. A library item that belongs to someone else answers exactly like one that
does not exist - 404, never 403 - so a caller cannot probe for another user's
files (the `delete_upload` precedent in `src.features.media.routes`).
"""

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.http.spooled_zip import stream_spooled_zip
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import get_current_active_user
from src.features.library.collaborators import LibraryCollaborators
from src.features.library.criteria_delete import delete_items, owner_criteria, preview_items
from src.features.library.dto import (
    SetLibraryFavoriteRequest,
    BulkDeleteLibraryByCriteriaRequest,
    CopyFromGenerationRequest,
    LibraryExportRequest,
    SetLibraryTagsRequest,
)
from src.features.library import operations

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class LibraryController(BaseController):
    """Controller for library browsing, curation and history copies."""

    def __init__(self, library_collaborators: LibraryCollaborators):
        super().__init__()
        self.collaborators = library_collaborators

    @guarded({ValueError: Fail("invalid_filter", status=400)}, Fail("list_failed", "Failed to list library items", 400))
    async def list_items(
        self,
        current_user,
        media_type: Optional[str] = None,
        tag_ids: Optional[str] = None,
        collection_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        include_descendants: bool = True,
        unsorted: bool = False,
        favorites_only: bool = False,
    ) -> APIResponse:
        """List the current user's library, newest first."""
        parsed_tag_ids = [t.strip() for t in tag_ids.split(',')] if tag_ids else None
        result = await asyncio.to_thread(
            operations.list_items,
            self.collaborators,
            current_user.id,
            media_type=media_type,
            tag_ids=parsed_tag_ids,
            collection_id=collection_id,
            search=search,
            limit=limit,
            offset=offset,
            include_descendants=include_descendants,
            unsorted=unsorted,
            favorites_only=favorites_only,
        )
        return self.success_response(data=result.model_dump())

    @guarded(fallback=Fail("facets_failed", "Failed to get library facets", 400))
    async def get_facets(
        self,
        current_user,
        collection_id: Optional[str] = None,
        include_descendants: bool = True,
        unsorted: bool = False,
        favorites_only: bool = False,
    ) -> APIResponse:
        """Per-media-type counts for the current user's library view."""
        facets = await asyncio.to_thread(
            operations.get_facets, self.collaborators, current_user.id,
            collection_id, include_descendants, unsorted, favorites_only,
        )
        return self.success_response(data=facets.model_dump())

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("get_failed", "Failed to get library item", 400))
    async def get_item(self, item_id: str, current_user) -> APIResponse:
        """Get one library item owned by the current user."""
        item = await asyncio.to_thread(operations.get_item, self.collaborators, item_id, current_user.id)
        return self.success_response(data=item.model_dump())

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("delete_failed", "Failed to delete library item", 400))
    async def delete_item(self, item_id: str, current_user) -> APIResponse:
        """Delete one library item owned by the current user."""
        await asyncio.to_thread(operations.delete_item, self.collaborators, item_id, current_user.id)
        return self.success_response(data={"id": item_id, "deleted": True})

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("get_tags_failed", "Failed to get tags", 400))
    async def get_tags(self, item_id: str, current_user) -> APIResponse:
        """List a library item's tags."""
        tags = await asyncio.to_thread(operations.get_tags, self.collaborators, item_id, current_user.id)
        return self.success_response(data={"tags": tags})

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("set_tags_failed", "Failed to set tags", 400))
    async def set_tags(
        self,
        item_id: str,
        request: SetLibraryTagsRequest,
        current_user
    ) -> APIResponse:
        """Replace a library item's tags."""
        tags = await asyncio.to_thread(operations.set_tags, self.collaborators, item_id, request.tag_ids, current_user.id)
        return self.success_response(data={"tags": tags})

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("set_favorite_failed", "Failed to set favorite", 400))
    async def set_favorite(self, item_id: str, request: SetLibraryFavoriteRequest, current_user) -> APIResponse:
        value = await asyncio.to_thread(
            operations.set_favorite, self.collaborators, item_id, request.is_favorite, current_user.id
        )
        return self.success_response(data={"id": item_id, "is_favorite": value})

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("copy_failed", "Failed to copy file into library", 400))
    async def copy_from_generation(
        self,
        request: CopyFromGenerationRequest,
        current_user
    ) -> APIResponse:
        """Copy one of the user's generated files into their library."""
        item = await asyncio.to_thread(
            operations.copy_generation_file, self.collaborators, request.file_id, current_user.id
        )
        return self.success_response(data={"item": item.model_dump()})

    @guarded({ValueError: Fail("validation_error", status=400)}, Fail("count_failed", "Failed to count library items by criteria", 400))
    async def count_items_by_criteria(
        self,
        request: BulkDeleteLibraryByCriteriaRequest,
        current_user
    ) -> APIResponse:
        """Count the current user's library items matching criteria, AND-ed (for preview)."""
        def _count() -> int:
            criteria = owner_criteria(self.collaborators, request, current_user.id)
            return preview_items(self.collaborators, criteria)

        count = await asyncio.to_thread(_count)
        return self.success_response(
            message=f"Found {count} item(s) matching the given criteria",
            data={"count": count}
        )

    @guarded(fallback=Fail("delete_failed", "Failed to delete library items by criteria", 400))
    async def bulk_delete_by_criteria(
        self,
        request: BulkDeleteLibraryByCriteriaRequest,
        current_user
    ) -> APIResponse:
        """Delete every library item of the current user matching criteria, AND-ed."""
        try:
            criteria = await asyncio.to_thread(owner_criteria, self.collaborators, request, current_user.id)
        except ValueError as e:
            return self.error_response(error="validation_error", message=str(e), status_code=400)

        if criteria.is_empty():
            return self.error_response(
                error="no_criteria",
                message="Choose at least one criterion before deleting library items",
                status_code=400
            )

        summary = await asyncio.to_thread(delete_items, self.collaborators, criteria)
        return self.success_response(
            message=f"Successfully deleted {summary['deleted_count']} item(s).",
            data=summary
        )

    @guarded({ValueError: Fail("not_found", status=404)}, Fail("export_failed", "Failed to export library items", 400))
    async def export_items(self, request: LibraryExportRequest, current_user):
        """Export the given library items as a downloadable zip.

        Unlike the other endpoints this returns the binary zip directly (via
        a streamed response) instead of an APIResponse JSON envelope.
        """
        if not request.item_ids:
            return self.error_response(
                error="invalid_request", message="No item IDs provided", status_code=400
            )
        zip_file, filename = await asyncio.to_thread(
            operations.export_zip, self.collaborators, request.item_ids, current_user.id
        )
        return stream_spooled_zip(zip_file, filename)


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.library_controller
    router = APIRouter(prefix="/api/library", tags=["Library"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("/items", response_model=APIResponse, summary="List Library Items")
    async def list_items(
        media_type: Optional[str] = Query(None, description="'image' | 'video' | 'audio'"),
        tag_ids: Optional[str] = Query(None, description="Comma-separated tag ids; an item must have ALL of them"),
        collection_id: Optional[str] = Query(None, description="Only items in this collection"),
        search: Optional[str] = Query(None, description="Substring match on the item's original filename"),
        include_descendants: bool = Query(True, description="With collection_id: include sub-collections' items"),
        unsorted: bool = Query(False, description="Only items in no collection"),
        favorites_only: bool = Query(False, description="Only favorite items"),
        limit: int = 50,
        offset: int = 0,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """List the current user's library items, newest first."""
        return await controller.list_items(
            current_user, media_type, tag_ids, collection_id, search, limit, offset,
            include_descendants, unsorted, favorites_only,
        )

    @router.get("/facets", response_model=APIResponse, summary="Library Facets")
    async def get_facets(
        collection_id: Optional[str] = Query(None, description="Count only items in this collection"),
        include_descendants: bool = Query(True, description="With collection_id: include sub-collections' items"),
        unsorted: bool = Query(False, description="Count only items in no collection"),
        favorites_only: bool = Query(False, description="Count favorite items only"),
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Per-media-type counts for the current user's library view."""
        return await controller.get_facets(
            current_user, collection_id, include_descendants, unsorted, favorites_only,
        )

    @router.post("/items/from-generation", response_model=APIResponse, summary="Copy Generated File Into Library")
    async def copy_from_generation(
        request: CopyFromGenerationRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Copy a generated file into the library as a standalone resource."""
        return await controller.copy_from_generation(request, current_user)

    @router.post("/export", summary="Export Library Items as Zip", dependencies=[key_scope(ApiScope.READ)])
    async def export_items(
        request: LibraryExportRequest,
        current_user=Depends(get_current_active_user)
    ):
        """Export the given library items as a single zip.

        Returns the binary zip directly (not an APIResponse envelope).
        """
        return await controller.export_items(request, current_user)

    @router.post("/count-by-criteria", response_model=APIResponse, summary="Count Library Items by Criteria", dependencies=[key_scope(ApiScope.READ)])
    async def count_items_by_criteria(
        request: BulkDeleteLibraryByCriteriaRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Count the current user's library items matching criteria, AND-ed (for confirmation preview)."""
        return await controller.count_items_by_criteria(request, current_user)

    @router.post("/bulk-delete-by-criteria", response_model=APIResponse, summary="Bulk Delete Library Items by Criteria")
    async def bulk_delete_by_criteria(
        request: BulkDeleteLibraryByCriteriaRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete every library item of the current user matching criteria, AND-ed."""
        return await controller.bulk_delete_by_criteria(request, current_user)

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

    @router.put("/items/{item_id}/favorite", response_model=APIResponse, summary="Set Library Item Favorite")
    async def set_favorite(
        item_id: str,
        request: SetLibraryFavoriteRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.set_favorite(item_id, request, current_user)

    return router
