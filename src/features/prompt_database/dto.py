"""Aggregate prompt API contracts."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from src.features.segments.dto import RichSegment
from src.features.prompt_database.validators import validate_at_least_one_segment_policy


class PromptMetadata(BaseModel):
    """Browse/search metadata; it is never generation configuration."""

    source_provider: Optional[str] = None
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    source_group_id: Optional[str] = None
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    base_model: Optional[str] = None
    cfg_scale: Optional[float] = None
    steps: Optional[int] = None
    sampler: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    heart_count: int = 0
    like_count: int = 0
    laugh_count: int = 0
    cry_count: int = 0
    comment_count: int = 0
    tags: List[str] = Field(default_factory=list)
    nsfw: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PromptRequest(PromptMetadata):
    name: Optional[str] = None
    usage_hint: Optional[Literal["positive", "negative"]] = None
    segments: List[RichSegment]

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("segments")
    @classmethod
    def at_least_one_segment(cls, value: List[RichSegment]) -> List[RichSegment]:
        return validate_at_least_one_segment_policy(value)


class PromptResponse(PromptMetadata):
    id: str
    user_id: str
    name: Optional[str] = None
    display_name: str
    flattened_text: str
    usage_hint: Optional[Literal["positive", "negative"]] = None
    segments: List[RichSegment]
    embedded: bool = False
    created_at: datetime
    updated_at: datetime


class PromptBulkDeleteRequest(BaseModel):
    prompt_ids: List[str]
