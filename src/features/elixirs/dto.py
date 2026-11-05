import math
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from src.features.elixirs.identity import media_ref
from src.features.elixirs.sessions import OVERRIDE_KEYS
from src.features.elixirs.vocabulary import MediaType, PortName
from src.features.generation.prompt_batch import MAX_BATCH_IMAGES
from src.features.tab_drafts.validation import variable_errors

MAX_STORED_NODES = 256
MAX_STORED_EDGES = 512
MAX_STORED_ASSETS = 5000
MAX_BREW_INVOCATIONS = 10000
MAX_INSTRUCTION_CHARS = 4000
MAX_WRITTEN_CHARS = 8000
MAX_REASONING_CHARS = 20000
MAX_PROMPTS_PER_INPUT = MAX_BATCH_IMAGES

ElementId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
RunMode = Literal["automatic", "review"]
FilePick = Literal["all", "random"]
SessionMode = Literal["follow", "copy", "new"]
SessionHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = 0.0
    y: float = 0.0

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("a position must be a finite number")
        return value


class Viewport(Position):
    zoom: float = Field(default=1.0, gt=0, le=16)


class AssetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["history", "library"]
    generation_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    file_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    item_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    media_type: Optional[str] = None
    original_filename: Optional[str] = None
    preview_url: Optional[str] = None
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _shape(self) -> "AssetEntry":
        if self.kind == "history" and (not self.generation_id or not self.file_id or self.item_id):
            raise ValueError("a history file needs generation_id and file_id only")
        if self.kind == "library" and (not self.item_id or self.generation_id or self.file_id):
            raise ValueError("a library item needs item_id only")
        return self

    def ref(self) -> Dict[str, str]:
        return media_ref(self.model_dump())


class PresetNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ElementId
    kind: Literal["preset"]
    title: Optional[Title] = None
    position: Position = Field(default_factory=Position)
    preset_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    mode: Optional[str] = Field(default=None, min_length=1, max_length=64)
    form_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    overrides: Dict[str, Any] = Field(default_factory=dict)
    session_source: Optional[Dict[str, Any]] = None
    session_snapshot: Optional[Dict[str, Any]] = None
    session_mode: Optional[SessionMode] = None
    session_ref: Optional[Dict[str, Any]] = None
    session_hash: Optional[str] = Field(default=None, max_length=64)

    @field_validator("overrides")
    @classmethod
    def _known_overrides(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        unknown = sorted(set(value) - OVERRIDE_KEYS)
        if unknown:
            raise ValueError(f"settings that cannot be changed on a node: {', '.join(unknown)}")
        if "formData" in value and not isinstance(value["formData"], dict):
            raise ValueError("formData overrides must be an object")
        return value


class MediaNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ElementId
    kind: Literal["media"]
    title: Optional[Title] = None
    position: Position = Field(default_factory=Position)
    media_type: Optional[MediaType] = None
    assets: List[AssetEntry] = Field(default_factory=list, max_length=MAX_STORED_ASSETS)
    source_revision: int = Field(default=0, ge=0)
    pick: FilePick = "all"
    pick_count: int = Field(default=1, ge=1, le=MAX_STORED_ASSETS)


WrittenText = Annotated[str, StringConstraints(min_length=1, max_length=MAX_WRITTEN_CHARS)]
PinKey = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ChipText = Annotated[str, StringConstraints(max_length=MAX_WRITTEN_CHARS)]
ChipKey = Annotated[str, StringConstraints(min_length=1, max_length=200)]
MAX_INSTRUCTION_CHIPS = 64
MAX_CHIP_VALUES = 1000


class ChipValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: ChipKey
    label: ChipText = ""
    value: ChipText = ""
    preview_file_id: Optional[ChipKey] = None


class InstructionChip(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: ChipKey
    categoryPath: ChipKey
    valueId: ChipKey
    label: ChipText = ""
    value: ChipText = ""
    allValues: List[ChipValue] = Field(default_factory=list, max_length=MAX_CHIP_VALUES)
    shuffle: bool = False
    autoRegen: bool = False


class LlmNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ElementId
    kind: Literal["llm"]
    title: Optional[Title] = None
    position: Position = Field(default_factory=Position)
    instruction: Annotated[str, StringConstraints(max_length=MAX_INSTRUCTION_CHARS)] = ""
    instruction_chips: Dict[ChipKey, InstructionChip] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    target_node_id: Optional[ElementId] = None
    llm_id: Optional[ElementId] = None
    prompts_per_input: int = Field(default=1, ge=1, le=MAX_PROMPTS_PER_INPUT)
    pinned: Dict[PinKey, WrittenText] = Field(default_factory=dict)

    @field_validator("pinned")
    @classmethod
    def _few_pins(cls, value: Dict[str, str]) -> Dict[str, str]:
        if len(value) > MAX_STORED_ASSETS:
            raise ValueError(f"a prompt writer can keep at most {MAX_STORED_ASSETS} pinned texts")
        return value

    @field_validator("instruction_chips")
    @classmethod
    def _few_chips(cls, value: Dict[str, InstructionChip]) -> Dict[str, InstructionChip]:
        if len(value) > MAX_INSTRUCTION_CHIPS:
            raise ValueError(f"an instruction can hold at most {MAX_INSTRUCTION_CHIPS} phrasebook chips")
        return value

    @field_validator("variables")
    @classmethod
    def _valid_variables(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        errors = variable_errors(value)
        if errors:
            raise ValueError("; ".join(errors))
        return value


Node = Annotated[Union[PresetNode, MediaNode, LlmNode], Field(discriminator="kind")]


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ElementId
    source: ElementId
    source_handle: PortName
    target: ElementId
    target_handle: PortName
    target_segment_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    iteration: Literal["each"] = "each"


class ElixirDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    nodes: List[Node] = Field(default_factory=list, max_length=MAX_STORED_NODES)
    edges: List[Edge] = Field(default_factory=list, max_length=MAX_STORED_EDGES)
    viewport: Optional[Viewport] = None


class CreateElixirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    run_mode: RunMode = "review"
    definition: ElixirDefinition = Field(default_factory=ElixirDefinition)


class UpdateElixirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    name: Optional[Name] = None
    run_mode: Optional[RunMode] = None
    definition: Optional[ElixirDefinition] = None


class ImportSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=64)
    version: Optional[int] = Field(default=None, ge=1)
    mode: Optional[str] = Field(default=None, min_length=1, max_length=64)
    overrides: Optional[Literal["keep", "discard"]] = None


class FollowSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=64)
    mode: Optional[str] = Field(default=None, min_length=1, max_length=64)


class NodeRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class StartBrewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=120)
    templates: Dict[ElementId, Dict[str, Any]] = Field(default_factory=dict, max_length=MAX_STORED_NODES)
    session_hashes: Dict[ElementId, SessionHash] = Field(default_factory=dict, max_length=MAX_STORED_NODES)
    run_mode: Optional[RunMode] = None


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    selected: List[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=MAX_STORED_ASSETS * 4)


class NodeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=120)
    template: Dict[str, Any]
    session_hashes: Dict[ElementId, SessionHash] = Field(default_factory=dict, max_length=MAX_STORED_NODES)


class CancelBrewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: Optional[List[Annotated[str, StringConstraints(min_length=1, max_length=200)]]] = Field(default=None, max_length=1000)
    all_active: bool = False


class EditTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: WrittenText
