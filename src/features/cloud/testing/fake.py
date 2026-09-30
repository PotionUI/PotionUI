import struct
import zlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional

from pydantic import Field

from src.features.cloud.contracts import (
    CloudArtifact,
    CloudBackendConfig,
    CloudCost,
    CloudError,
    CloudHealth,
    CloudJob,
    CloudModelSpec,
    CloudProvider,
    CloudRequest,
    CloudResult,
    CloudStatus,
    MediaInputSpec,
    ParamSpec,
    PriceLine,
)
from src.features.cloud.http import CloudHttp

def _png_1x1() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\xff\xff\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


PNG_1X1 = _png_1x1()

FAKE_USER_MESSAGES = {
    "auth": "Fake auth failure",
    "credits": "Fake credits failure",
    "rate_limited": "Fake rate limit",
    "refused": "Fake refusal",
    "invalid_request": "Fake invalid request",
    "unavailable": "Fake outage",
    "timeout": "Fake timeout",
    "failed": "Fake failure",
    "expired": "Fake expiry",
}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._now += max(0.0, seconds)


class FakeCloudConfig(CloudBackendConfig):
    driver: str = Field(default="cloud.fake")
    api_key: str = Field(default="fake-key", json_schema_extra={"secret": True})

    engine_label: ClassVar[Optional[str]] = "Fake cloud"


@dataclass
class FakeBehaviour:
    mode: Literal["sync", "async"] = "sync"
    queue_s: float = 0.0
    duration_s: float = 10.0
    poll_after_s: float = 1.0
    terminal: Literal["succeeded", "failed", "expired"] = "succeeded"
    fail_kind: Optional[str] = None
    fail_stage: Literal["discover", "submit", "poll", "fetch"] = "submit"
    retry_after_s: Optional[float] = None
    outputs: int = 1
    cost_usd: Decimal = Decimal("0.04")
    calls: list[str] = field(default_factory=list)


def fake_specs() -> list[CloudModelSpec]:
    return [
        CloudModelSpec(
            provider_model_id="fake/image-1",
            label="Fake Image",
            vendor="fake",
            description="Scripted image model",
            tasks=frozenset({"txt2img", "img_edit"}),
            outputs=frozenset({"image"}),
            params=(
                ParamSpec(name="aspect_ratio", kind="enum", values=("1:1", "16:9"), default="1:1"),
                ParamSpec(name="quality", kind="range", minimum=1, maximum=10, default=5),
                ParamSpec(name="background", kind="boolean", default=False),
                ParamSpec(name="x.style", kind="text"),
            ),
            inputs=(MediaInputSpec(role="reference", modality="image", max_items=4, tasks=frozenset({"img_edit"})),),
            max_outputs_per_job=4,
            pricing=(PriceLine(unit="image", usd=Decimal("0.04")),),
            typical_seconds=10,
            max_seconds=120,
        ),
        CloudModelSpec(
            provider_model_id="fake/video-1",
            label="Fake Video",
            vendor="fake",
            description="Scripted video model",
            tasks=frozenset({"txt2video", "img2video"}),
            outputs=frozenset({"video"}),
            params=(
                ParamSpec(name="duration_s", kind="range", minimum=2, maximum=10, default=4),
                ParamSpec(name="generate_audio", kind="boolean", default=True),
            ),
            inputs=(MediaInputSpec(role="first_frame", modality="image", tasks=frozenset({"img2video"})),),
            pricing=(PriceLine(unit="second", usd=Decimal("0.40")),),
            typical_seconds=90,
            max_seconds=600,
        ),
    ]


