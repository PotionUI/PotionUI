"""
Session DTOs for API requests and responses.

These Pydantic models define the contract between the API layer and clients.
They are used throughout the application as the single model type (no separate dataclass models).
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class Session(BaseModel):
    """Session model used throughout the application."""
    id: str
    user_id: str
    preset_id: str
    name: str
    data: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    pinned: bool = False
    pinned_at: Optional[datetime] = None
    version: int = 1
    last_origin: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    """Response model for session (excludes user_id for security)."""
    id: str
    preset_id: str
    name: str
    data: Dict[str, Any]
    created_at: str
    updated_at: str
    pinned: bool = False


class PinSessionRequest(BaseModel):
    pinned: bool


class SaveSessionRequest(BaseModel):
    """Request model for creating/updating session by name."""
    preset_id: str
    name: str
    data: Dict[str, Any]
    mode: Optional[str] = None  # Optional mode to specify which mode data is being updated
    base_version: Optional[int] = None


class UpdateSessionRequest(BaseModel):
    """Request model for updating session by ID."""
    name: str
    data: Dict[str, Any]
    mode: Optional[str] = None  # Optional mode to specify which mode data is being updated
    base_version: Optional[int] = None


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


class PromptOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positive: str
    negative: Optional[str] = None


class SessionOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    prompts: Optional[List[PromptOverride]] = Field(default=None, min_length=1, max_length=20)
    variables: Dict[str, str] = Field(default_factory=dict)
    seed: Optional[int] = None
    count: Optional[int] = Field(default=None, ge=1)
    form: Dict[str, Any] = Field(default_factory=dict)


class GenerateFromSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Optional[str] = None
    overrides: SessionOverrides = Field(default_factory=SessionOverrides)
    if_settings_hash: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)
    tag_ids: Optional[List[str]] = None
    collection_ids: Optional[List[str]] = None
    dry_run: bool = False
