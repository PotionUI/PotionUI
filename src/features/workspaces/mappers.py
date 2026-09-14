"""
Response mappers for the workspaces feature.

Plain functions that turn Workspace records into their API response DTOs.
No class, no state.
"""
from src.features.workspaces.dto import WorkspaceResponse
from src.features.workspaces.records import Workspace
from src.platform.database.rows import dt_iso


def workspace_to_response(workspace: Workspace) -> WorkspaceResponse:
    """
    Convert a Workspace model to WorkspaceResponse (excludes user_id for security).

    Args:
        workspace: Workspace model

    Returns:
        WorkspaceResponse DTO
    """
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        data=workspace.data,
        created_at=dt_iso(workspace.created_at),
        updated_at=dt_iso(workspace.updated_at)
    )
