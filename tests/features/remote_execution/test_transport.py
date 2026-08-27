"""WorkerTransport against a REAL worker app, reached over an in-process ASGI
transport (httpx.ASGITransport) - never a real socket. Every pipe is a
fake/stub, same convention as tests/bootstrap/test_worker_app.py.

This is deliberately not a re-test of the worker's own route behaviour (that's
test_worker_app.py's job) - it proves WorkerTransport translates what the
worker actually sends back into the typed results/errors core's dispatch code
depends on.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.remote_execution.transport import (
    ArtifactVerificationError,
    WorkerProtocolError,
    WorkerTransport,
    WorkerUnreachableError,
)
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.pipelines.contracts import IOType, PipeOutputSpec
from src.pipelines.contracts import PipeOutput
from src.platform.worker_protocol import (
    ContentDigest,
    ExecutionLimitsV1,
    ExecutionPackageV1,
    InputAssetManifestV1,
    InputAssetV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)

TOKEN = "secret-worker-token"


class CountingPipe:
    name = "counting/fake"
    calls = 0

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        type(self).calls += 1
        return PipeOutput(output={})


class ImagePipe:
    name = "image/fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="image", io_type=IOType.IMAGE)]

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        from PIL import Image
        from src.pipelines.outputs import ImageGenerationOutput

        image = Image.new("RGB", (2, 2))
        generation_outputs(ImageGenerationOutput(image=image, temporary=False))
        return PipeOutput(output={"image": image})


class FakeCatalog:
    def __init__(self, classes):
        self.pipes = dict(classes)
        self.pipe_sources = {}

    def get_pipe(self, name):
        return self.pipes.get(name)

    def get_available_pipes(self):
        return list(self.pipes.values())

    def remote_relevant_plugin_ids(self):
        return set()


CATALOG = FakeCatalog({"counting/fake": CountingPipe, "image/fake": ImagePipe})

MODEL_BUNDLE = ModelBundleManifestV1(
    bundle_id="bundle-1", bundle_digest=ContentDigest(algorithm="sha256", hex="ab" * 32), entries=(),
)


@pytest.fixture
def container(tmp_path: Path) -> WorkerContainer:
    config = WorkerConfig(
        token=TOKEN, worker_id="worker-test", provider="manual", host="127.0.0.1", port=0,
        work_dir=tmp_path, artifacts_dir=tmp_path / "artifacts", build_id="test-build",
        device="cpu", dtype="fp32", vram_limit_gb=None,
    )
    journal = WorkerJournal(config.work_dir)
    coordinator = WorkerCoordinator(
        worker_id=config.worker_id, pipe_catalog=CATALOG, journal=journal,
        artifacts_dir=config.artifacts_dir, device=config.device, dtype=config.dtype,
        vram_limit_gb=config.vram_limit_gb, build_id=config.build_id,
    )
    return WorkerContainer(
        config=config, pipe_catalog=CATALOG, journal=journal, coordinator=coordinator,
        gpu_monitor=None, system_monitor=None,
    )


@pytest.fixture
def transport(container: WorkerContainer) -> WorkerTransport:
    app = create_worker_app(container=container)
    return WorkerTransport(
        "http://fake-worker", TOKEN, transport=httpx.ASGITransport(app=app),
    )


def _package(
    container: WorkerContainer, *, execution_id: str, pipe_type: str = "counting/fake",
    digest_hex: str = "11" * 32, input_assets=None,
) -> ExecutionPackageV1:
    now = datetime.now(timezone.utc)
    return ExecutionPackageV1(
        execution_id=execution_id, idempotency_key=execution_id,
        request_digest=ContentDigest(algorithm="sha256", hex=digest_hex),
        issued_at=now, expires_at=now + timedelta(hours=1),
        required_fingerprints=container.coordinator.fingerprints(),
        model_bundle=MODEL_BUNDLE,
        processed_pipes=ProcessedPipelineV1(pipes=(
            ProcessedPipeV1(pipe_id="p1", pipe_type=pipe_type, config={}, inputs={}),
        )),
        limits=ExecutionLimitsV1(), input_assets=input_assets,
    )


async def _wait_for_terminal(container, execution_id):
    import asyncio

    for _ in range(100):
        record = container.coordinator.record_for(execution_id)
        if record and record.is_terminal:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{execution_id} never reached a terminal state")


# -- handshake --------------------------------------------------------------

class TestHandshake:
    @pytest.mark.asyncio
    async def test_reads_a_real_worker_info(self, transport):
        info = await transport.handshake()

        assert info.worker_id == "worker-test"
        assert set(info.fingerprints.keys()) == {"pipe_catalog", "plugin_bundle", "build"}

    @pytest.mark.asyncio
    async def test_a_wrong_token_is_unreachable_not_a_crash(self, container):
        app = create_worker_app(container=container)
        bad = WorkerTransport("http://fake-worker", "wrong-token", transport=httpx.ASGITransport(app=app))

        with pytest.raises(WorkerProtocolError):
            await bad.handshake()


# -- submit + events ----------------------------------------------------------

class TestSubmitAndEvents:
    @pytest.mark.asyncio
    async def test_submit_reports_accepted_then_terminal_events_stream_in_order(self, transport, container):
        package = _package(container, execution_id="exec-1")

        result = await transport.submit(package)
        assert result.status_code == 202
        assert result.outcome == "accepted"

        await _wait_for_terminal(container, "exec-1")

        kinds = [event.kind async for event in transport.stream_events("exec-1", after=0)]
        assert kinds[0] == "accepted"
        assert kinds[-1] == "succeeded"
        assert kinds == sorted(kinds, key=kinds.index)  # arrived in cursor order

    @pytest.mark.asyncio
    async def test_a_digest_conflict_is_reported_as_its_own_outcome(self, transport, container):
        first = _package(container, execution_id="exec-conflict", digest_hex="22" * 32)
        await transport.submit(first)

        second = _package(container, execution_id="exec-conflict", digest_hex="33" * 32)
        result = await transport.submit(second)

        assert result.status_code == 409
        assert result.outcome == "digest_conflict"

    @pytest.mark.asyncio
    async def test_resume_after_a_cursor_only_replays_later_events(self, transport, container):
        package = _package(container, execution_id="exec-resume", digest_hex="44" * 32)
        await transport.submit(package)
        await _wait_for_terminal(container, "exec-resume")

        all_events = [event async for event in transport.stream_events("exec-resume", after=0)]
        resume_after = all_events[0].cursor

        resumed = [event async for event in transport.stream_events("exec-resume", after=resume_after)]
        assert all(e.cursor > resume_after for e in resumed)
        assert len(resumed) == len(all_events) - 1


# -- cancel -----------------------------------------------------------------

class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_on_an_unknown_execution(self, transport):
        assert await transport.cancel("never-existed") == "not_found"


# -- artifact download --------------------------------------------------------

class TestArtifactDownload:
    @pytest.mark.asyncio
    async def test_a_downloaded_artifact_matches_its_digest(self, transport, container, tmp_path):
        package = _package(container, execution_id="exec-artifact", pipe_type="image/fake", digest_hex="55" * 32)
        await transport.submit(package)
        await _wait_for_terminal(container, "exec-artifact")

        artifact = None
        async for event in transport.stream_events("exec-artifact", after=0):
            if event.kind == "artifact":
                artifact = event.artifacts[0]
        assert artifact is not None

        dest = tmp_path / "downloaded.png"
        await transport.download_artifact(artifact, dest)

        assert dest.exists()
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == artifact.digest.hex
        assert not dest.with_name(dest.name + ".part").exists()

    @pytest.mark.asyncio
    async def test_a_corrupted_digest_is_rejected_and_nothing_is_left_on_disk(self, transport, container, tmp_path):
        package = _package(container, execution_id="exec-corrupt", pipe_type="image/fake", digest_hex="66" * 32)
        await transport.submit(package)
        await _wait_for_terminal(container, "exec-corrupt")

        artifact = None
        async for event in transport.stream_events("exec-corrupt", after=0):
            if event.kind == "artifact":
                artifact = event.artifacts[0]

        tampered = artifact.model_copy(update={
            "digest": ContentDigest(algorithm="sha256", hex="0" * 64),
        })

        dest = tmp_path / "downloaded.png"
        with pytest.raises(ArtifactVerificationError):
            await transport.download_artifact(tampered, dest)

        assert not dest.exists()
        assert not dest.with_name(dest.name + ".part").exists()


# -- asset upload -------------------------------------------------------------

class TestAssetUpload:
    @pytest.mark.asyncio
    async def test_uploads_a_real_file_and_the_worker_accepts_it(self, transport, container, tmp_path):
        content = b"a small fake input file"
        source = tmp_path / "source.bin"
        source.write_bytes(content)

        entry = InputAssetV1(
            logical_id="asset1", relative_path="inputs/asset1/asset1.bin",
            digest=ContentDigest(algorithm="sha256", hex=hashlib.sha256(content).hexdigest()),
            size_bytes=len(content),
        )
        package = _package(
            container, execution_id="exec-upload", digest_hex="77" * 32,
            input_assets=InputAssetManifestV1(assets=(entry,)),
        )
        await transport.submit(package)

        await transport.upload_asset("exec-upload", "asset1", source)

        await _wait_for_terminal(container, "exec-upload")
        record = container.coordinator.record_for("exec-upload")
        assert record.events[-1].kind == "succeeded"

    @pytest.mark.asyncio
    async def test_uploading_for_an_unknown_execution_raises(self, transport, tmp_path):
        source = tmp_path / "source.bin"
        source.write_bytes(b"x")

        with pytest.raises(WorkerProtocolError):
            await transport.upload_asset("never-submitted", "asset1", source)