class FakeCloudProvider(CloudProvider):
    key: ClassVar[str] = "fake"
    label: ClassVar[str] = "Fake cloud"
    config_class: ClassVar[type[CloudBackendConfig]] = FakeCloudConfig
    data_notice: ClassVar[str] = "Scripted provider used by tests; nothing leaves this process."
    supports_cancel: ClassVar[bool] = True

    def __init__(
        self,
        config: CloudBackendConfig,
        http: CloudHttp,
        *,
        clock: Any = None,
        behaviour: Optional[FakeBehaviour] = None,
    ) -> None:
        super().__init__(config, http)
        self.clock = clock if clock is not None else FakeClock()
        self.behaviour = behaviour or FakeBehaviour()
        self._cancelled: set[str] = set()
        self._counter = 0

    def _maybe_fail(self, stage: str) -> None:
        behaviour = self.behaviour
        if behaviour.fail_kind and behaviour.fail_stage == stage:
            raise CloudError(
                behaviour.fail_kind,
                FAKE_USER_MESSAGES[behaviour.fail_kind],
                detail=f"scripted failure at {stage}",
                retry_after_s=behaviour.retry_after_s,
            )

    def _result(self, job_id: str, request_count: int, modality: str) -> CloudResult:
        count = min(self.behaviour.outputs, max(1, request_count))
        artifacts = tuple(
            CloudArtifact(
                modality=modality,
                index=index,
                data=PNG_1X1 if self.behaviour.mode == "sync" else None,
                url=None if self.behaviour.mode == "sync" else f"https://fake.invalid/{job_id}/{index}",
                media_type="image/png" if modality == "image" else "video/mp4",
            )
            for index in range(count)
        )
        return CloudResult(
            artifacts=artifacts,
            cost=CloudCost(amount_usd=self.behaviour.cost_usd, source="provider"),
            provider_job_id=job_id,
        )

    async def discover(self) -> list[CloudModelSpec]:
        self.behaviour.calls.append("discover")
        self._maybe_fail("discover")
        return fake_specs()

    async def submit(self, request: CloudRequest) -> CloudJob:
        self.behaviour.calls.append("submit")
        self._maybe_fail("submit")
        self._counter += 1
        job_id = f"fake-job-{self._counter}"
        modality = "video" if "video" in request.task else "image"
        handle = {
            "submitted_at": self.clock.now(),
            "count": request.count,
            "modality": modality,
        }
        if self.behaviour.mode == "sync":
            return CloudJob(job_id=job_id, handle=handle, result=self._result(job_id, request.count, modality))
        return CloudJob(job_id=job_id, handle=handle, poll_after_s=self.behaviour.poll_after_s)

    async def poll(self, job: CloudJob) -> CloudStatus:
        self.behaviour.calls.append("poll")
        self._maybe_fail("poll")
        if job.job_id in self._cancelled:
            return CloudStatus(state="cancelled")
        behaviour = self.behaviour
        elapsed = self.clock.now() - float(job.handle["submitted_at"])
        if elapsed < behaviour.queue_s:
            return CloudStatus(state="queued", queue_position=1, poll_after_s=behaviour.poll_after_s)
        if elapsed < behaviour.queue_s + behaviour.duration_s:
            progress = (elapsed - behaviour.queue_s) / behaviour.duration_s
            return CloudStatus(state="running", progress=min(progress, 0.99), poll_after_s=behaviour.poll_after_s)
        if behaviour.terminal == "failed":
            return CloudStatus(state="failed", message="Scripted failure")
        if behaviour.terminal == "expired":
            return CloudStatus(state="expired", message="Scripted expiry")
        result = self._result(job.job_id, int(job.handle["count"]), str(job.handle["modality"]))
        return CloudStatus(state="succeeded", progress=1.0, result=result)

    async def fetch(self, artifact: CloudArtifact, dest: Path, *, max_bytes: Optional[int] = None) -> Path:
        self.behaviour.calls.append("fetch")
        self._maybe_fail("fetch")
        payload = artifact.data if artifact.data is not None else PNG_1X1
        if max_bytes is not None and len(payload) > max_bytes:
            raise CloudError("failed", "The result is larger than the allowed size.", detail="fake max_bytes")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    async def cancel(self, job: CloudJob) -> bool:
        self.behaviour.calls.append("cancel")
        if not self.supports_cancel:
            return False
        self._cancelled.add(job.job_id)
        return True

    async def check(self) -> CloudHealth:
        return CloudHealth(ok=True, message="fake")


class FakeNoCancelProvider(FakeCloudProvider):
    key: ClassVar[str] = "fake"
    supports_cancel: ClassVar[bool] = False


def build_fake_provider(
    behaviour: Optional[FakeBehaviour] = None,
    *,
    clock: Optional[FakeClock] = None,
    supports_cancel: bool = True,
) -> FakeCloudProvider:
    config = FakeCloudConfig(id="fake-1", name="Fake")
    http = CloudHttp("https://fake.invalid", max_parallel=config.max_parallel)
    provider_class = FakeCloudProvider if supports_cancel else FakeNoCancelProvider
    return provider_class(config, http, clock=clock or FakeClock(), behaviour=behaviour)
