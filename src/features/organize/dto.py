from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RuleBody(BaseModel):
    name: Optional[Any] = None
    subject: Optional[str] = None
    match: Optional[str] = None
    conditions: Optional[List[Any]] = None
    actions: Optional[List[Any]] = None
    enabled: Optional[bool] = None
    stop_after: Optional[bool] = None


class RulePatch(BaseModel):
    name: Optional[Any] = None
    enabled: Optional[bool] = None
    stop_after: Optional[bool] = None


class ReorderBody(BaseModel):
    subject: str
    rule_ids: List[str]


class PreviewBody(BaseModel):
    subject: Optional[str] = None
    match: Optional[str] = None
    conditions: Optional[List[Any]] = None
    actions: Optional[List[Any]] = None
    rule_id: Optional[str] = None


class ApplyExistingBody(BaseModel):
    pass


class AdminControlsBody(BaseModel):
    paused_all: Optional[bool] = None
    default_rule_cap: Optional[int] = Field(default=None, ge=0, le=1000)
    hourly_limit: Optional[int] = Field(default=None, ge=1, le=100000)


class AdminUserBody(BaseModel):
    paused: Optional[bool] = None
    rule_cap: Optional[int] = Field(default=None, ge=0, le=1000)


def body_dict(model: BaseModel) -> Dict[str, Any]:
    return model.model_dump(exclude_unset=True)
