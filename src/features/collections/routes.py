"""
Collection Controller

Handles collection CRUD and membership operations with thin route handlers
delegating to controller methods. Mutations delegate to
`src.features.collections.operations`.
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.collections.dto import (
    CollectionScope,
    CreateCollectionRequest,
    UpdateCollectionRequest,
    MoveCollectionRequest,
    BulkMoveCollectionsRequest,
    CollectionMembersRequest,
    CollectionUploadMembersRequest,
    CollectionPromptMembersRequest,
)
from src.features.collections import operations
from src.features.collections.repository import CollectionRepository
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class CollectionController(BaseController):
    """
    Controller for collection operations.

    Handles CRUD for collections plus membership management, delegating
    mutations to `src.features.collections.operations`. All operations are
    scoped to the current user and to a 'history' | 'library' collection tree.
    """

    def __init__(self, collection_repository: CollectionRepository):
        super().__init__()
        self.repository = collection_repository

    # ========== List Methods ==========

    async def list_collections(self, scope: CollectionScope, user: User) -> APIResponse:
        """List all of the user's collections within scope, with generation counts. Pure DB read."""
        try:
            collections = self.repository.list(user.id, scope)
            return self.success_response(data={
                "collections": [c.to_dict() for c in collections],
                "total": len(collections)
            })
        except ValueError as e:
            return self.error_api_response(error="list_collections_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error listing collections: {e}")
            return self.error_api_response(error="list_collections_failed", message=str(e))

    # ========== CRUD Methods ==========

    async def create_collection(self, request: CreateCollectionRequest, user: User) -> APIResponse:
        """Create a new collection."""
        try:
            collection = operations.create_collection(
                self.repository, request.name, user.id, request.scope, request.parent_id
            )
            return self.success_response(data={
                "message": f"Collection '{collection.name}' created successfully",
                "collection": collection.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="create_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error creating collection: {e}")
            return self.error_api_response(error="create_collection_failed", message=str(e))

    async def rename_collection(
        self,
        collection_id: str,
        request: UpdateCollectionRequest,
        user: User
    ) -> APIResponse:
        """Rename a collection."""
        try:
            collection = operations.rename_collection(self.repository, collection_id, request.name, user.id, request.scope)
            return self.success_response(data={
                "message": "Collection updated successfully",
                "collection": collection.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="update_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error updating collection: {e}")
            return self.error_api_response(error="update_collection_failed", message=str(e))

    async def move_collection(
        self,
        collection_id: str,
        request: MoveCollectionRequest,
        user: User
    ) -> APIResponse:
        """Reparent a collection in the folder tree (parent_id None = root)."""
        try:
            collection = operations.move_collection(
                self.repository, collection_id, request.parent_id, user.id, request.scope
            )
            return self.success_response(data={
                "message": "Collection moved successfully",
                "collection": collection.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="move_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error moving collection: {e}")
            return self.error_api_response(error="move_collection_failed", message=str(e))

    async def bulk_move_collections(
        self,
        request: BulkMoveCollectionsRequest,
        user: User
    ) -> APIResponse:
        """Reparent several collections at once. Per-item failures don't block the rest."""
        try:
            result = operations.bulk_move_collections(
                self.repository, request.collection_ids, request.parent_id, user.id, request.scope
            )
            return self.success_response(data=result)
        except Exception as e:
            self.logger.error(f"Error bulk moving collections: {e}")
            return self.error_api_response(error="bulk_move_collections_failed", message=str(e))

    async def delete_collection(self, collection_id: str, scope: CollectionScope, user: User) -> APIResponse:
        """Delete a collection (cascade removes all memberships)."""
        try:
            operations.delete_collection(self.repository, collection_id, user.id, scope)
            return self.success_response(data={
                "message": "Collection deleted successfully"
            })
        except ValueError as e:
            return self.error_api_response(error="delete_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error deleting collection: {e}")
            return self.error_api_response(error="delete_collection_failed", message=str(e))

    # ========== Membership Methods ==========

    async def add_members(
        self,
        collection_id: str,
        request: CollectionMembersRequest,
        user: User
    ) -> APIResponse:
        """Add generations to a collection."""
        try:
            added = operations.add_members(self.repository, collection_id, request.generation_ids, user.id, request.scope)
            return self.success_response(data={
                "message": f"Added {added} generation(s) to collection",
                "added": added
            })
        except ValueError as e:
            return self.error_api_response(error="add_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error adding collection members: {e}")
            return self.error_api_response(error="add_members_failed", message=str(e))

    async def remove_members(
        self,
        collection_id: str,
        request: CollectionMembersRequest,
        user: User
    ) -> APIResponse:
        """Remove generations from a collection."""
        try:
            removed = operations.remove_members(self.repository, collection_id, request.generation_ids, user.id, request.scope)
            return self.success_response(data={
                "message": f"Removed {removed} generation(s) from collection",
                "removed": removed
            })
        except ValueError as e:
            return self.error_api_response(error="remove_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error removing collection members: {e}")
            return self.error_api_response(error="remove_members_failed", message=str(e))

    async def add_upload_members(
        self,
        collection_id: str,
        request: CollectionUploadMembersRequest,
        user: User
    ) -> APIResponse:
        """Add library uploads to a collection."""
        try:
            added = operations.add_upload_members(self.repository, collection_id, request.upload_ids, user.id, request.scope)
            return self.success_response(data={
                "message": f"Added {added} item(s) to collection",
                "added": added
            })
        except ValueError as e:
            return self.error_api_response(error="add_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error adding collection upload members: {e}")
            return self.error_api_response(error="add_members_failed", message=str(e))

    async def remove_upload_members(
        self,
        collection_id: str,
        request: CollectionUploadMembersRequest,
        user: User
    ) -> APIResponse:
        """Remove library uploads from a collection."""
        try:
            removed = operations.remove_upload_members(self.repository, collection_id, request.upload_ids, user.id, request.scope)
            return self.success_response(data={
                "message": f"Removed {removed} item(s) from collection",
                "removed": removed
            })
        except ValueError as e:
            return self.error_api_response(error="remove_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error removing collection upload members: {e}")
            return self.error_api_response(error="remove_members_failed", message=str(e))

    async def add_prompt_members(
        self,
        collection_id: str,
        request: CollectionPromptMembersRequest,
        user: User
    ) -> APIResponse:
        """Add saved prompts to a collection."""
        try:
            added = operations.add_prompt_members(self.repository, collection_id, request.prompt_ids, user.id, request.scope)
            return self.success_response(data={
                "message": f"Added {added} prompt(s) to collection",
                "added": added
            })
        except ValueError as e:
            return self.error_api_response(error="add_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error adding collection prompt members: {e}")
            return self.error_api_response(error="add_members_failed", message=str(e))

    async def remove_prompt_members(
        self,
        collection_id: str,
        request: CollectionPromptMembersRequest,
        user: User
    ) -> APIResponse:
        """Remove saved prompts from a collection."""
        try:
            removed = operations.remove_prompt_members(self.repository, collection_id, request.prompt_ids, user.id, request.scope)
            return self.success_response(data={
                "message": f"Removed {removed} prompt(s) from collection",
                "removed": removed
            })
        except ValueError as e:
            return self.error_api_response(error="remove_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error removing collection prompt members: {e}")
            return self.error_api_response(error="remove_members_failed", message=str(e))


# ========== Route Handlers ==========

def build_router(container: "AppContainer") -> APIRouter:
    controller = container.collection_controller
    router = APIRouter(prefix="/api/collections", tags=["Collections"])

    @router.get("", response_model=APIResponse, summary="List Collections")
    async def list_collections(
        scope: CollectionScope = Query(..., description="Which folder tree to list: 'history', 'library', or 'prompts'"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """List all of the current user's collections within scope, with generation counts."""
        return await controller.list_collections(scope, current_user)

    @router.post("", response_model=APIResponse, summary="Create Collection")
    async def create_collection(
        request: CreateCollectionRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Create a new collection."""
        return await controller.create_collection(request, current_user)

    @router.put("/{collection_id}", response_model=APIResponse, summary="Rename Collection")
    async def rename_collection(
        collection_id: str,
        request: UpdateCollectionRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Rename a collection."""
        return await controller.rename_collection(collection_id, request, current_user)

    @router.put("/{collection_id}/move", response_model=APIResponse, summary="Move Collection")
    async def move_collection(
        collection_id: str,
        request: MoveCollectionRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Reparent a collection in the folder tree (parent_id null = move to root)."""
        return await controller.move_collection(collection_id, request, current_user)

    @router.post("/bulk-move", response_model=APIResponse, summary="Bulk Move Collections")
    async def bulk_move_collections(
        request: BulkMoveCollectionsRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Reparent several collections in one call (parent_id null = move to root)."""
        return await controller.bulk_move_collections(request, current_user)

    @router.delete("/{collection_id}", response_model=APIResponse, summary="Delete Collection")
    async def delete_collection(
        collection_id: str,
        scope: CollectionScope = Query(..., description="Which folder tree the collection belongs to"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete a collection (cascade removes all memberships)."""
        return await controller.delete_collection(collection_id, scope, current_user)

    @router.post("/{collection_id}/members", response_model=APIResponse, summary="Add Collection Members")
    async def add_members(
        collection_id: str,
        request: CollectionMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Add generations to a collection."""
        return await controller.add_members(collection_id, request, current_user)

    @router.delete("/{collection_id}/members", response_model=APIResponse, summary="Remove Collection Members")
    async def remove_members(
        collection_id: str,
        request: CollectionMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Remove generations from a collection."""
        return await controller.remove_members(collection_id, request, current_user)

    @router.post("/{collection_id}/uploads", response_model=APIResponse, summary="Add Collection Library Items")
    async def add_upload_members(
        collection_id: str,
        request: CollectionUploadMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Add library uploads to a collection."""
        return await controller.add_upload_members(collection_id, request, current_user)

    @router.delete("/{collection_id}/uploads", response_model=APIResponse, summary="Remove Collection Library Items")
    async def remove_upload_members(
        collection_id: str,
        request: CollectionUploadMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Remove library uploads from a collection."""
        return await controller.remove_upload_members(collection_id, request, current_user)

    @router.post("/{collection_id}/prompts", response_model=APIResponse, summary="Add Collection Prompts")
    async def add_prompt_members(
        collection_id: str,
        request: CollectionPromptMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Add saved prompts to a collection."""
        return await controller.add_prompt_members(collection_id, request, current_user)

    @router.delete("/{collection_id}/prompts", response_model=APIResponse, summary="Remove Collection Prompts")
    async def remove_prompt_members(
        collection_id: str,
        request: CollectionPromptMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Remove saved prompts from a collection."""
        return await controller.remove_prompt_members(collection_id, request, current_user)

    return router
