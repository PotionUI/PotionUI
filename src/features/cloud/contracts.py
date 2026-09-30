from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Mapping, Optional, get_args

from pydantic import Field

from src.features.backends.backend_config import BaseBackendConfig
from src.pipelines.cloud import CLOUD_ERROR_KINDS, CloudErrorKind, CloudRunError

if TYPE_CHECKING:
    from src.features.cloud.http import CloudHttp

CLOUD_ENGINE = "cloud"

TaskKind = Literal[
    "txt2img",
    "img_edit",
    "inpaint",
    "txt2video",
    "img2video",
    "ref2video",
    "video2video",
    "txt2audio",
    "txt2speech",
    "upscale_image",
    "upscale_video",
]
TASK_KINDS: tuple[str, ...] = get_args(TaskKind)

Modality = Literal["image", "video", "audio"]
MODALITIES: tuple[str, ...] = get_args(Modality)
ParamKind = Literal["enum", "range", "boolean", "text"]
PARAM_KINDS: tuple[str, ...] = get_args(ParamKind)
PriceUnit = Literal["request", "image", "megapixel", "second", "token", "sku"]
PRICE_UNITS: tuple[str, ...] = get_args(PriceUnit)
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "expired"]

PARAM_PROMPT = "prompt"
PARAM_NEGATIVE_PROMPT = "negative_prompt"
PARAM_COUNT = "count"
PARAM_SEED = "seed"
PARAM_ASPECT_RATIO = "aspect_ratio"
PARAM_RESOLUTION = "resolution"
PARAM_SIZE = "size"
PARAM_DURATION_S = "duration_s"
PARAM_FPS = "fps"
PARAM_QUALITY = "quality"
PARAM_OUTPUT_FORMAT = "output_format"
PARAM_BACKGROUND = "background"
PARAM_GUIDANCE = "guidance"
PARAM_STEPS = "steps"
PARAM_STRENGTH = "strength"
PARAM_GENERATE_AUDIO = "generate_audio"
PARAM_LYRICS = "lyrics"
PARAM_VOICE = "voice"
PARAM_INSTRUMENTAL = "instrumental"
PARAM_ENHANCE_PROMPT = "enhance_prompt"

CANONICAL_PARAMS: frozenset[str] = frozenset({
    PARAM_PROMPT,
    PARAM_NEGATIVE_PROMPT,
    PARAM_COUNT,
    PARAM_SEED,
    PARAM_ASPECT_RATIO,
    PARAM_RESOLUTION,
    PARAM_SIZE,
    PARAM_DURATION_S,
    PARAM_FPS,
    PARAM_QUALITY,
    PARAM_OUTPUT_FORMAT,
    PARAM_BACKGROUND,
    PARAM_GUIDANCE,
    PARAM_STEPS,
    PARAM_STRENGTH,
    PARAM_GENERATE_AUDIO,
    PARAM_LYRICS,
    PARAM_VOICE,
    PARAM_INSTRUMENTAL,
    PARAM_ENHANCE_PROMPT,
})

EXTRA_PARAM_PREFIX = "x."

ROLE_REFERENCE = "reference"
ROLE_FIRST_FRAME = "first_frame"
ROLE_LAST_FRAME = "last_frame"
ROLE_MASK = "mask"
ROLE_SOURCE_IMAGE = "source_image"
ROLE_SOURCE_VIDEO = "source_video"
ROLE_SOURCE_AUDIO = "source_audio"

MEDIA_ROLES: frozenset[str] = frozenset({
    ROLE_REFERENCE,
    ROLE_FIRST_FRAME,
    ROLE_LAST_FRAME,
    ROLE_MASK,
    ROLE_SOURCE_IMAGE,
    ROLE_SOURCE_VIDEO,
    ROLE_SOURCE_AUDIO,
})

TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "expired"})


