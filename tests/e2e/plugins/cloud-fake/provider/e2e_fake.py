import hashlib
import io
from dataclasses import replace
from decimal import Decimal
from typing import Any, ClassVar, Optional

from PIL import Image
from pydantic import Field

from src.plugin_api.cloud import CloudArtifact, CloudCost, CloudModelSpec, CloudResult, MonotonicClock
from src.plugin_api.cloud_testing import FakeBehaviour, FakeCloudConfig, FakeCloudProvider, fake_director_specs, fake_specs

FAIL_KINDS = ["", "auth", "credits", "rate_limited", "refused", "invalid_request", "unavailable", "timeout", "failed", "expired"]
IMAGE_SIZE = 128


class E2eFakeConfig(FakeCloudConfig):
    mode: str = Field(default="async", title="Mode", description="sync answers at once; async queues and then runs", json_schema_extra={"options": ["sync", "async"]})
    queue_seconds: float = Field(default=0.0, ge=0, title="Queue seconds", description="Async only: seconds spent queued before running")
    duration_seconds: float = Field(default=2.0, ge=0, title="Run seconds", description="Async only: seconds a job runs before it finishes")
    poll_seconds: float = Field(default=0.25, gt=0, title="Poll seconds", description="How often an async job is polled")
    fail_kind: str = Field(default="", title="Fail with", description="Make every submit fail with this error kind", json_schema_extra={"options": FAIL_KINDS})
    cost_usd: float = Field(default=0.04, ge=0, title="Reported cost", description="Cost the provider reports for each job")
    supports_cancel: bool = Field(default=True, title="Can cancel jobs", description="Off makes the provider unable to cancel a job it already accepted")
    fail_from_shot: int = Field(default=0, ge=0, title="Fail from request", description="When above 0, this request and every later one fails with the Fail with kind (failed when that is empty)")


def image_bytes(seed: str, size: int = IMAGE_SIZE) -> bytes:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    base = (digest[0], digest[1], digest[2])
    accent = (digest[3], digest[4], digest[5])
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            mix = (x + y) / (2 * (size - 1))
            pixels[x, y] = tuple(int(base[i] * (1 - mix) + accent[i] * mix) for i in range(3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def e2e_specs() -> list[CloudModelSpec]:
    full = next(spec for spec in fake_specs() if spec.provider_model_id == "fake/image-1")
    lite = replace(
        full,
        provider_model_id="fake/lite-1",
        label="Fake Lite",
        description="A cheaper model that only draws from text",
        tasks=frozenset({"txt2img"}),
        params=tuple(
            replace(param, values=("1:1", "4:3")) if param.name == "aspect_ratio" else param
            for param in full.params
            if param.name in ("aspect_ratio", "x.style")
        ),
        inputs=(),
        pricing=tuple(replace(line, usd=Decimal("0.01")) for line in full.pricing),
    )
    return [full, lite, *fake_director_specs()]


class E2eFakeProvider(FakeCloudProvider):
    key: ClassVar[str] = "fake"
    label: ClassVar[str] = "Fake cloud"
    config_class: ClassVar[type] = E2eFakeConfig
    data_notice: ClassVar[str] = "Test provider: nothing leaves this machine."

    def __init__(self, config: E2eFakeConfig, http: Any) -> None:
        behaviour = FakeBehaviour(
            mode=config.mode,
            queue_s=config.queue_seconds,
            duration_s=config.duration_seconds,
            poll_after_s=config.poll_seconds,
            fail_kind=config.fail_kind or None,
            cost_usd=Decimal(str(config.cost_usd)),
            real_video=True,
            strict_capabilities=True,
            fail_from_submit=config.fail_from_shot or None,
        )
        super().__init__(config, http, clock=MonotonicClock(), behaviour=behaviour)
        self.supports_cancel = config.supports_cancel

    async def discover(self) -> list[CloudModelSpec]:
        self.behaviour.calls.append("discover")
        return e2e_specs()

    def _result(self, job_id: str, request_count: int, modality: str) -> CloudResult:
        if modality == "video":
            return super()._result(job_id, request_count, modality)
        artifacts = tuple(
            CloudArtifact(modality="image", index=index, data=image_bytes(f"{job_id}:{index}"), media_type="image/png")
            for index in range(max(1, request_count))
        )
        return CloudResult(
            artifacts=artifacts,
            cost=CloudCost(amount_usd=self.behaviour.cost_usd, source="provider"),
            provider_job_id=job_id,
        )
