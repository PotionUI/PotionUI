"""
User Group Controller

Handles user group CRUD operations with thin route handlers delegating to
controller methods. Business logic is in `src.features.user_groups.operations`
(formerly `UserGroupManager`).
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.api_scopes import ApiScope, key_access
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

    @guarded({ValueError: Fail("get_groups_failed")}, Fail("get_groups_failed"))
    async def get_all_groups(self, user: User) -> APIResponse:
        """Get all user groups with counts."""
        groups = operations.get_all_groups(self.repository, user)
        return self.success_response(
            data=[g.model_dump() for g in groups],
            message=f"Retrieved {len(groups)} groups"
        )

    @guarded({ValueError: Fail("create_group_failed")}, Fail("create_group_failed"))
    async def create_group(self, request: GroupCreate, user: User) -> APIResponse:
        """Create a new user group."""
        group = operations.create_group(self.repository, self.plugins, request, user)
        return self.success_response(
            data=group.model_dump(),
            message="Group created successfully"
        )

    @guarded({ValueError: Fail("get_group_failed")}, Fail("get_group_failed"))
    async def get_group(self, group_id: str, user: User) -> APIResponse:
        """Get group details with counts."""
        group = operations.get_group(self.repository, group_id, user)
        return self.success_response(
            data=group.model_dump(),
            message="Group retrieved successfully"
        )

    @guarded({ValueError: Fail("update_group_failed")}, Fail("update_group_failed"))
    async def update_group(self, group_id: str, request: GroupUpdate, user: User) -> APIResponse:
        """Update a user group."""
        group = operations.update_group(self.repository, self.plugins, group_id, request, user)
        return self.success_response(
            data=group.model_dump(),
            message="Group updated successfully"
        )

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

    @guarded({ValueError: Fail("get_members_failed")}, Fail("get_members_failed"))
    async def get_group_members(self, group_id: str, user: User) -> APIResponse:
        """Get all members of a group."""
        members = operations.get_group_members(self.repository, group_id, user)
        return self.success_response(
            data=[m.model_dump() for m in members],
            message=f"Retrieved {len(members)} members"
        )

    @guarded({ValueError: Fail("add_members_failed")}, Fail("add_members_failed"))
    async def add_members(self, group_id: str, request: MemberIds, user: User) -> APIResponse:
        """Add users to a group."""
        added = operations.add_members(self.repository, self.plugins, group_id, request.user_ids, user)
        return self.success_response(
            data=[m.model_dump() for m in added],
            message=f"Added {len(added)} members to group"
        )

    @guarded({ValueError: Fail("remove_member_failed")}, Fail("remove_member_failed"))
    async def remove_member(self, group_id: str, user_id: str, user: User) -> APIResponse:
        """Remove a user from a group."""
        operations.remove_member(self.repository, self.plugins, group_id, user_id, user)
        return self.success_response(message="Member removed from group")

    @guarded({ValueError: Fail("get_user_groups_failed")}, Fail("get_user_groups_failed"))
    async def get_user_groups(self, user_id: str, user: User) -> APIResponse:
        """Get all groups a user belongs to."""
        groups = operations.get_user_groups(self.repository, user_id, user)
        return self.success_response(
            data=[g.model_dump() for g in groups],
            message=f"User belongs to {len(groups)} groups"
        )

    # ========== Preset Methods ==========

    @guarded({ValueError: Fail("get_group_presets_failed")}, Fail("get_group_presets_failed"))
    async def get_group_presets(self, group_id: str, user: User) -> APIResponse:
        """Get all presets assigned to a group."""
        presets = operations.get_group_presets(self.repository, group_id, user)
        return self.success_response(
            data=[p.model_dump() for p in presets],
            message=f"Retrieved {len(presets)} preset assignments"
        )

    @guarded({ValueError: Fail("assign_presets_failed")}, Fail("assign_presets_failed"))
    async def assign_presets(self, group_id: str, request: PresetIds, user: User) -> APIResponse:
        """Assign presets to a group."""
        assigned = operations.assign_presets(self.repository, self.plugins, group_id, request.preset_ids, user)
        return self.success_response(
            data=[p.model_dump() for p in assigned],
            message=f"Assigned {len(assigned)} presets to group"
        )

    @guarded({ValueError: Fail("unassign_preset_failed")}, Fail("unassign_preset_failed"))
    async def unassign_preset(self, group_id: str, preset_id: str, user: User) -> APIResponse:
        """Unassign a preset from a group."""
        operations.unassign_preset(self.repository, self.plugins, group_id, preset_id, user)
        return self.success_response(message="Preset unassigned from group")

    # ========== LLM Methods ==========

    @guarded({ValueError: Fail("get_group_llms_failed")}, Fail("get_group_llms_failed"))
    async def get_group_llms(self, group_id: str, user: User) -> APIResponse:
        """Get all LLM configurations assigned to a group."""
        llms = operations.get_group_llms(self.repository, group_id, user)
        return self.success_response(
            data=[l.model_dump() for l in llms],
            message=f"Retrieved {len(llms)} LLM assignments"
        )

    @guarded({ValueError: Fail("assign_llms_failed")}, Fail("assign_llms_failed"))
    async def assign_llms(self, group_id: str, request: LLMConfigIds, user: User) -> APIResponse:
        """Assign LLM configurations to a group."""
        assigned = operations.assign_llms(self.repository, self.plugins, group_id, request.llm_config_ids, user)
        return self.success_response(
            data=[l.model_dump() for l in assigned],
            message=f"Assigned {len(assigned)} LLMs to group"
        )

    @guarded({ValueError: Fail("unassign_llm_failed")}, Fail("unassign_llm_failed"))
    async def unassign_llm(self, group_id: str, llm_config_id: str, user: User) -> APIResponse:
        """Unassign an LLM configuration from a group."""
        operations.unassign_llm(self.repository, self.plugins, group_id, llm_config_id, user)
        return self.success_response(message="LLM configuration unassigned from group")

    # ========== Model Methods ==========

    @guarded({ValueError: Fail("get_group_models_failed")}, Fail("get_group_models_failed"))
    async def get_group_models(self, group_id: str, user: User) -> APIResponse:
        """Get all models assigned to a group."""
        models = operations.get_group_models(self.repository, group_id, user)
        return self.success_response(
            data=[m.model_dump() for m in models],
            message=f"Retrieved {len(models)} model assignments"
        )

    @guarded({ValueError: Fail("assign_models_failed")}, Fail("assign_models_failed"))
    async def assign_models(self, group_id: str, request: ModelIds, user: User) -> APIResponse:
        """Assign models to a group."""
        assigned = operations.assign_models(self.repository, self.plugins, group_id, request.model_ids, user)
        return self.success_response(
            data=[m.model_dump() for m in assigned],
            message=f"Assigned {len(assigned)} models to group"
        )

    @guarded({ValueError: Fail("unassign_model_failed")}, Fail("unassign_model_failed"))
    async def unassign_model(self, group_id: str, model_id: str, user: User) -> APIResponse:
        """Unassign a model from a group."""
        operations.unassign_model(self.repository, self.plugins, group_id, model_id, user)
        return self.success_response(message="Model unassigned from group")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.user_group_controller
    router = APIRouter(prefix="/api/user-groups", tags=["user-groups"], dependencies=[key_access(read=ApiScope.ADMIN, write=ApiScope.ADMIN)])

    # Group CRUD
    @router.get("", response_model=APIResponse, summary="List All Groups")
    async def get_all_groups(
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get all user groups (admin only)."""
        return await controller.get_all_groups(current_user)

    @router.post("", response_model=APIResponse, summary="Create Group")
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
