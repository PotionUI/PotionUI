"""Chat Data Transfer Objects for API requests and responses."""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    """Request model for creating a new chat session"""
    original_text: Optional[str] = None
    llm_config_id: Optional[str] = None
    mode: str = 'generation'  # Chat mode id; immutable once the session exists
    name: Optional[str] = None
    system_message: Optional[str] = None  # Custom system message for chat
    enabled_tools: Optional[List[str]] = None  # Subtractive filter within the mode's tools (None = all)


class MessageResponse(BaseModel):
    """Response model for a chat message"""
    id: str
    session_id: str
    role: str
    content: str
    parsed_content: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    tokens_used: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tool_executions: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    """Response model for a chat session"""
    id: str
    user_id: str
    mode: str
    name: Optional[str] = None
    status: str
    llm_config_id: Optional[str] = None
    original_text: Optional[str] = None
    title_generated: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None
    message_count: int = 0
    messages: Optional[List[MessageResponse]] = None
    metadata: Optional[Dict[str, Any]] = None


class ResourceRefRequest(BaseModel):
    """A reference to an @-mentioned resource attached to a message"""
    uri: str


class SendMessageRequest(BaseModel):
    """Request model for sending a chat message"""
    content: str
    image_data: Optional[str] = None  # Base64 encoded image
    context_metadata: Optional[Dict[str, Any]] = None  # model_id, preset_path, etc.
    resources: Optional[List[ResourceRefRequest]] = None  # @resource refs, resolved at send time


class SendMessageResponse(BaseModel):
    """Response model for sending a chat message"""
    user_message: MessageResponse
    assistant_message: MessageResponse


class UpdateSessionRequest(BaseModel):
    """Request model for updating a chat session"""
    name: Optional[str] = None
    llm_config_id: Optional[str] = None


class ToolApprovalRequest(BaseModel):
    """Request model for approving or rejecting a pending tool execution"""
    message_id: str
    tool_index: int
    approved: bool


class PromptFeedbackRequest(BaseModel):
    """Request model for approving or rejecting an enhancement-proposed prompt"""
    action_index: int
    verdict: str  # 'approved' | 'rejected'
    reason: Optional[str] = None


class MemoryWriteRequest(BaseModel):
    """Request model for creating/updating a persistent LLM memory note"""
    key: str
    content: str
    scope: str = 'global'  # 'global' | 'preset' | 'model'
    scope_ref: Optional[str] = None  # required for 'preset' / 'model' scopes


class MemoryUpdateRequest(BaseModel):
    """Request model for updating an existing LLM memory note's key/content"""
    key: str
    content: str
