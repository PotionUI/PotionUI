"""
Workspace Controller

Handles CRUD operations for user workspaces (saved tab layout configurations).
Delegates business logic to WorkspaceManager.
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.workspaces.dto import SaveWorkspaceRequest, UpdateWorkspaceRequest
from src.features.workspaces import WorkspaceManager

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class WorkspaceController(BaseController):
    """Controller for managing user workspaces."""

    def __init__(self, workspace_manager: WorkspaceManager):
        super().__init__()
        self.manager = workspace_manager

    async def get_workspaces(self, user_id: str) -> APIResponse:
        """Get all workspaces for the current user."""
        try:
            workspaces = self.manager.get_workspaces(user_id)
            return self.success_response(data=[w.model_dump() for w in workspaces])
        except Exception as e:
            return self.error_api_response(
                error="get_workspaces_failed",
                message=f"Failed to get workspaces: {str(e)}"
            )

    async def get_workspace_by_id(self, workspace_id: str, user_id: str) -> APIResponse:
        """Get a specific workspace by ID."""
        try:
            workspace = self.manager.get_workspace_by_id(workspace_id, user_id)
            return self.success_response(data=workspace.model_dump())
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_api_response(
                    error="workspace_not_found",
                    message=error_msg
                )
            elif "access denied" in error_msg.lower():
                return self.error_api_response(
                    error="workspace_access_denied",
                    message=error_msg
                )
            return self.error_api_response(error="get_workspace_failed", message=error_msg)
        except Exception as e:
            return self.error_api_response(
                error="get_workspace_failed",
                message=f"Failed to get workspace: {str(e)}"
            )

    async def save_workspace(self, user_id: str, request: SaveWorkspaceRequest) -> APIResponse:
        """Save a new workspace."""
        try:
            workspace = self.manager.save_workspace(user_id, request)
            return self.success_response(
                data=workspace.model_dump(),
                message=f"Workspace '{request.name}' saved successfully"
            )
        except ValueError as e:
            return self.error_api_response(error="save_workspace_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(
                error="save_workspace_failed",
                message=f"Failed to save workspace: {str(e)}"
            )

    async def update_workspace(
        self,
        workspace_id: str,
        user_id: str,
        request: UpdateWorkspaceRequest
    ) -> APIResponse:
        """Update an existing workspace."""
        try:
            workspace = self.manager.update_workspace(workspace_id, user_id, request)
            return self.success_response(
                data=workspace.model_dump(),
                message="Workspace updated successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_api_response(
                    error="workspace_not_found",
                    message=error_msg
                )
            elif "access denied" in error_msg.lower():
                return self.error_api_response(
                    error="workspace_access_denied",
                    message=error_msg
                )
            return self.error_api_response(error="update_workspace_failed", message=error_msg)
        except Exception as e:
            return self.error_api_response(
                error="update_workspace_failed",
                message=f"Failed to update workspace: {str(e)}"
            )

    async def delete_workspace(self, workspace_id: str, user_id: str) -> APIResponse:
        """Delete a workspace."""
        try:
            message = self.manager.delete_workspace(workspace_id, user_id)
            return self.success_response(message=message)
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_api_response(
                    error="workspace_not_found",
                    message=error_msg
                )
            elif "access denied" in error_msg.lower():
                return self.error_api_response(
                    error="workspace_access_denied",
                    message=error_msg
                )
            return self.error_api_response(error="delete_workspace_failed", message=error_msg)
        except Exception as e:
            return self.error_api_response(
                error="delete_workspace_failed",
                message=f"Failed to delete workspace: {str(e)}"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.workspace_controller
    router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"])

    @router.get("/", response_model=APIResponse, summary="Get Workspaces")
    async def get_workspaces(current_user=Depends(get_current_active_user)):
        """Get all workspaces for the current user."""
        return await controller.get_workspaces(current_user.id)

    @router.get("/{workspace_id}", response_model=APIResponse, summary="Get Workspace by ID")
    async def get_workspace_by_id(workspace_id: str, current_user=Depends(get_current_active_user)):
        """Get a specific workspace by ID."""
        return await controller.get_workspace_by_id(workspace_id, current_user.id)

    @router.post("/", response_model=APIResponse, summary="Save Workspace")
    async def save_workspace(request: SaveWorkspaceRequest, current_user=Depends(get_current_active_user)):
        """Save a new workspace."""
        return await controller.save_workspace(current_user.id, request)

    @router.put("/{workspace_id}", response_model=APIResponse, summary="Update Workspace")
    async def update_workspace(
        workspace_id: str,
        request: UpdateWorkspaceRequest,
        current_user=Depends(get_current_active_user)
    ):
        """Update an existing workspace."""
        return await controller.update_workspace(workspace_id, current_user.id, request)

    @router.delete("/{workspace_id}", response_model=APIResponse, summary="Delete Workspace")
    async def delete_workspace(workspace_id: str, current_user=Depends(get_current_active_user)):
        """Delete a workspace."""
        return await controller.delete_workspace(workspace_id, current_user.id)

    return router
