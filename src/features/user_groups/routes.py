"""
User Group Controller

Handles user group CRUD operations with thin route handlers delegating to
controller methods. Business logic is in `src.features.user_groups.operations`
(formerly `UserGroupManager`).
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_user
from src.features.user_groups.dto import (
    GroupCreate,
    GroupUpdate,
    MemberIds,
    PresetIds,
    LLMConfigIds,
    ModelIds,
)
from src.features.user_groups import operations
from src.features.user_groups.operations import SystemGroupProtectedError
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins import PluginRegistry
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class UserGroupController(BaseController):
    """
    Controller for user group operations.

    Handles CRUD operations for user groups and resource assignments.
    """

    def __init__(self, user_group_repository: UserGroupRepository, plugin_registry: PluginRegistry):
        super().__init__()
        self.repository = user_group_repository
        self.plugins = plugin_registry

    # ========== Group CRUD Methods ==========

    async def get_all_groups(self, user: User) -> APIResponse:
        """Get all user groups with counts."""
        try:
            groups = operations.get_all_groups(self.repository, user)
            return self.success_response(
                data=[g.model_dump() for g in groups],
                message=f"Retrieved {len(groups)} groups"
            )
        except ValueError as e:
            return self.error_api_response(error="get_groups_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting groups: {e}")
            return self.error_api_response(error="get_groups_failed", message=str(e))

    async def create_group(self, request: GroupCreate, user: User) -> APIResponse:
        """Create a new user group."""
        try:
            group = operations.create_group(self.repository, self.plugins, request, user)
            return self.success_response(
                data=group.model_dump(),
                message="Group created successfully"
            )
        except ValueError as e:
            return self.error_api_response(error="create_group_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error creating group: {e}")
            return self.error_api_response(error="create_group_failed", message=str(e))

    async def get_group(self, group_id: str, user: User) -> APIResponse:
        """Get group details with counts."""
        try:
            group = operations.get_group(self.repository, group_id, user)
            return self.success_response(
                data=group.model_dump(),
                message="Group retrieved successfully"
            )
        except ValueError as e:
            return self.error_api_response(error="get_group_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting group: {e}")
            return self.error_api_response(error="get_group_failed", message=str(e))

    async def update_group(self, group_id: str, request: GroupUpdate, user: User) -> APIResponse:
        """Update a user group."""
        try:
            group = operations.update_group(self.repository, self.plugins, group_id, request, user)
            return self.success_response(
                data=group.model_dump(),
                message="Group updated successfully"
            )
        except ValueError as e:
            return self.error_api_response(error="update_group_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error updating group: {e}")
            return self.error_api_response(error="update_group_failed", message=str(e))

    async def delete_group(self, group_id: str, user: User) -> APIResponse:
        """Delete a user group."""
        try:
            group_name = operations.delete_group(self.repository, self.plugins, group_id, user)
            return self.success_response(
                message=f"Group '{group_name}' deleted successfully"
            )
        except SystemGroupProtectedError as e:
            # Built-in group (ALL_USERS/ALL_ADMINS) - a plain 409, not the
            # generic 400 other delete failures get here.
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            return self.error_api_response(error="delete_group_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error deleting group: {e}")
            return self.error_api_response(error="delete_group_failed", message=str(e))

    # ========== Member Methods ==========

    async def get_group_members(self, group_id: str, user: User) -> APIResponse:
        """Get all members of a group."""
        try:
            members = operations.get_group_members(self.repository, group_id, user)
            return self.success_response(
                data=[m.model_dump() for m in members],
                message=f"Retrieved {len(members)} members"
            )
        except ValueError as e:
            return self.error_api_response(error="get_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting members: {e}")
            return self.error_api_response(error="get_members_failed", message=str(e))

    async def add_members(self, group_id: str, request: MemberIds, user: User) -> APIResponse:
        """Add users to a group."""
        try:
            added = operations.add_members(self.repository, self.plugins, group_id, request.user_ids, user)
            return self.success_response(
                data=[m.model_dump() for m in added],
                message=f"Added {len(added)} members to group"
            )
        except ValueError as e:
            return self.error_api_response(error="add_members_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error adding members: {e}")
            return self.error_api_response(error="add_members_failed", message=str(e))

    async def remove_member(self, group_id: str, user_id: str, user: User) -> APIResponse:
        """Remove a user from a group."""
        try:
            operations.remove_member(self.repository, self.plugins, group_id, user_id, user)
            return self.success_response(message="Member removed from group")
        except ValueError as e:
            return self.error_api_response(error="remove_member_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error removing member: {e}")
            return self.error_api_response(error="remove_member_failed", message=str(e))

    async def get_user_groups(self, user_id: str, user: User) -> APIResponse:
        """Get all groups a user belongs to."""
        try:
            groups = operations.get_user_groups(self.repository, user_id, user)
            return self.success_response(
                data=[g.model_dump() for g in groups],
                message=f"User belongs to {len(groups)} groups"
            )
        except ValueError as e:
            return self.error_api_response(error="get_user_groups_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting user groups: {e}")
            return self.error_api_response(error="get_user_groups_failed", message=str(e))

    # ========== Preset Methods ==========

    async def get_group_presets(self, group_id: str, user: User) -> APIResponse:
        """Get all presets assigned to a group."""
        try:
            presets = operations.get_group_presets(self.repository, group_id, user)
            return self.success_response(
                data=[p.model_dump() for p in presets],
                message=f"Retrieved {len(presets)} preset assignments"
            )
        except ValueError as e:
            return self.error_api_response(error="get_group_presets_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting presets: {e}")
            return self.error_api_response(error="get_group_presets_failed", message=str(e))

    async def assign_presets(self, group_id: str, request: PresetIds, user: User) -> APIResponse:
        """Assign presets to a group."""
        try:
            assigned = operations.assign_presets(self.repository, self.plugins, group_id, request.preset_ids, user)
            return self.success_response(
                data=[p.model_dump() for p in assigned],
                message=f"Assigned {len(assigned)} presets to group"
            )
        except ValueError as e:
            return self.error_api_response(error="assign_presets_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error assigning presets: {e}")
            return self.error_api_response(error="assign_presets_failed", message=str(e))

    async def unassign_preset(self, group_id: str, preset_id: str, user: User) -> APIResponse:
        """Unassign a preset from a group."""
        try:
            operations.unassign_preset(self.repository, self.plugins, group_id, preset_id, user)
            return self.success_response(message="Preset unassigned from group")
        except ValueError as e:
            return self.error_api_response(error="unassign_preset_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error unassigning preset: {e}")
            return self.error_api_response(error="unassign_preset_failed", message=str(e))

    # ========== LLM Methods ==========

    async def get_group_llms(self, group_id: str, user: User) -> APIResponse:
        """Get all LLM configurations assigned to a group."""
        try:
            llms = operations.get_group_llms(self.repository, group_id, user)
            return self.success_response(
                data=[l.model_dump() for l in llms],
                message=f"Retrieved {len(llms)} LLM assignments"
            )
        except ValueError as e:
            return self.error_api_response(error="get_group_llms_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting LLMs: {e}")
            return self.error_api_response(error="get_group_llms_failed", message=str(e))

    async def assign_llms(self, group_id: str, request: LLMConfigIds, user: User) -> APIResponse:
        """Assign LLM configurations to a group."""
        try:
            assigned = operations.assign_llms(self.repository, self.plugins, group_id, request.llm_config_ids, user)
            return self.success_response(
                data=[l.model_dump() for l in assigned],
                message=f"Assigned {len(assigned)} LLMs to group"
            )
        except ValueError as e:
            return self.error_api_response(error="assign_llms_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error assigning LLMs: {e}")
            return self.error_api_response(error="assign_llms_failed", message=str(e))

    async def unassign_llm(self, group_id: str, llm_config_id: str, user: User) -> APIResponse:
        """Unassign an LLM configuration from a group."""
        try:
            operations.unassign_llm(self.repository, self.plugins, group_id, llm_config_id, user)
            return self.success_response(message="LLM configuration unassigned from group")
        except ValueError as e:
            return self.error_api_response(error="unassign_llm_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error unassigning LLM: {e}")
            return self.error_api_response(error="unassign_llm_failed", message=str(e))

    # ========== Model Methods ==========

    async def get_group_models(self, group_id: str, user: User) -> APIResponse:
        """Get all models assigned to a group."""
        try:
            models = operations.get_group_models(self.repository, group_id, user)
            return self.success_response(
                data=[m.model_dump() for m in models],
                message=f"Retrieved {len(models)} model assignments"
            )
        except ValueError as e:
            return self.error_api_response(error="get_group_models_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error getting models: {e}")
            return self.error_api_response(error="get_group_models_failed", message=str(e))

    async def assign_models(self, group_id: str, request: ModelIds, user: User) -> APIResponse:
        """Assign models to a group."""
        try:
            assigned = operations.assign_models(self.repository, self.plugins, group_id, request.model_ids, user)
            return self.success_response(
                data=[m.model_dump() for m in assigned],
                message=f"Assigned {len(assigned)} models to group"
            )
        except ValueError as e:
            return self.error_api_response(error="assign_models_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error assigning models: {e}")
            return self.error_api_response(error="assign_models_failed", message=str(e))

    async def unassign_model(self, group_id: str, model_id: str, user: User) -> APIResponse:
        """Unassign a model from a group."""
        try:
            operations.unassign_model(self.repository, self.plugins, group_id, model_id, user)
            return self.success_response(message="Model unassigned from group")
        except ValueError as e:
            return self.error_api_response(error="unassign_model_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error unassigning model: {e}")
            return self.error_api_response(error="unassign_model_failed", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.user_group_controller
    router = APIRouter(prefix="/api/user-groups", tags=["user-groups"])

    # Group CRUD
    @router.get("/", response_model=APIResponse, summary="List All Groups")
    async def get_all_groups(
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all user groups (admin only)."""
        return await controller.get_all_groups(current_user)

    @router.post("/", response_model=APIResponse, summary="Create Group")
    async def create_group(
        data: GroupCreate,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Create a new user group (admin only)."""
        return await controller.create_group(data, current_user)

    @router.get("/{group_id}", response_model=APIResponse, summary="Get Group")
    async def get_group(
        group_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get group details with counts (admin only)."""
        return await controller.get_group(group_id, current_user)

    @router.put("/{group_id}", response_model=APIResponse, summary="Update Group")
    async def update_group(
        group_id: str,
        data: GroupUpdate,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Update a user group (admin only)."""
        return await controller.update_group(group_id, data, current_user)

    @router.delete("/{group_id}", response_model=APIResponse, summary="Delete Group")
    async def delete_group(
        group_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Delete a user group (admin only, cascades memberships and assignments).

        409 if the group is built-in (All Users / All Admins)."""
        return await controller.delete_group(group_id, current_user)

    # Members
    @router.get("/{group_id}/members", response_model=APIResponse, summary="Get Group Members")
    async def get_group_members(
        group_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all members of a group (admin only)."""
        return await controller.get_group_members(group_id, current_user)

    @router.post("/{group_id}/members", response_model=APIResponse, summary="Add Members")
    async def add_members(
        group_id: str,
        data: MemberIds,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Add users to a group (admin only)."""
        return await controller.add_members(group_id, data, current_user)

    @router.delete("/{group_id}/members/{user_id}", response_model=APIResponse, summary="Remove Member")
    async def remove_member(
        group_id: str,
        user_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Remove a user from a group (admin only)."""
        return await controller.remove_member(group_id, user_id, current_user)

    # User's groups
    @router.get("/user/{user_id}", response_model=APIResponse, summary="Get User's Groups")
    async def get_user_groups(
        user_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all groups a user belongs to (admin only)."""
        return await controller.get_user_groups(user_id, current_user)

    # Presets
    @router.get("/{group_id}/presets", response_model=APIResponse, summary="Get Group Presets")
    async def get_group_presets(
        group_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all presets assigned to a group (admin only)."""
        return await controller.get_group_presets(group_id, current_user)

    @router.post("/{group_id}/presets", response_model=APIResponse, summary="Assign Presets")
    async def assign_presets(
        group_id: str,
        data: PresetIds,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Assign presets to a group (admin only)."""
        return await controller.assign_presets(group_id, data, current_user)

    @router.delete("/{group_id}/presets/{preset_id}", response_model=APIResponse, summary="Unassign Preset")
    async def unassign_preset(
        group_id: str,
        preset_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Unassign a preset from a group (admin only)."""
        return await controller.unassign_preset(group_id, preset_id, current_user)

    # LLMs
    @router.get("/{group_id}/llms", response_model=APIResponse, summary="Get Group LLMs")
    async def get_group_llms(
        group_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all LLM configurations assigned to a group (admin only)."""
        return await controller.get_group_llms(group_id, current_user)

    @router.post("/{group_id}/llms", response_model=APIResponse, summary="Assign LLMs")
    async def assign_llms(
        group_id: str,
        data: LLMConfigIds,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Assign LLM configurations to a group (admin only)."""
        return await controller.assign_llms(group_id, data, current_user)

    @router.delete("/{group_id}/llms/{llm_config_id}", response_model=APIResponse, summary="Unassign LLM")
    async def unassign_llm(
        group_id: str,
        llm_config_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Unassign an LLM configuration from a group (admin only)."""
        return await controller.unassign_llm(group_id, llm_config_id, current_user)

    # Models
    @router.get("/{group_id}/models", response_model=APIResponse, summary="Get Group Models")
    async def get_group_models(
        group_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all models assigned to a group (admin only)."""
        return await controller.get_group_models(group_id, current_user)

    @router.post("/{group_id}/models", response_model=APIResponse, summary="Assign Models")
    async def assign_models(
        group_id: str,
        data: ModelIds,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Assign models to a group (admin only)."""
        return await controller.assign_models(group_id, data, current_user)

    @router.delete("/{group_id}/models/{model_id}", response_model=APIResponse, summary="Unassign Model")
    async def unassign_model(
        group_id: str,
        model_id: str,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Unassign a model from a group (admin only)."""
        return await controller.unassign_model(group_id, model_id, current_user)

    return router
