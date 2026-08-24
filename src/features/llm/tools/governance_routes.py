"""HTTP surface for LLM tool governance: an admin config screen nested under
one LLM configuration (every registered tool + that config's {enabled,
locked}) and a user preferences screen (the global set of tools a user may
see, with their own opt-out toggle - always scoped to the caller's active LLM
config for `locked`/visibility). Reads go straight to the repository + tool
registry; writes go through ToolGovernanceManager. See
src/features/llm/tools/governance.py for the model.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import User
from src.features.llm.dto import ToolGovernanceUpdateRequest, UserToolPreferenceRequest
from src.features.llm.tools.governance import (
    ToolGovernanceManager,
    ToolGovernanceRepository,
    ToolAdminDisabledException,
    ToolLockedException,
    ToolNotFoundException,
    build_admin_toolset_listing,
    build_user_toolset_listing,
)

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.llm.repository import LLMRepository

logger = logging.getLogger(__name__)


class ToolGovernanceController(BaseController):
    def __init__(
        self,
        repository: ToolGovernanceRepository,
        manager: ToolGovernanceManager,
        tool_registry,
        llm_repository: "LLMRepository",
    ):
        super().__init__()
        self.repository = repository
        self.manager = manager
        self.tool_registry = tool_registry
        self.llm_repository = llm_repository

    def _config_exists(self, llm_config_id: str) -> bool:
        return self.llm_repository.config_repo.exists(llm_config_id)

    def get_admin_toolset(self, llm_config_id: str) -> APIResponse:
        if not self._config_exists(llm_config_id):
            return self.error_response(
                error="llm_config_not_found",
                message=f"LLM configuration '{llm_config_id}' not found",
                status_code=404,
            )
        listing = build_admin_toolset_listing(
            self.tool_registry.get_all(),
            self.repository.get_all_config(llm_config_id),
            self.tool_registry.source_of,
        )
        return self.success_response(data=listing)

    def update_admin_toolset(
        self, llm_config_id: str, tool_name: str, request: ToolGovernanceUpdateRequest
    ) -> APIResponse:
        if not self._config_exists(llm_config_id):
            return self.error_response(
                error="llm_config_not_found",
                message=f"LLM configuration '{llm_config_id}' not found",
                status_code=404,
            )
        try:
            row = self.manager.set_admin_config(
                llm_config_id, tool_name, enabled=request.enabled, locked=request.locked
            )
            return self.success_response(data={"name": tool_name, **row})
        except ToolNotFoundException:
            return self.error_response(
                error="tool_not_found", message=f"Unknown tool '{tool_name}'", status_code=404
            )

    def get_user_toolset_preferences(self, user: User, llm_config_id: str) -> APIResponse:
        listing = build_user_toolset_listing(
            self.tool_registry.get_all(),
            self.repository.get_all_config(llm_config_id),
            self.repository.get_user_disabled(user.id),
        )
        return self.success_response(data=listing)

    def update_user_toolset_preference(
        self, user: User, tool_name: str, request: UserToolPreferenceRequest
    ) -> APIResponse:
        try:
            self.manager.set_user_preference(
                user.id, tool_name, request.disabled, llm_config_id=request.llm_config_id
            )
            return self.success_response(data={"name": tool_name, "disabled_by_user": request.disabled})
        except ToolNotFoundException:
            return self.error_response(
                error="tool_not_found", message=f"Unknown tool '{tool_name}'", status_code=404
            )
        except ToolAdminDisabledException:
            return self.error_response(
                error="tool_admin_disabled",
                message=f"'{tool_name}' is disabled by an administrator",
                status_code=403,
            )
        except ToolLockedException:
            return self.error_response(
                error="tool_locked",
                message=f"'{tool_name}' is enabled for everyone by an administrator",
                status_code=409,
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.tool_governance_controller
    router = APIRouter(tags=["LLM Tool Governance"])

    # --- Admin: per-LLM-config tool governance ---

    @router.get(
        "/api/llm/configurations/{config_id}/toolset",
        response_model=APIResponse,
        summary="Get LLM Config Toolset",
    )
    async def get_admin_toolset(config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Every registered chat tool merged with this config's governance row (admin only)."""
        return controller.get_admin_toolset(config_id)

    @router.put(
        "/api/llm/configurations/{config_id}/toolset/{tool_name}",
        response_model=APIResponse,
        summary="Update LLM Config Toolset",
    )
    async def update_admin_toolset(
        config_id: str,
        tool_name: str,
        request: ToolGovernanceUpdateRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Set a tool's `enabled`/`locked` config for one LLM config (admin only)."""
        return controller.update_admin_toolset(config_id, tool_name, request)

    # --- User: global opt-out set ---

    @router.get("/api/llm/toolset/preferences", response_model=APIResponse, summary="Get My Toolset Preferences")
    async def get_user_toolset_preferences(
        llm_config_id: str,
        current_user: User = Depends(get_current_active_user),
    ):
        """The tools the current user may see, with their own opt-out state,
        scoped to `llm_config_id` (the chat composer's active session config) -
        admin-disabled tools for that config are omitted and `locked` reflects
        that config's rows."""
        return controller.get_user_toolset_preferences(current_user, llm_config_id)

    @router.put(
        "/api/llm/toolset/preferences/{tool_name}", response_model=APIResponse, summary="Update My Toolset Preference"
    )
    async def update_user_toolset_preference(
        tool_name: str,
        request: UserToolPreferenceRequest,
        current_user: User = Depends(get_current_active_user),
    ):
        """Toggle the current user's own (global) opt-out for a tool, rejected
        if `request.llm_config_id` (the caller's active config) admin-disabled
        or locked the tool."""
        return controller.update_user_toolset_preference(current_user, tool_name, request)

    return router
