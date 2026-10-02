import hashlib
import os
import shutil
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional, Sequence

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
from src.features.cloud.capability_rules import find_input, find_param, validate_param_value
from src.features.cloud.http import CloudHttp

def _png_1x1() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\xff\xff\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


PNG_1X1 = _png_1x1()
VIDEO_FPS = 8
VIDEO_SIZE = (32, 24)
VIDEO_ROLES = ("first_frame", "last_frame")
SAMPLE_RATE = 16000


def _colour(text: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return digest[0], digest[1], digest[2]


def image_colour(path: Path) -> tuple[int, int, int]:
    from PIL import Image

    with Image.open(path) as image:
        small = image.convert("RGB").resize((1, 1))
        return small.getpixel((0, 0))


def _encode_with_ffmpeg(frames: Any, path: str, fps: int, sound: bool) -> None:
    import numpy as np

    from src.pipelines.pipes._shared.media.video_encode import AudioTrack, encode_frames_to_mp4

    audio = None
    if sound:
        samples = int(SAMPLE_RATE * len(frames) / fps)
        tone = 0.05 * np.sin(2 * np.pi * 440 * np.arange(samples) / SAMPLE_RATE)
        audio = AudioTrack(waveform=np.stack([tone, tone]).astype(np.float32), sample_rate=SAMPLE_RATE)
    encode_frames_to_mp4(frames, path, float(fps), audio=audio)


def _encode_with_opencv(frames: Any, path: str, fps: int, size: tuple[int, int]) -> None:
    import cv2

    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    try:
        for frame in frames:
            writer.write(frame[:, :, ::-1])
    finally:
        writer.release()


def fake_video_bytes(
    seconds: float,
    *,
    start: Sequence[int],
    end: Sequence[int],
    fps: int = VIDEO_FPS,
    size: tuple[int, int] = VIDEO_SIZE,
    sound: bool = False,
) -> bytes:
    import numpy as np

    count = max(2, int(round(float(seconds) * fps)))
    frames = np.stack([
        np.full(
            (size[1], size[0], 3),
            [int(round(a * (1 - index / (count - 1)) + b * index / (count - 1))) for a, b in zip(start, end)],
            dtype=np.uint8,
        )
        for index in range(count)
    ])
    handle, name = tempfile.mkstemp(suffix=".mp4", prefix="potionui-fake-video-")
    os.close(handle)
    try:
        if shutil.which("ffmpeg") is not None:
            _encode_with_ffmpeg(frames, name, fps, sound)
        else:
            _encode_with_opencv(frames, name, fps, size)
        return Path(name).read_bytes()
    finally:
        Path(name).unlink(missing_ok=True)


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
    fail_times: Optional[int] = None
    request_sent: bool = True
    keys: list[str] = field(default_factory=list)
    requests: list[Any] = field(default_factory=list)
    fetch_limits: list[Optional[int]] = field(default_factory=list)
    cost_usd: Decimal = Decimal("0.04")
    calls: list[str] = field(default_factory=list)
    real_video: bool = False
    strict_capabilities: bool = False
    fail_from_submit: Optional[int] = None
    specs: Optional[list[CloudModelSpec]] = None


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
            inputs=(
                MediaInputSpec(role="first_frame", modality="image", tasks=frozenset({"img2video"})),
                MediaInputSpec(role="last_frame", modality="image", tasks=frozenset({"img2video"})),
            ),
            pricing=(PriceLine(unit="second", usd=Decimal("0.40")),),
            typical_seconds=90,
            max_seconds=600,
        ),
    ]


