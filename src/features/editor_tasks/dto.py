from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.features.media_tools.dto import InputRef, StepRequest

MAX_CANDIDATES = 4


class RunEditRef(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    step_id: Optional[str] = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


class RunSetup(BaseModel):
    preset_id: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    variant: Optional[str] = None
    form_data: Dict[str, Any] = Field(default_factory=dict)


class StartEditorRunRequest(BaseModel):
    edit: RunEditRef
    input: InputRef
    steps: List[StepRequest] = Field(default_factory=list)
    region: Optional[InputRef] = None
    prompt: Optional[str] = None
    setup: RunSetup
    candidates: int = Field(default=1, ge=1, le=MAX_CANDIDATES)
    seed: Optional[int] = Field(default=None, ge=0)
