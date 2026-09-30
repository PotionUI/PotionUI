from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping, Optional, Protocol, Sequence, Union, get_args

CloudErrorKind = Literal[
    "auth",
    "credits",
    "rate_limited",
    "refused",
    "invalid_request",
    "unavailable",
    "timeout",
    "failed",
    "expired",
]

CLOUD_ERROR_KINDS: tuple[str, ...] = get_args(CloudErrorKind)


class CloudRunError(Exception):
    def __init__(
        self,
        kind: CloudErrorKind,
        user_message: str,
        *,
        detail: str = "",
        retry_after_s: Optional[float] = None,
    ) -> None:
        if kind not in CLOUD_ERROR_KINDS:
            raise ValueError(f"unknown cloud error kind {kind!r}")
        super().__init__(user_message)
        self.kind = kind
        self.user_message = user_message
        self.detail = detail
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class CloudRunRequest:
    task: str
    model: str
    prompt: str
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    count: int = 1
    params: Mapping[str, Any] = field(default_factory=dict)
    inputs: Mapping[str, Sequence[Path]] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudRunProgress:
    state: Literal["queued", "running", "fetching"]
    fraction: Optional[float] = None
    queue_position: Optional[int] = None
    message: Optional[str] = None
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class CloudRunArtifact:
    modality: Literal["image", "video", "audio"]
    index: int
    path: Path
    media_type: Optional[str] = None


@dataclass(frozen=True)
class CloudRunCost:
    amount_usd: Decimal
    source: Literal["provider", "estimate"]


@dataclass(frozen=True)
class CloudRunOutcome:
    artifacts: tuple[CloudRunArtifact, ...]
    cost: Optional[CloudRunCost] = None
    seed_used: Optional[int] = None
    provider_job_id: Optional[str] = None
    cancel_confirmed: Optional[bool] = None


ProgressCallback = Callable[[CloudRunProgress], Union[Awaitable[None], None]]


class CloudRunner(Protocol):
    async def run(
        self,
        request: CloudRunRequest,
        *,
        on_progress: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CloudRunOutcome: ...
