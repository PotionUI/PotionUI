"""
User Group DTOs for request/response models.
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


# ========== Request DTOs ==========

class GroupCreate(BaseModel):
    """Request model for creating a user group."""
    name: str
    description: Optional[str] = None


class GroupUpdate(BaseModel):
    """Request model for updating a user group."""
    name: Optional[str] = None
    description: Optional[str] = None


class MemberIds(BaseModel):
    """Request model for adding members to a group."""
    user_ids: List[str]


class PresetIds(BaseModel):
    """Request model for assigning presets to a group."""
    preset_ids: List[str]


class LLMConfigIds(BaseModel):
    """Request model for assigning LLM configs to a group."""
    llm_config_ids: List[str]


class ModelIds(BaseModel):
    """Request model for assigning models to a group."""
    model_ids: List[str]


# ========== Response DTOs ==========

class UserGroupDTO(BaseModel):
    """Response model for a user group."""
    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # True for the built-in ALL_USERS/ALL_ADMINS groups - the UI uses this to
    # hide/disable delete (and the API refuses it with a 409 regardless).
    is_system: bool = False

    class Config:
        from_attributes = True


class UserGroupMemberDTO(BaseModel):
    """Response model for a group member assignment."""
    id: str
    group_id: str
    user_id: str
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserGroupPresetDTO(BaseModel):
    """Response model for a group preset assignment."""
    id: str
    group_id: str
    preset_id: str
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserGroupLLMDTO(BaseModel):
    """Response model for a group LLM assignment."""
    id: str
    group_id: str
    llm_config_id: str
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserGroupModelDTO(BaseModel):
    """Response model for a group model assignment."""
    id: str
    group_id: str
    model_id: str
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GroupWithCountsDTO(BaseModel):
    """Response model for a user group with resource counts."""
    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    member_count: int = 0
    preset_count: int = 0
    llm_count: int = 0
    model_count: int = 0
    is_system: bool = False

    class Config:
        from_attributes = True
