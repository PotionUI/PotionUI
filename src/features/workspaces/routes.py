"""
Workspace Controller

Handles CRUD operations for user workspaces (saved tab layout configurations).
Delegates mutations to `src.features.workspaces.operations`.
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.errors import AccessDeniedError, NotFoundError
from src.platform.security.api_scopes import ApiScope, key_access
from src.platform.security.current_user import get_current_active_user
from src.features.workspaces.dto import SaveWorkspaceRequest, UpdateWorkspaceRequest
from src.features.workspaces import operations
from src.features.workspaces.mappers import workspace_to_response
from src.features.workspaces.repository import WorkspaceRepository

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class WorkspaceController(BaseController):

    def __init__(self, workspace_repository: WorkspaceRepository):
        super().__init__()
        self.repository = workspace_repository

    async def get_workspaces(self, user_id: str) -> APIResponse:
        def load():
            return [workspace_to_response(w).model_dump() for w in self.repository.get_by_user(user_id)]
        return await self.run("get_workspaces_failed", load, soft=True, message="Failed to get workspaces")

    async def get_workspace_by_id(self, workspace_id: str, user_id: str) -> APIResponse:
        def load():
            workspace = self.repository.get_by_id(workspace_id)
            if not workspace:
                raise NotFoundError("workspace_not_found", "Workspace not found")
            if workspace.user_id != user_id:
                raise AccessDeniedError("workspace_access_denied", "Access denied to this workspace")
            return workspace_to_response(workspace).model_dump()
        return await self.run("get_workspace_failed", load, soft=True, message="Failed to get workspace")

    async def save_workspace(self, user_id: str, request: SaveWorkspaceRequest) -> APIResponse:
        response = await self.run(
            "save_workspace_failed", operations.save_workspace, self.repository, user_id, request,
            soft=True, message="Failed to save workspace",
        )
        if response.success:
            response.data = response.data.model_dump()
            response.message = f"Workspace '{request.name}' saved successfully"
        return response

    async def update_workspace(
        self,
        workspace_id: str,
        user_id: str,
        request: UpdateWorkspaceRequest
    ) -> APIResponse:
        response = await self.run(
            "update_workspace_failed", operations.update_workspace, self.repository, workspace_id, user_id, request,
            soft=True, message="Failed to update workspace",
        )
        if response.success:
            response.data = response.data.model_dump()
            response.message = "Workspace updated successfully"
        return response

    async def delete_workspace(self, workspace_id: str, user_id: str) -> APIResponse:
        response = await self.run(
            "delete_workspace_failed", operations.delete_workspace, self.repository, workspace_id, user_id,
            soft=True, message="Failed to delete workspace",
        )
        if response.success:
            response.message, response.data = response.data, None
        return response


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.workspace_controller
    router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("", response_model=APIResponse, summary="Get Workspaces")
    async def get_workspaces(current_user=Depends(get_current_active_user)):
        """Get all workspaces for the current user."""
        return await controller.get_workspaces(current_user.id)

    @router.get("/{workspace_id}", response_model=APIResponse, summary="Get Workspace by ID")
    async def get_workspace_by_id(workspace_id: str, current_user=Depends(get_current_active_user)):
        """Get a specific workspace by ID."""
        return await controller.get_workspace_by_id(workspace_id, current_user.id)

    @router.post("", response_model=APIResponse, summary="Save Workspace")
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
