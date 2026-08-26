"""
Tag DTOs for request/response models.
"""
from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


# ========== Enums ==========

class TagType(str, Enum):
    """Type of tag - determines what entities it can be applied to."""
    MODEL = "MODEL"
    GENERATION = "GENERATION"
    UPLOAD = "UPLOAD"


# Every type except MODEL is owned by the user who created it. MODEL tags are
# global (user_id=None) and admin-authored.
USER_SCOPED_TAG_TYPES = (TagType.GENERATION, TagType.UPLOAD)


def effective_user_id_for_type(tag_type: TagType, user_id: str) -> Optional[str]:
    """The user_id to scope a tag query/write to, given its type.

    MODEL tags are global (user_id=None); GENERATION and UPLOAD tags are
    user-specific.
    """
    return None if tag_type == TagType.MODEL else user_id


# ========== Response DTOs ==========

class Tag(BaseModel):
    """Response model for a tag."""
    id: str
    name: str
    type: TagType
    user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

    class Config:
        use_enum_values = True


class TagWithCount(BaseModel):
    """Response model for a tag with usage count."""
    id: str
    name: str
    type: TagType
    user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    usage_count: int = 0
    # For untyped queries that return both counts
    model_count: Optional[int] = None
    generation_count: Optional[int] = None
    upload_count: Optional[int] = None

    class Config:
        use_enum_values = True


# ========== Request DTOs ==========

class CreateTagRequest(BaseModel):
    """Request model for creating a tag."""
    name: str
    type: TagType


class UpdateTagRequest(BaseModel):
    """Request model for updating a tag."""
    name: str