def fake_director_specs() -> list[CloudModelSpec]:
    return [
        CloudModelSpec(
            provider_model_id="fake/director-1",
            label="Fake Director",
            vendor="fake",
            description="Scripted video model with start and end frames, sound and fixed clip lengths",
            tasks=frozenset({"txt2video", "img2video"}),
            outputs=frozenset({"video"}),
            params=(
                ParamSpec(name="duration_s", kind="enum", values=(2, 4, 6), default=4),
                ParamSpec(name="aspect_ratio", kind="enum", values=("16:9", "9:16"), default="16:9"),
                ParamSpec(name="generate_audio", kind="boolean", default=False),
            ),
            inputs=(
                MediaInputSpec(role="first_frame", modality="image", tasks=frozenset({"img2video"})),
                MediaInputSpec(role="last_frame", modality="image", tasks=frozenset({"img2video"})),
            ),
            pricing=(PriceLine(unit="second", usd=Decimal("0.10")),),
            typical_seconds=30,
            max_seconds=600,
            director={"modes": {"director": {"max_segments": 4}}},
        ),
        CloudModelSpec(
            provider_model_id="fake/director-start-1",
            label="Fake Director Start",
            vendor="fake",
            description="Scripted video model that takes a start frame but no end frame",
            tasks=frozenset({"txt2video", "img2video"}),
            outputs=frozenset({"video"}),
            params=(ParamSpec(name="duration_s", kind="enum", values=(3, 5), default=3),),
            inputs=(MediaInputSpec(role="first_frame", modality="image", tasks=frozenset({"img2video"})),),
            pricing=(PriceLine(unit="request", usd=Decimal("0.25")),),
            typical_seconds=30,
            max_seconds=600,
        ),
        CloudModelSpec(
            provider_model_id="fake/director-text-1",
            label="Fake Director Text",
            vendor="fake",
            description="Scripted text-only video model with an unknown price",
            tasks=frozenset({"txt2video"}),
            outputs=frozenset({"video"}),
            params=(ParamSpec(name="duration_s", kind="enum", values=(5,), default=5),),
            typical_seconds=30,
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
        self._videos: dict[str, bytes] = {}

    def _maybe_fail(self, stage: str) -> None:
        behaviour = self.behaviour
        if stage == "submit" and behaviour.fail_from_submit is not None:
            return
        if behaviour.fail_kind and behaviour.fail_stage == stage:
            if behaviour.fail_times is not None:
                if behaviour.fail_times <= 0:
                    return
                behaviour.fail_times -= 1
            raise CloudError(
                behaviour.fail_kind,
                FAKE_USER_MESSAGES[behaviour.fail_kind],
                detail=f"scripted failure at {stage}",
                retry_after_s=behaviour.retry_after_s,
                request_sent=behaviour.request_sent,
            )

    def _payload(self, job_id: str, modality: str) -> bytes:
        if modality == "video" and job_id in self._videos:
            return self._videos[job_id]
        return PNG_1X1

    def _result(self, job_id: str, request_count: int, modality: str) -> CloudResult:
        count = min(self.behaviour.outputs, max(1, request_count))
        artifacts = tuple(
            CloudArtifact(
                modality=modality,
                index=index,
                data=self._payload(job_id, modality) if self.behaviour.mode == "sync" else None,
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
        return list(self.behaviour.specs) if self.behaviour.specs is not None else fake_specs()

    def _check_capabilities(self, request: CloudRequest) -> None:
        problems: list[str] = []
        for name, value in request.params.items():
            param = find_param(request.model, name, request.task)
            if param is None:
                problems.append(f"{name} is not offered")
            elif validate_param_value(param, value) is not None:
                problems.append(f"{name}={value!r} is not offered")
        for role, items in request.inputs.items():
            if items and find_input(request.model, role, request.task) is None:
                problems.append(f"{role} is not accepted")
        if request.task not in request.model.tasks:
            problems.append(f"{request.task} is not offered")
        if problems:
            raise CloudError("invalid_request", FAKE_USER_MESSAGES["invalid_request"], detail="; ".join(problems))

    def _fail_this_submit(self) -> None:
        behaviour = self.behaviour
        submits = behaviour.calls.count("submit")
        if behaviour.fail_from_submit is not None and submits >= behaviour.fail_from_submit:
            kind = behaviour.fail_kind or "failed"
            raise CloudError(kind, FAKE_USER_MESSAGES[kind], detail=f"scripted failure at submit {submits}")

    def _video(self, request: CloudRequest, job_id: str) -> bytes:
        frames = {role: (request.inputs.get(role) or [None])[0] for role in VIDEO_ROLES}
        start = image_colour(frames["first_frame"].path) if frames["first_frame"] else _colour(f"{job_id}:start")
        end = image_colour(frames["last_frame"].path) if frames["last_frame"] else _colour(f"{job_id}:end")
        seconds = request.params.get("duration_s") or 2
        return fake_video_bytes(float(seconds), start=start, end=end, sound=request.params.get("generate_audio") is True)

    async def submit(self, request: CloudRequest) -> CloudJob:
        self.behaviour.calls.append("submit")
        self.behaviour.keys.append(request.idempotency_key)
        self.behaviour.requests.append(request)
        self._maybe_fail("submit")
        self._fail_this_submit()
        modality = "video" if "video" in request.task else "image"
        if self.behaviour.strict_capabilities and modality == "video":
            self._check_capabilities(request)
        self._counter += 1
        job_id = f"fake-job-{self._counter}"
        if modality == "video" and self.behaviour.real_video:
            self._videos[job_id] = self._video(request, job_id)
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
        self.behaviour.fetch_limits.append(max_bytes)
        self._maybe_fail("fetch")
        job_id = (artifact.url or "").rsplit("/", 2)[-2] if artifact.url else ""
        payload = artifact.data if artifact.data is not None else self._payload(job_id, artifact.modality)
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


class FakeIdempotentProvider(FakeCloudProvider):
    key: ClassVar[str] = "fake"
    idempotent_submit: ClassVar[bool] = True


def build_fake_provider(
    behaviour: Optional[FakeBehaviour] = None,
    *,
    clock: Optional[FakeClock] = None,
    supports_cancel: bool = True,
    idempotent: bool = False,
) -> FakeCloudProvider:
    config = FakeCloudConfig(id="fake-1", name="Fake")
    http = CloudHttp("https://fake.invalid", max_parallel=config.max_parallel)
    provider_class = FakeIdempotentProvider if idempotent else FakeCloudProvider if supports_cancel else FakeNoCancelProvider
    return provider_class(config, http, clock=clock or FakeClock(), behaviour=behaviour)