def is_canonical_param(name: str) -> bool:
    return name in CANONICAL_PARAMS or name.startswith(EXTRA_PARAM_PREFIX)


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: ParamKind
    values: tuple = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    integer: bool = True
    default: Any = None
    required: bool = False
    label: Optional[str] = None
    description: Optional[str] = None
    tasks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MediaInputSpec:
    role: str
    modality: Modality
    min_items: int = 0
    max_items: int = 1
    max_bytes: Optional[int] = None
    formats: tuple[str, ...] = ()
    tasks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PriceLine:
    unit: PriceUnit
    usd: Decimal
    applies_to: Optional[str] = None


@dataclass(frozen=True)
class CloudModelSpec:
    provider_model_id: str
    label: str
    tasks: frozenset[str]
    outputs: frozenset[str]
    params: tuple[ParamSpec, ...]
    vendor: Optional[str] = None
    description: Optional[str] = None
    inputs: tuple[MediaInputSpec, ...] = ()
    max_outputs_per_job: int = 1
    pricing: tuple[PriceLine, ...] = ()
    typical_seconds: Optional[int] = None
    max_seconds: Optional[int] = None
    deprecated_at: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalMedia:
    path: Path
    media_type: str
    size: int


@dataclass(frozen=True)
class CloudRequest:
    model: CloudModelSpec
    task: str
    prompt: str
    count: int = 1
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    params: Mapping[str, Any] = field(default_factory=dict)
    inputs: Mapping[str, list[LocalMedia]] = field(default_factory=dict)
    client_reference: str = ""
    idempotency_key: str = ""
    user_ref: str = ""


@dataclass(frozen=True)
class CloudArtifact:
    modality: Modality
    index: int
    data: Optional[bytes] = None
    url: Optional[str] = None
    media_type: Optional[str] = None


@dataclass(frozen=True)
class CloudCost:
    amount_usd: Decimal
    source: Literal["provider", "estimate"]
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudResult:
    artifacts: tuple[CloudArtifact, ...]
    cost: Optional[CloudCost] = None
    seed_used: Optional[int] = None
    provider_job_id: Optional[str] = None


@dataclass(frozen=True)
class CloudJob:
    job_id: str
    handle: Mapping[str, Any] = field(default_factory=dict)
    result: Optional[CloudResult] = None
    poll_after_s: Optional[float] = None


@dataclass(frozen=True)
class CloudStatus:
    state: JobState
    progress: Optional[float] = None
    queue_position: Optional[int] = None
    message: Optional[str] = None
    result: Optional[CloudResult] = None
    poll_after_s: Optional[float] = None


@dataclass(frozen=True)
class CloudHealth:
    ok: bool
    message: Optional[str] = None
    credits_usd: Optional[Decimal] = None


class CloudError(CloudRunError):
    def __init__(
        self,
        kind: CloudErrorKind,
        user_message: str,
        *,
        detail: str = "",
        retry_after_s: Optional[float] = None,
        request_sent: bool = True,
    ) -> None:
        super().__init__(kind, user_message, detail=detail, retry_after_s=retry_after_s)
        self.request_sent = request_sent


class CloudBackendConfig(BaseBackendConfig):
    engine: str = Field(default=CLOUD_ENGINE)
    timeout_seconds: int = Field(default=1800, description="Longest a single generation may run before it is abandoned")
    max_parallel: int = Field(default=4, ge=1, le=32, title="Parallel jobs", description="How many generations this backend runs at the same time")

    engine_label: ClassVar[Optional[str]] = "Cloud"


