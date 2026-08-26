"""
Tag Controller

Handles tag CRUD operations with thin route handlers delegating to controller methods.
Mutations delegate to `src.features.tags.operations`.
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Query, Depends, HTTPException

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.repository import DatabasePresetRepository
from src.features.tags import operations
from src.features.tags.dto import CreateTagRequest, UpdateTagRequest, TagType, USER_SCOPED_TAG_TYPES, effective_user_id_for_type
from src.features.tags.errors import TagInUseByPresetError
from src.features.tags.repository import TagRepository
from src.platform.plugins import PluginRegistry
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class TagController(BaseController):
    """
    Controller for tag operations.

    Handles CRUD operations for tags (MODEL and GENERATION types), delegating
    mutations to `src.features.tags.operations`.
    """

    def __init__(
        self,
        tag_repository: TagRepository,
        plugin_registry: PluginRegistry,
        database_preset_repository: DatabasePresetRepository,
        file_preset_repository: FilePresetRepository,
    ):
        super().__init__()
        self.repository = tag_repository
        self.plugins = plugin_registry
        self.preset_repository = database_preset_repository
        self.file_preset_repository = file_preset_repository

    # ========== List/Search Methods ==========

    async def list_tags(self, tag_type: TagType, user: User) -> APIResponse:
        """List all tags of specified type with usage counts. Pure DB read."""
        try:
            effective_user_id = effective_user_id_for_type(tag_type, user.id)
            tags = self.repository.get_tags_with_counts(type=tag_type.value, user_id=effective_user_id)
            return self.success_response(data={
                "tags": [tag.model_dump() for tag in tags],
                "total": len(tags)
            })
        except ValueError as e:
            return self.error_api_response(error="list_tags_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error listing tags: {e}")
            return self.error_api_response(error="list_tags_failed", message=str(e))

    async def search_tags(
        self,
        query: str,
        tag_type: TagType,
        limit: int,
        user: User
    ) -> APIResponse:
        """Search tags by name. Pure DB read."""
        try:
            effective_user_id = effective_user_id_for_type(tag_type, user.id)
            tags = self.repository.search_tags(query=query, type=tag_type.value, user_id=effective_user_id, limit=limit)
            return self.success_response(data={
                "tags": [tag.model_dump() for tag in tags]
            })
        except ValueError as e:
            return self.error_api_response(error="search_tags_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error searching tags: {e}")
            return self.error_api_response(error="search_tags_failed", message=str(e))

    def _get_owned_tag(self, tag_id: str, user_id: str):
        """Look up a tag by id and enforce ownership for user-scoped types.
        Pure DB read (mirrors the ownership check `operations.update_tag`/
        `delete_tag` also make before mutating)."""
        tag = self.repository.get_tag_by_id(tag_id)
        if not tag:
            raise ValueError("Tag not found")
        if tag.type in USER_SCOPED_TAG_TYPES and tag.user_id != user_id:
            raise ValueError("Tag not found or access denied")
        return tag

    # ========== CRUD Methods ==========

    async def create_tag(self, request: CreateTagRequest, user: User) -> APIResponse:
        """Create a new tag."""
        try:
            tag = operations.create_tag(
                self.repository, self.plugins, request, user.id, is_admin=user.account_type == AccountType.ADMIN
            )
            return self.success_response(data={
                "message": f"Tag '{tag.name}' created successfully",
                "tag": tag.model_dump()
            })
        except ValueError as e:
            return self.error_api_response(error="create_tag_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error creating tag: {e}")
            return self.error_api_response(error="create_tag_failed", message=str(e))

    async def update_tag(
        self,
        tag_id: str,
        request: UpdateTagRequest,
        user: User
    ) -> APIResponse:
        """Update a tag's name."""
        try:
            tag = operations.update_tag(
                self.repository, self.plugins, tag_id, request, user.id, is_admin=user.account_type == AccountType.ADMIN
            )
            return self.success_response(data={
                "message": "Tag updated successfully",
                "tag": tag.model_dump()
            })
        except ValueError as e:
            return self.error_api_response(error="update_tag_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error updating tag: {e}")
            return self.error_api_response(error="update_tag_failed", message=str(e))

    async def delete_tag(self, tag_id: str, user: User) -> APIResponse:
        """Delete a tag (cascade removes all associations)."""
        try:
            # Get tag name before deletion for the message
            tag = self._get_owned_tag(tag_id, user.id)
            tag_name = tag.name

            operations.delete_tag(
                self.repository, self.plugins, self.preset_repository, self.file_preset_repository,
                tag_id, user.id, is_admin=user.account_type == AccountType.ADMIN
            )
            return self.success_response(data={
                "message": f"Tag '{tag_name}' deleted successfully"
            })
        except TagInUseByPresetError as e:
            # Fixed contract with the frontend - see docs/presets.md
            # "Configuration (admin-set)": no force flag, the admin must unset the
            # tag from the preset's configuration first.
            raise HTTPException(
                status_code=409,
                detail={"error": "tag_in_use_by_preset", "used_by": e.used_by},
            )
        except ValueError as e:
            return self.error_api_response(error="delete_tag_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error deleting tag: {e}")
            return self.error_api_response(error="delete_tag_failed", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.tag_controller
    router = APIRouter(prefix="/api/tags", tags=["Tags"])

    @router.get("/", response_model=APIResponse, summary="List Tags")
    async def list_tags(
        type: TagType = Query(..., description="Tag type: MODEL or GENERATION"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """List all tags of specified type with usage counts."""
        return await controller.list_tags(type, current_user)

    @router.post("/", response_model=APIResponse, summary="Create Tag")
    async def create_tag(
        request: CreateTagRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Create a new tag."""
        return await controller.create_tag(request, current_user)

    @router.get("/search", response_model=APIResponse, summary="Search Tags")
    async def search_tags(
        q: str = Query(..., description="Search query"),
        type: TagType = Query(..., description="Tag type: MODEL or GENERATION"),
        limit: int = Query(10, description="Max results"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Search tags for autocomplete."""
        return await controller.search_tags(q, type, limit, current_user)

    @router.put("/{tag_id}", response_model=APIResponse, summary="Update Tag")
    async def update_tag(
        tag_id: str,
        request: UpdateTagRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Update tag name."""
        return await controller.update_tag(tag_id, request, current_user)

    @router.delete("/{tag_id}", response_model=APIResponse, summary="Delete Tag")
    async def delete_tag(
        tag_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete tag (cascade removes all associations)."""
        return await controller.delete_tag(tag_id, current_user)

    return router
