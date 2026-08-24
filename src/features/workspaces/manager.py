"""
Workspace domain manager.

Handles all business logic for user workspaces (saved tab layout configurations).
Framework-agnostic - uses ValueError for errors (controller converts to HTTP responses).
"""
import logging
from typing import Dict, Any, List, Optional


from src.platform.util.ids import generate_ulid
from src.features.workspaces.dto import (
    SaveWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceResponse,
)
from src.features.workspaces.records import Workspace
from src.features.workspaces.repository import WorkspaceRepository
from src.platform.plugins import PluginRegistry

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """
    Coordinates workspace operations.

    Handles CRUD for workspaces.
    Workspaces store user-specific tab layout configurations (names, colors, order, preset/mode per tab).
    """

    def __init__(
        self,
        workspace_repository: WorkspaceRepository,
        plugin_registry: PluginRegistry
    ):
        self.repository = workspace_repository
        self.plugins = plugin_registry

    def _workspace_to_response(self, workspace: Workspace) -> WorkspaceResponse:
        """
        Convert workspace model to response DTO (excludes user_id for security).

        Args:
            workspace: Workspace model

        Returns:
            WorkspaceResponse DTO
        """
        return WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            data=workspace.data,
            created_at=workspace.created_at.isoformat() if workspace.created_at else None,
            updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None
        )

    # ========== Read Operations ==========

    def get_workspaces(self, user_id: str) -> List[WorkspaceResponse]:
        """
        Get all workspaces for a user.

        Args:
            user_id: The user's ID

        Returns:
            List of WorkspaceResponse DTOs
        """
        workspaces = self.repository.get_by_user(user_id)
        return [self._workspace_to_response(w) for w in workspaces]

    def get_workspace_by_id(self, workspace_id: str, user_id: str) -> WorkspaceResponse:
        """
        Get a specific workspace by ID with ownership validation.

        Args:
            workspace_id: The workspace's ID
            user_id: The user's ID (for ownership check)

        Returns:
            WorkspaceResponse DTO

        Raises:
            ValueError: If workspace not found or access denied
        """
        workspace = self.repository.get_by_id(workspace_id)

        if not workspace:
            raise ValueError("Workspace not found")

        if workspace.user_id != user_id:
            raise ValueError("Access denied to this workspace")

        return self._workspace_to_response(workspace)

    # ========== Create/Update Operations ==========

    def save_workspace(
        self,
        user_id: str,
        request: SaveWorkspaceRequest
    ) -> WorkspaceResponse:
        """
        Save a new workspace.

        Args:
            user_id: The user's ID
            request: Save workspace request

        Returns:
            Created WorkspaceResponse DTO
        """
        workspace = Workspace(
            id=generate_ulid(),
            user_id=user_id,
            name=request.name,
            data=request.data
        )

        created_workspace = self.repository.create(workspace)

        logger.info(f"Workspace created: {created_workspace.name} (id: {created_workspace.id})")
        return self._workspace_to_response(created_workspace)

    def update_workspace(
        self,
        workspace_id: str,
        user_id: str,
        request: UpdateWorkspaceRequest
    ) -> WorkspaceResponse:
        """
        Update an existing workspace by ID.

        Args:
            workspace_id: The workspace's ID
            user_id: The user's ID
            request: Update workspace request

        Returns:
            Updated WorkspaceResponse DTO

        Raises:
            ValueError: If workspace not found or access denied
        """
        workspace = self.repository.get_by_id(workspace_id)

        if not workspace:
            raise ValueError("Workspace not found")

        if workspace.user_id != user_id:
            raise ValueError("Access denied to this workspace")

        # Apply partial updates - only update fields that are provided
        updated_name = request.name if request.name is not None else workspace.name
        updated_data = request.data if request.data is not None else workspace.data

        updated_workspace = Workspace(
            id=workspace.id,
            user_id=workspace.user_id,
            name=updated_name,
            data=updated_data,
            created_at=workspace.created_at
        )

        result = self.repository.update(updated_workspace)

        logger.info(f"Workspace updated: {result.name} (id: {result.id})")
        return self._workspace_to_response(result)

    # ========== Delete Operations ==========

    def delete_workspace(self, workspace_id: str, user_id: str) -> str:
        """
        Delete a workspace.

        Args:
            workspace_id: The workspace's ID
            user_id: The user's ID

        Returns:
            Success message

        Raises:
            ValueError: If workspace not found or access denied
        """
        workspace = self.repository.get_by_id(workspace_id)

        if not workspace:
            raise ValueError("Workspace not found")

        if workspace.user_id != user_id:
            raise ValueError("Access denied to this workspace")

        workspace_name = workspace.name
        success = self.repository.delete(workspace_id)

        if not success:
            raise ValueError("Failed to delete workspace")

        logger.info(f"Workspace deleted: {workspace_name} (id: {workspace_id})")
        return f"Workspace '{workspace_name}' deleted successfully"