class CloudProvider(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    config_class: ClassVar[type[CloudBackendConfig]]
    data_notice: ClassVar[str] = ""
    supports_cancel: ClassVar[bool] = False
    idempotent_submit: ClassVar[bool] = False

    def __init__(self, config: CloudBackendConfig, http: CloudHttp) -> None:
        self.config = config
        self.http = http
        http.set_error_mapper(self.map_error)

    def suggested_model_ids(self) -> tuple[str, ...]:
        return ()

    @classmethod
    def api_base_url(cls, config: CloudBackendConfig) -> str:
        return str(getattr(config, "base_url", "") or "")

    @classmethod
    def auth_headers(cls, config: CloudBackendConfig) -> Mapping[str, str]:
        return {}

    def map_error(self, status: int, headers: Mapping[str, str], body: Any) -> Optional[CloudError]:
        return None

    @abstractmethod
    async def discover(self) -> list[CloudModelSpec]: ...

    @abstractmethod
    async def submit(self, request: CloudRequest) -> CloudJob: ...

    async def poll(self, job: CloudJob) -> CloudStatus:
        raise NotImplementedError

    async def fetch(self, artifact: CloudArtifact, dest: Path, *, max_bytes: Optional[int] = None) -> Path:
        if artifact.data is not None:
            if max_bytes is not None and len(artifact.data) > max_bytes:
                raise CloudError("failed", "The result is larger than the allowed size.", detail=f"inline result exceeded {max_bytes} bytes")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(artifact.data)
            return dest
        if not artifact.url:
            raise CloudError("failed", "The provider returned a result without any content.")
        return await self.http.download(artifact.url, dest, max_bytes=max_bytes)

    async def cancel(self, job: CloudJob) -> bool:
        return False

    async def check(self) -> CloudHealth:
        return CloudHealth(ok=True)


def spec_problems(spec: CloudModelSpec) -> list[str]:
    problems: list[str] = []
    if not spec.provider_model_id:
        problems.append("provider_model_id is empty")
    if not spec.label:
        problems.append("label is empty")
    if not spec.tasks:
        problems.append("no tasks")
    for task in spec.tasks:
        if task not in TASK_KINDS:
            problems.append(f"unknown task {task!r}")
    if not spec.outputs:
        problems.append("no outputs")
    for output in spec.outputs:
        if output not in MODALITIES:
            problems.append(f"unknown output {output!r}")
    if spec.max_outputs_per_job < 1:
        problems.append("max_outputs_per_job below 1")
    seen: set[str] = set()
    for param in spec.params:
        if param.name in seen:
            problems.append(f"duplicate param {param.name!r}")
        seen.add(param.name)
        if not is_canonical_param(param.name):
            problems.append(f"param {param.name!r} is neither canonical nor an x. extra")
        if param.kind not in PARAM_KINDS:
            problems.append(f"param {param.name!r} has unknown kind {param.kind!r}")
        if param.kind == "enum":
            if not param.values:
                problems.append(f"enum param {param.name!r} has no values")
            elif param.default is not None and param.default not in param.values:
                problems.append(f"enum param {param.name!r} default is not one of its values")
        if param.kind == "range":
            if param.minimum is None or param.maximum is None or param.minimum > param.maximum:
                problems.append(f"range param {param.name!r} has an invalid span")
            elif param.default is not None and not param.minimum <= param.default <= param.maximum:
                problems.append(f"range param {param.name!r} default is outside its span")
        for task in param.tasks:
            if task not in spec.tasks:
                problems.append(f"param {param.name!r} names task {task!r} the model lacks")
    for media in spec.inputs:
        if media.role not in MEDIA_ROLES:
            problems.append(f"unknown media role {media.role!r}")
        if media.min_items < 0 or media.max_items < media.min_items:
            problems.append(f"media role {media.role!r} has invalid item bounds")
    for line in spec.pricing:
        if line.unit not in PRICE_UNITS:
            problems.append(f"unknown price unit {line.unit!r}")
        if line.usd < 0:
            problems.append("negative price")
    return problems


__all__ = [
    "CANONICAL_PARAMS",
    "CLOUD_ENGINE",
    "CLOUD_ERROR_KINDS",
    "CloudArtifact",
    "CloudBackendConfig",
    "CloudCost",
    "CloudError",
    "CloudErrorKind",
    "CloudHealth",
    "CloudJob",
    "CloudModelSpec",
    "CloudProvider",
    "CloudRequest",
    "CloudResult",
    "CloudStatus",
    "EXTRA_PARAM_PREFIX",
    "JobState",
    "MODALITIES",
    "LocalMedia",
    "MEDIA_ROLES",
    "MediaInputSpec",
    "Modality",
    "PARAM_KINDS",
    "PRICE_UNITS",
    "ParamKind",
    "ParamSpec",
    "PriceLine",
    "PriceUnit",
    "TASK_KINDS",
    "TERMINAL_STATES",
    "TaskKind",
    "is_canonical_param",
    "spec_problems",
]
