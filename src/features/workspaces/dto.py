"""
Workspace DTOs for API requests and responses.

These Pydantic models define the contract between the API layer and clients.
"""
from typing import Dict, Any, Optional, List
from pydantic import BaseModel


class WorkspaceTabData(BaseModel):
    """Data model for a single tab within a workspace."""
    name: str
    color: Optional[str] = None
    preset_id: Optional[str] = None
    mode: Optional[str] = None
    autoTagIds: Optional[List[str]] = None


class SaveWorkspaceRequest(BaseModel):
    """Request model for creating a new workspace."""
    name: str
    data: Dict[str, Any]


class UpdateWorkspaceRequest(BaseModel):
    """Request model for updating an existing workspace."""
    name: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class WorkspaceResponse(BaseModel):
    """Response model for workspace (excludes user_id for security)."""
    id: str
    name: str
    data: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
