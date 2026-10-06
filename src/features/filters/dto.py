from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

MAX_USER_FILTERS = 100
MAX_NAME_LENGTH = 24
MAX_DESCRIPTION_LENGTH = 240


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("name must not be empty")
    return cleaned


class CreateFilterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH + 40)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    group: Optional[str] = Field(None, min_length=1, max_length=MAX_NAME_LENGTH)
    intensity: int = Field(100, ge=0, le=100)
    steps: List[Dict[str, Any]]
    lut: Optional[str] = None
    has_lut: bool = False
    source_id: Optional[str] = Field(None, max_length=120)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)


class UpdateFilterRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=MAX_NAME_LENGTH + 40)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    group: Optional[str] = Field(None, min_length=1, max_length=MAX_NAME_LENGTH)
    intensity: Optional[int] = Field(None, ge=0, le=100)
    steps: Optional[List[Dict[str, Any]]] = None
    lut: Optional[str] = None
    has_lut: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: Optional[str]) -> Optional[str]:
        return _clean_name(value) if value is not None else None
