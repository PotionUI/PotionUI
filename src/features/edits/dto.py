from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class EditSource(BaseModel):
    kind: str
    id: str
    name: Optional[str] = None


class CreateEditRequest(BaseModel):
    tool: str = "image"
    name: Optional[str] = None
    source: EditSource
    state: Dict[str, Any] = Field(default_factory=dict)
    thumbnail_upload_id: Optional[str] = None


class UpdateEditStateRequest(BaseModel):
    revision: int = Field(ge=1)
    state: Dict[str, Any]
    thumbnail_upload_id: Optional[str] = None


class RenameEditRequest(BaseModel):
    name: Optional[str] = None
