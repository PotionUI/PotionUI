import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

MAX_VALUES_BYTES = 64 * 1024
MAX_FORMULAS_PER_SCOPE = 100
MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 1000


def _check_values_size(values: Dict[str, Any]) -> Dict[str, Any]:
    if len(json.dumps(values, separators=(",", ":")).encode("utf-8")) > MAX_VALUES_BYTES:
        raise ValueError(f"values exceed {MAX_VALUES_BYTES // 1024} KB")
    return values


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("name must not be empty")
    return cleaned


class FormulaGroupSelection(BaseModel):
    id: str = Field(..., min_length=1, max_length=120)


class FormulaContent(BaseModel):
    variant: Optional[str] = Field(None, max_length=200)
    groups: List[FormulaGroupSelection] = Field(..., min_length=1)
    values: Dict[str, Any]
    signatures: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    preset_version: str = Field("", max_length=64)

    @field_validator("values")
    @classmethod
    def _values_size(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        return _check_values_size(values)


class CreateFormulaRequest(FormulaContent):
    preset_id: str = Field(..., min_length=1, max_length=200)
    mode: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., max_length=MAX_NAME_LENGTH)
    note: Optional[str] = Field(None, max_length=MAX_NOTE_LENGTH)

    @field_validator("name")
    @classmethod
    def _name(cls, name: str) -> str:
        return _clean_name(name)


class UpdateFormulaRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=MAX_NAME_LENGTH)
    note: Optional[str] = Field(None, max_length=MAX_NOTE_LENGTH)
    content: Optional[FormulaContent] = None

    @field_validator("name")
    @classmethod
    def _name(cls, name: Optional[str]) -> Optional[str]:
        return None if name is None else _clean_name(name)


class PlanFormulaRequest(BaseModel):
    form_name: Optional[str] = None
    current_values: Dict[str, Any] = Field(default_factory=dict)
    lora_mode: Literal["replace", "add"] = "replace"
