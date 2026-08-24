"""LLM Data Transfer Objects for API requests and responses."""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel


# ============================================================================
# LLM Configuration DTOs
# ============================================================================

class LLMConfigRequest(BaseModel):
    """Request model for creating/updating LLM configuration."""
    id: Optional[str] = None
    name: str
    type: str  # "ollama" or "openai"
    enabled: bool
    base_url: str
    api_key: Optional[str] = None
    model: str
    system_message: str
    temperature: float = 0.7
    max_tokens: int = 1000
    timeout: int = 30
    supports_vision: bool = False
    disable_system_prompt: bool = False
    memory_reflection: bool = True
    provider_options: Optional[Dict[str, Any]] = None


# ============================================================================
# Tool Governance DTOs
# ============================================================================

class ToolGovernanceUpdateRequest(BaseModel):
    """Admin update to one tool's governance row. Both fields optional so a
    client can flip just `enabled` or just `locked` in one call."""
    enabled: Optional[bool] = None
    locked: Optional[bool] = None


class UserToolPreferenceRequest(BaseModel):
    """A user's opt-out toggle for one tool. `llm_config_id` names the caller's
    active LLM config - the toggle is always checked against that config's
    governance row (403/409 if it disabled or locked the tool); the opt-out
    itself is always stored globally, not per-config."""
    disabled: bool
    llm_config_id: str


class LLMConfigResponse(BaseModel):
    """Response model for LLM configuration.

    The API key is never returned. ``api_key_set`` reports whether a key is
    stored so the UI can show a "configured" state without exposing the
    secret; to update the key, clients send a new value on the request DTO.
    """
    id: str
    name: str
    type: str
    enabled: bool
    base_url: str
    api_key_set: bool = False
    model: str
    system_message: str
    temperature: float
    max_tokens: int
    timeout: int
    supports_vision: bool = False
    disable_system_prompt: bool = False
    memory_reflection: bool = True
    provider_options: Optional[Dict[str, Any]] = None
    is_default: bool = False


# ============================================================================
# LLM Generation DTOs
# ============================================================================

class LLMGenerateRequest(BaseModel):
    """Request model for LLM text generation."""
    prompt: str
    config_id: Optional[str] = None
    image_data: Optional[str] = None  # Base64 encoded image or file path for vision models


class LLMGenerateResponse(BaseModel):
    """Response model for LLM text generation."""
    content: str
    model: str
    provider_id: str
    tokens_used: Optional[int] = None


# ============================================================================
# User LLM Assignment DTOs
# ============================================================================

class UserLLMAssignmentRequest(BaseModel):
    """Request model for assigning LLM to user."""
    user_id: str
    llm_config_id: str


class UserLLMAssignmentResponse(BaseModel):
    """Response model for a user-LLM assignment."""
    user_id: str
    llm_config_id: str
    assigned_at: str


class UserLLMAssignmentsResponse(BaseModel):
    """Response model for user's LLM assignments."""
    user_id: str
    llm_configs: List[LLMConfigResponse]
