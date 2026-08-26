"""
Workspace administration operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the collaborators it needs (workspace_repository)
as leading arguments, followed by the operation's own parameters.
`WorkspaceController` (`routes.py`) holds the collaborator and passes it in;
nothing here is stored across calls.

One concern (workspace CRUD), small enough for a single module - split it out
before it outgrows ~200 lines rather than let a second concern move in here.
"""
import logging

from src.platform.util.ids import generate_ulid
from src.features.workspaces.dto import (
    SaveWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkspaceResponse,
)
from src.features.workspaces.mappers import workspace_to_response
from src.features.workspaces.records import Workspace
from src.features.workspaces.repository import WorkspaceRepository

logger = logging.getLogger(__name__)


def save_workspace(
    workspace_repository: WorkspaceRepository,
    user_id: str,
    request: SaveWorkspaceRequest,
) -> WorkspaceResponse:
    """Save a new workspace."""
    workspace = Workspace(
        id=generate_ulid(),
        user_id=user_id,
        name=request.name,
        data=request.data,
    )

    created_workspace = workspace_repository.create(workspace)

    logger.info(f"Workspace created: {created_workspace.name} (id: {created_workspace.id})")
    return workspace_to_response(created_workspace)


def update_workspace(
    workspace_repository: WorkspaceRepository,
    workspace_id: str,
    user_id: str,
    request: UpdateWorkspaceRequest,
) -> WorkspaceResponse:
    """Update an existing workspace by ID.

    Raises:
        ValueError: If workspace not found or access denied
    """
    workspace = workspace_repository.get_by_id(workspace_id)

    if not workspace:
        raise ValueError("Workspace not found")

    if workspace.user_id != user_id:
        raise ValueError("Access denied to this workspace")

    updated_name = request.name if request.name is not None else workspace.name
    updated_data = request.data if request.data is not None else workspace.data

    updated_workspace = Workspace(
        id=workspace.id,
        user_id=workspace.user_id,
        name=updated_name,
        data=updated_data,
        created_at=workspace.created_at,
    )

    result = workspace_repository.update(updated_workspace)

    logger.info(f"Workspace updated: {result.name} (id: {result.id})")
    return workspace_to_response(result)


def delete_workspace(
    workspace_repository: WorkspaceRepository,
    workspace_id: str,
    user_id: str,
) -> str:
    """Delete a workspace.

    Raises:
        ValueError: If workspace not found or access denied
    """
    workspace = workspace_repository.get_by_id(workspace_id)

    if not workspace:
        raise ValueError("Workspace not found")

    if workspace.user_id != user_id:
        raise ValueError("Access denied to this workspace")

    workspace_name = workspace.name
    success = workspace_repository.delete(workspace_id)

    if not success:
        raise ValueError("Failed to delete workspace")

    logger.info(f"Workspace deleted: {workspace_name} (id: {workspace_id})")
    return f"Workspace '{workspace_name}' deleted successfully"
