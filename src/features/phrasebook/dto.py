"""
Phrasebook DTOs for request/response models.
"""
from enum import Enum
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


# ========== Enums ==========

class PhrasebookStateFilter(str, Enum):
    """Filter for phrasebook active state."""
    ALL = "all"
    ACTIVE = "active"
    INACTIVE = "inactive"


# ========== Response DTOs ==========

class PhrasebookCategory(BaseModel):
    """Response model for phrasebook category."""
    id: str
    name: str
    path: str
    parent_id: Optional[str] = None
    description: str = ""
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    user_id: Optional[str] = None


class PhrasebookValue(BaseModel):
    """Response model for phrasebook value."""
    id: str
    category_id: str
    label: str
    value: str
    sort_order: int = 0
    is_active: bool = True
    preview_file_id: Optional[str] = None
    preview_generation_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    user_id: Optional[str] = None


# ========== Request DTOs ==========

class PhrasebookCategoryRequest(BaseModel):
    """Request model for creating/updating phrasebook category."""
    name: str
    path: str
    parent_id: Optional[str] = None
    description: str = ""


class PhrasebookValueRequest(BaseModel):
    """Request model for creating/updating phrasebook value."""
    category_id: str
    label: str
    value: str
    sort_order: int = 0


class PhrasebookSearchRequest(BaseModel):
    """Request model for phrasebook search."""
    path: str
    limit: int = 50


class ToggleActiveRequest(BaseModel):
    """Request model for toggling active state."""
    is_active: bool


class GeneratePreviewRequest(BaseModel):
    """Request model for generating preview images."""
    session_id: str
    prompt_template: str = Field(
        ...,
        description="Prompt template containing << value >> placeholder"
    )
    mode: str = Field(
        ...,
        description="Session mode to use (e.g., 'txt2img', 'img2img')"
    )
    negative_prompt: Optional[str] = Field(
        None,
        description="Override session's negative prompt. Leave None to use session's default."
    )
    seed: Optional[int] = Field(
        None,
        description="Fixed seed for all generations. None = random seed per generation."
    )
    value_ids: Optional[List[str]] = Field(
        None,
        description="Specific value IDs to generate for. None = all active values in category."
    )
