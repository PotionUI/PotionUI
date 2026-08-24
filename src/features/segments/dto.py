"""Contracts shared by prompts, saved segments, and segment templates."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.features.segments.validators import (
    validate_at_least_one_segment_policy,
    validate_required_name_policy,
    validate_required_string_policy,
)


class PhrasebookChipValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    value: str
    preview_file_id: Optional[str] = None


class PhrasebookChip(BaseModel):
    """Complete state required to reproduce an editor phrasebook chip."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    categoryPath: str
    valueId: str
    label: str
    value: str
    allValues: List[PhrasebookChipValue] = Field(default_factory=list)
    shuffle: bool = False
    autoRegen: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_snake_case(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for snake, camel in (
            ("category_path", "categoryPath"), ("value_id", "valueId"),
            ("all_values", "allValues"), ("auto_regen", "autoRegen"),
        ):
            if camel not in data and snake in data:
                data[camel] = data[snake]
        return data


class RichSegment(BaseModel):
    """The persistent, editor-portable subset of a prompt segment.

    Editor ids, collapse state, AI provenance, and library source links are
    deliberately absent.  Aggregate child ids are response-only conveniences;
    clients may omit them when replacing a collection.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: Optional[str] = None
    type: Literal["content", "break"] = "content"
    content: str = ""
    chips: Dict[str, PhrasebookChip] = Field(default_factory=dict)
    enabled: bool = True
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def accept_editor_state(cls, value):
        """Accept the camelCase editor state without persisting UI-only fields."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "enabled" not in data:
            if "is_enabled" in data:
                data["enabled"] = bool(data["is_enabled"])
            elif "isDisabled" in data:
                data["enabled"] = not bool(data["isDisabled"])
            elif "is_disabled" in data:
                data["enabled"] = not bool(data["is_disabled"])
        if "name" not in data and "title" in data:
            data["name"] = data["title"]
        return data

    @field_validator("name", "color", "description", mode="before")
    @classmethod
    def empty_optional_strings(cls, value):
        return None if value == "" else value


class SegmentCategory(BaseModel):
    id: str
    name: str
    description: str = ""
    color: str = "#3B82F6"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    user_id: Optional[str] = None


class SegmentCategoryRequest(BaseModel):
    name: str
    description: str = ""
    color: str = "#3B82F6"

    @field_validator("description", mode="before")
    @classmethod
    def optional_description(cls, value):
        return "" if value is None else value

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return validate_required_name_policy(value)


class SavedSegment(BaseModel):
    id: str
    name: str
    category_id: str
    type: Literal["content", "break"] = "content"
    content: str = ""
    chips: Dict[str, PhrasebookChip] = Field(default_factory=dict)
    enabled: bool = True
    color: Optional[str] = None
    effective_color: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    user_id: Optional[str] = None

    def as_rich_segment(self) -> RichSegment:
        return RichSegment(
            type=self.type,
            content=self.content,
            chips=self.chips,
            enabled=self.enabled,
            name=self.name,
            color=self.effective_color,
            description=self.description,
        )


class SavedSegmentRequest(BaseModel):
    name: str
    category_id: str
    type: Literal["content", "break"] = "content"
    content: str = ""
    chips: Dict[str, PhrasebookChip] = Field(default_factory=dict)
    enabled: bool = True
    color: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_nested_segment(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        nested = data.pop("segment", None)
        if isinstance(nested, dict):
            data = {**nested, **data}
        return RichSegment.accept_editor_state(data)

    @field_validator("name", "category_id")
    @classmethod
    def required_strings(cls, value: str) -> str:
        return validate_required_string_policy(value)


class SegmentTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    segments: List[RichSegment]
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    user_id: Optional[str] = None


class SegmentTemplateRequest(BaseModel):
    name: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    segments: List[RichSegment]

    @field_validator("description", mode="before")
    @classmethod
    def optional_description(cls, value):
        return "" if value is None else value

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return validate_required_name_policy(value)

    @field_validator("segments")
    @classmethod
    def at_least_one_segment(cls, value: List[RichSegment]) -> List[RichSegment]:
        return validate_at_least_one_segment_policy(value)


# Explicit aliases make the distinction from the retired single-snippet
# SegmentTemplate contract clear to plugin/tool authors.
SavedSegmentResponse = SavedSegment
SegmentTemplateResponse = SegmentTemplate
