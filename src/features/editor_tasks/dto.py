from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from src.features.media_tools.dto import InputRef, StepRequest

MAX_CANDIDATES = 4
MAX_EXPAND_SIDE = 8192


class RunEditRef(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    step_id: Optional[str] = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


class RunSetup(BaseModel):
    preset_id: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    form_data: Dict[str, Any] = Field(default_factory=dict)


class RunRange(BaseModel):
    start_s: float = Field(default=0.0, ge=0)
    end_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "RunRange":
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be greater than start_s")
        return self

class RunExpand(BaseModel):
    width: int = Field(ge=1, le=MAX_EXPAND_SIDE)
    height: int = Field(ge=1, le=MAX_EXPAND_SIDE)
    x: int = Field(default=0, ge=0, le=MAX_EXPAND_SIDE)
    y: int = Field(default=0, ge=0, le=MAX_EXPAND_SIDE)

class UpscaleTarget(BaseModel):
    width: int = Field(ge=1, le=65536)
    height: int = Field(ge=1, le=65536)

class StartEditorRunRequest(BaseModel):
    edit: RunEditRef
    input: InputRef
    steps: List[StepRequest] = Field(default_factory=list)
    region: Optional[InputRef] = None
    range: Optional[RunRange] = None
    expand: Optional[RunExpand] = None
    prompt: Optional[str] = None
    setup: RunSetup
    candidates: int = Field(default=1, ge=1, le=MAX_CANDIDATES)
    seed: Optional[int] = Field(default=None, ge=0)
    options: Dict[str, Any] = Field(default_factory=dict)
    strength: Optional[float] = Field(default=None, ge=0)
    scale: Optional[float] = Field(default=None, gt=1, le=64)
    target: Optional[UpscaleTarget] = None

class DetectionRequest(BaseModel):
    input: InputRef
    steps: List[StepRequest] = Field(default_factory=list)

class DefaultSetup(BaseModel):
    kind: Literal["session", "formula", "defaults"]
    id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _id_matches_kind(self) -> "DefaultSetup":
        if self.kind == "defaults" and self.id is not None:
            raise ValueError("a setup of kind 'defaults' takes no id")
        if self.kind != "defaults" and self.id is None:
            raise ValueError(f"a setup of kind '{self.kind}' needs an id")
        return self

class SetTaskDefaultRequest(BaseModel):
    preset_id: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    setup: DefaultSetup
