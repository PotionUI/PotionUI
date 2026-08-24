"""
Session DTOs for API requests and responses.

These Pydantic models define the contract between the API layer and clients.
They are used throughout the application as the single model type (no separate dataclass models).
"""
from typing import Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class Session(BaseModel):
    """Session model used throughout the application."""
    id: str
    user_id: str
    preset_id: str
    name: str
    data: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None


class SessionResponse(BaseModel):
    """Response model for session (excludes user_id for security)."""
    id: str
    preset_id: str
    name: str
    data: Dict[str, Any]
    created_at: str
    updated_at: str


class SaveSessionRequest(BaseModel):
    """Request model for creating/updating session by name."""
    preset_id: str
    name: str
    data: Dict[str, Any]
    mode: Optional[str] = None  # Optional mode to specify which mode data is being updated


class UpdateSessionRequest(BaseModel):
    """Request model for updating session by ID."""
    name: str
    data: Dict[str, Any]
    mode: Optional[str] = None  # Optional mode to specify which mode data is being updated


# A `SessionVersion` is an immutable snapshot appended every time a session is
# saved; the `sessions` row itself remains the "current" state, unchanged.

class SessionVersion(BaseModel):
    """Full version record, including the payload snapshot (internal + detail response)."""
    id: str
    session_id: str
    version_number: int
    data: Dict[str, Any]
    summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class SessionVersionSummaryResponse(BaseModel):
    """List-endpoint entry — no payload, cheap to return in bulk."""
    version_number: int
    created_at: str
    summary: Optional[str] = None


class SessionVersionDetailResponse(BaseModel):
    """Single-version-endpoint response — includes the full payload."""
    version_number: int
    created_at: str
    summary: Optional[str] = None
    data: Dict[str, Any]
