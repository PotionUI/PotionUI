"""Backend Data Transfer Objects for API requests and responses.

A backend is a configured instance of an engine. See docs/backends.md.

Engine-specific settings (e.g. ComfyUI's host/port/secure) are FLAT top-level
fields, not nested under `config` - that matches the backend config models
(BaseBackendConfig and its subclasses) and therefore the GET response shape.
The request models allow extra fields so each engine's own settings pass
through to its registered config class, which is what validates them.
"""

from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class BackendCreateRequest(BaseModel):
    """
    Request model for creating a new backend.

    `is_default` is deliberately absent: the default flag lives on the persisted
    entity and is set only via POST /api/backends/{id}/set-default.
    """
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Human-readable name for the backend")
    engine: str = Field(..., description="Engine this backend provides (e.g. 'comfyui')")
    enabled: bool = Field(default=True, description="Whether the backend is enabled")
    priority: int = Field(default=1, description="Priority for backend selection (higher = preferred)")
    timeout_seconds: int = Field(default=300, description="Timeout for generation requests")


class BackendUpdateRequest(BaseModel):
    """
    Request model for updating an existing backend. All fields optional (partial update).

    `engine` is absent because it is immutable: changing it would change which
    config class validates the backend. Delete and recreate instead.
    `is_default` is absent - see BackendCreateRequest.
    """
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(None, description="Human-readable name for the backend")
    enabled: Optional[bool] = Field(None, description="Whether the backend is enabled")
    priority: Optional[int] = Field(None, description="Priority for backend selection")
    timeout_seconds: Optional[int] = Field(None, description="Timeout for generation requests")


class AttentionBackendRequest(BaseModel):
    """Body for pinning the native engine's attention backend (Optimizations panel)."""

    backend: str = Field(..., description="'auto', or one of attention.known_backends() (BACKEND_PRIORITY + PIN_ONLY_BACKENDS, e.g. 'sparge')")


class EngineFlagsRequest(BaseModel):
    """Body for toggling native engine flags (Optimizations panel). Omitted fields are left unchanged."""

    torch_compile: Optional[str] = Field(None, description="'on' or 'off' for regional torch.compile")
    stream_prefetch: Optional[str] = Field(None, description="'on' or 'off' for streaming layer prefetch under partial residency")


