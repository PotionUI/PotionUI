"""
Response mappers for the workspaces feature.

Plain functions that turn Workspace records into their API response DTOs.
No class, no state.
"""
from src.features.workspaces.dto import WorkspaceResponse
from src.features.workspaces.records import Workspace


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
        created_at=workspace.created_at.isoformat() if workspace.created_at else None,
        updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None
    )
