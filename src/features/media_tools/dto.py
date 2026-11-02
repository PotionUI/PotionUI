from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class StepRequest(BaseModel):
    tool: str
    params: Dict[str, Any] = Field(default_factory=dict)
    hidden: bool = False


class InputRef(BaseModel):
    item_id: Optional[str] = None
    generation_file_id: Optional[str] = None


class OutputSpec(BaseModel):
    mode: Literal["new", "replace", "update"] = "new"
    target_item_id: Optional[str] = None


class StartRunRequest(BaseModel):
    steps: List[StepRequest]
    inputs: List[InputRef]
    output: OutputSpec = Field(default_factory=OutputSpec)


class PreviewRequest(BaseModel):
    tool: str
    params: Dict[str, Any] = Field(default_factory=dict)
    input: InputRef
    time_seconds: Optional[float] = None


class FramePreviewRequest(BaseModel):
    input: InputRef
    steps: List[StepRequest] = Field(default_factory=list)
    time_seconds: float = 0.0
    max_side: int = Field(1280, ge=16, le=8192)
    format: Literal["webp", "png"] = "webp"


class ClipPreviewRequest(BaseModel):
    input: InputRef
    steps: List[StepRequest] = Field(default_factory=list)
    start_seconds: float = Field(0.0, ge=0)
    duration_seconds: float = Field(3.0, gt=0, le=10)
    height: int = Field(480, ge=16, le=2160)


class PreviewClip(BaseModel):
    id: str
    url: str
    mime_type: str
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


class ClipPreviewResponse(BaseModel):
    run_id: Optional[str] = None
    preview: Optional[PreviewClip] = None


class ArtifactDTO(BaseModel):
    id: str
    media_type: str
    mime_type: Optional[str] = None
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    size: Optional[int] = None


class ToolDTO(BaseModel):
    id: str
    label: str
    icon: str
    accepts: List[str]
    produces: str
    cardinality: str
    params: List[Dict[str, Any]]
    requires: Dict[str, Any]
    selection: Optional[Dict[str, Any]] = None
    scopes: List[str]
    editor: Optional[str] = None
    preview: bool = False
    component: Optional[str] = None
    source: str
    description: Optional[str] = None
    group: str = "edit"
    runnable: bool = True
    last_params: Optional[Dict[str, Any]] = None


class CatalogResponse(BaseModel):
    tools: List[ToolDTO]


class StartRunResponse(BaseModel):
    run_id: str


class MediaToolItem(BaseModel):
    id: str
    filename: str
    original_filename: Optional[str] = None
    media_type: str
    mime_type: Optional[str] = None
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    size: Optional[int] = None
    created_at: Optional[str] = None
    thumbnail_small: Optional[str] = None
    thumbnail_medium: Optional[str] = None
    thumbnail_large: Optional[str] = None
    source_generation_id: Optional[str] = None


class RunError(BaseModel):
    code: str
    message: str


class RunDTO(BaseModel):
    run_id: str
    kind: Literal["run", "preview"] = "run"
    status: RunStatus
    progress: float = 0.0
    label: str = ""
    items: List[MediaToolItem] = Field(default_factory=list)
    replaced: bool = False
    preview: Optional[PreviewClip] = None
    error: Optional[RunError] = None


class RunListResponse(BaseModel):
    runs: List[RunDTO]


@dataclass
class RunResult:
    items: List[MediaToolItem] = field(default_factory=list)
    replaced: bool = False
    error: Optional[RunError] = None
    status: str = "completed"
    run_id: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == "completed"
