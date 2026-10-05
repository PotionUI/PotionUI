from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.features.generation.dto import GenerationRequest

PROMPT_AXIS_FIELD = "__prompt__"
HARD_CAP = 100
DEFAULT_CONFIRM_ABOVE = 24


class AxisValue(BaseModel):
    value: Any = None
    label: str = ""


class Axis(BaseModel):
    field: str = Field(min_length=1)
    type: str = ""
    label: str = ""
    values: List[AxisValue] = Field(min_length=1)


class CreateGridRequest(BaseModel):
    request: GenerationRequest
    x_axis: Axis
    y_axis: Optional[Axis] = None
    lock_seed: bool = True


@dataclass(frozen=True)
class GridCellRef:
    grid_id: str
    x: int
    y: int
    axis_values: Dict[str, Any]


class GridCell(BaseModel):
    x: int
    y: int
    generation_id: Optional[str] = None
    status: str = "deleted"
    axis_values: Dict[str, Any] = Field(default_factory=dict)
    seed: Optional[int] = None
    thumbnail_url: Optional[str] = None
    media_type: Optional[str] = None
    error: Optional[str] = None
