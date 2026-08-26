"""
Model Collection Controller

Handles model collection CRUD and membership operations with thin route
handlers delegating to controller methods. Mutations delegate to
`src.features.model_library.operations`. Mirrors CollectionController
(generation collections).

Registered on its own router/prefix (/api/models/collections) so it does not
collide with the `/api/models/{model_id}` catch-all route in model_controller.
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.model_library.dto import (
    CreateModelCollectionRequest,
    UpdateModelCollectionRequest,
    MoveModelCollectionRequest,
    BulkMoveModelCollectionsRequest,
    ModelCollectionMembersRequest,
)
from src.features.model_library import operations
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class ModelCollectionController(BaseController):
    """
    Controller for model collection operations.

    Handles CRUD for model collections plus membership management, delegating
    mutations to `src.features.model_library.operations`. All operations are
    scoped to the current user.
    """

    def __init__(self, model_collection_repository: ModelCollectionRepository):
        super().__init__()
        self.repository = model_collection_repository

    # ========== List Methods ==========

    async def list_collections(self, user: User) -> APIResponse:
        """List all model collections owned by the user with model counts. Pure DB read."""
        try:
            collections = self.repository.list(user.id)
            return self.success_response(data={
                "collections": [c.to_dict() for c in collections],
                "total": len(collections)
            })
        except ValueError as e:
            return self.error_api_response(error="list_collections_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error listing model collections: {e}")
            return self.error_api_response(error="list_collections_failed", message=str(e))

    # ========== CRUD Methods ==========

    async def create_collection(self, request: CreateModelCollectionRequest, user: User) -> APIResponse:
        """Create a new model collection."""
        try:
            collection = operations.create_collection(self.repository, request.name, user.id, request.parent_id)
            return self.success_response(data={
                "message": f"Collection '{collection.name}' created successfully",
                "collection": collection.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="create_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error creating model collection: {e}")
            return self.error_api_response(error="create_collection_failed", message=str(e))

    async def rename_collection(
        self,
        collection_id: str,
        request: UpdateModelCollectionRequest,
        user: User
    ) -> APIResponse:
        """Rename a model collection."""
        try:
            collection = operations.rename_collection(self.repository, collection_id, request.name, user.id)
            return self.success_response(data={
                "message": "Collection updated successfully",
                "collection": collection.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="update_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error updating model collection: {e}")
            return self.error_api_response(error="update_collection_failed", message=str(e))

    async def move_collection(
        self,
        collection_id: str,
        request: MoveModelCollectionRequest,
        user: User
    ) -> APIResponse:
        """Reparent a model collection in the folder tree (parent_id None = root)."""
        try:
            collection = operations.move_collection(self.repository, collection_id, request.parent_id, user.id)
            return self.success_response(data={
                "message": "Collection moved successfully",
                "collection": collection.to_dict()
            })
        except ValueError as e:
            return self.error_api_response(error="move_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error moving model collection: {e}")
            return self.error_api_response(error="move_collection_failed", message=str(e))

    async def bulk_move_collections(
        self,
        request: BulkMoveModelCollectionsRequest,
        user: User
    ) -> APIResponse:
        """Reparent several model collections at once. Per-item failures don't block the rest."""
        try:
            result = operations.bulk_move_collections(self.repository, request.collection_ids, request.parent_id, user.id)
            return self.success_response(data=result)
        except Exception as e:
            self.logger.error(f"Error bulk moving model collections: {e}")
            return self.error_api_response(error="bulk_move_collections_failed", message=str(e))

    async def delete_collection(self, collection_id: str, user: User) -> APIResponse:
        """Delete a model collection (cascade removes all memberships)."""
        try:
            operations.delete_collection(self.repository, collection_id, user.id)
            return self.success_response(data={
                "message": "Collection deleted successfully"
            })
        except ValueError as e:
            return self.error_api_response(error="delete_collection_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error deleting model collection: {e}")
            return self.error_api_response(error="delete_collection_failed", message=str(e))

    # ========== Membership Methods ==========

    async def add_members(
        self,
        collection_id: str,
        request: ModelCollectionMembersRequest,
        user: User
    ) -> APIResponse:
        """Add models to a collection."""
        try:
            added = operations.add_members(self.repository, collection_id, request.model_ids, user.id)
            return self.success_response(data={
                "message": f"Added {added} model(s) to collection",
                "added": added
            })
        except ValueError as e:
            return self.error_api_response(error="add_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error adding model collection members: {e}")
            return self.error_api_response(error="add_members_failed", message=str(e))

    async def remove_members(
        self,
        collection_id: str,
        request: ModelCollectionMembersRequest,
        user: User
    ) -> APIResponse:
        """Remove models from a collection."""
        try:
            removed = operations.remove_members(self.repository, collection_id, request.model_ids, user.id)
            return self.success_response(data={
                "message": f"Removed {removed} model(s) from collection",
                "removed": removed
            })
        except ValueError as e:
            return self.error_api_response(error="remove_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error removing model collection members: {e}")
            return self.error_api_response(error="remove_members_failed", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.model_collection_controller
    router = APIRouter(prefix="/api/models/collections", tags=["Model Collections"])

    @router.get("", response_model=APIResponse, summary="List Model Collections")
    async def list_collections(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """List all model collections owned by the current user with model counts."""
        return await controller.list_collections(current_user)

    @router.post("", response_model=APIResponse, summary="Create Model Collection")
    async def create_collection(
        request: CreateModelCollectionRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Create a new model collection."""
        return await controller.create_collection(request, current_user)

    @router.put("/{collection_id}", response_model=APIResponse, summary="Rename Model Collection")
    async def rename_collection(
        collection_id: str,
        request: UpdateModelCollectionRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Rename a model collection."""
        return await controller.rename_collection(collection_id, request, current_user)

    @router.put("/{collection_id}/move", response_model=APIResponse, summary="Move Model Collection")
    async def move_collection(
        collection_id: str,
        request: MoveModelCollectionRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Reparent a model collection in the folder tree (parent_id null = move to root)."""
        return await controller.move_collection(collection_id, request, current_user)

    @router.post("/bulk-move", response_model=APIResponse, summary="Bulk Move Model Collections")
    async def bulk_move_collections(
        request: BulkMoveModelCollectionsRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Reparent several model collections in one call (parent_id null = move to root)."""
        return await controller.bulk_move_collections(request, current_user)

    @router.delete("/{collection_id}", response_model=APIResponse, summary="Delete Model Collection")
    async def delete_collection(
        collection_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete a model collection (cascade removes all memberships)."""
        return await controller.delete_collection(collection_id, current_user)

    @router.post("/{collection_id}/members", response_model=APIResponse, summary="Add Model Collection Members")
    async def add_members(
        collection_id: str,
        request: ModelCollectionMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Add models to a collection."""
        return await controller.add_members(collection_id, request, current_user)

    @router.delete("/{collection_id}/members", response_model=APIResponse, summary="Remove Model Collection Members")
    async def remove_members(
        collection_id: str,
        request: ModelCollectionMembersRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Remove models from a collection."""
        return await controller.remove_members(collection_id, request, current_user)

    return router
