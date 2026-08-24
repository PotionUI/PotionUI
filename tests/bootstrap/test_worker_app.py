"""End-to-end worker app tests: auth, handshake, submit lifecycle, resume,
cancel, journal-restart idempotency. Every pipe is a fake/stub - no torch, no
model file, no PotionUI database, no real port binding (TestClient only).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
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
from src.platform.worker_protocol.envelope import envelope, read_envelope

TOKEN = "secret-worker-token"


class FakeCatalog:
    """Duck-types the slice of PipeCatalog the fingerprint functions and the
    executor actually touch: get_pipe, .pipes, get_available_pipes,
    .pipe_sources, remote_relevant_plugin_ids."""

    def __init__(self, classes):
        self.pipes = dict(classes)
        self.pipe_sources = {}

    def get_pipe(self, name):
        return self.pipes.get(name)

    def get_available_pipes(self):
        return list(self.pipes.values())

    def remote_relevant_plugin_ids(self):
        return set()


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


class GatedPipe:
    """Blocks in `process` until the test releases it - lets a test hold the
    worker's single slot open long enough to prove a second submit is busy."""

    name = "gated/fake"
    gate = threading.Event()

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
        type(self).gate.wait(timeout=5)
        return PipeOutput(output={})


class AssetAwarePipe:
    """Records the config value it actually received, so a test can prove the
    executor handed it a staged filesystem path rather than an unresolved
    ``asset://`` token."""

    name = "asset/fake"
    received_input_path: str | None = None

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
        from src.pipelines.contracts import PipeConfigSpec

        return [PipeConfigSpec(name="input_path", param_type=str, default="")]

    def process(self, pipe_input, generation_outputs):
        type(self).received_input_path = self.config["input_path"]
        return PipeOutput(output={})


CATALOG = FakeCatalog({
    "counting/fake": CountingPipe, "gated/fake": GatedPipe, "asset/fake": AssetAwarePipe,
})

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
        gpu_manager=None, system_monitor=None,
    )


@pytest.fixture
def client(container: WorkerContainer) -> TestClient:
    app = create_worker_app(container=container)
    return TestClient(app)


def _auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _package(
    container: WorkerContainer,
    *,
    execution_id: str,
    pipe_type: str = "counting/fake",
    pipe_config: dict | None = None,
    digest_hex: str = "11" * 32,
    required_fingerprints: dict | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    limits: ExecutionLimitsV1 | None = None,
    input_assets: InputAssetManifestV1 | None = None,
) -> ExecutionPackageV1:
    now = issued_at or datetime.now(timezone.utc)
    return ExecutionPackageV1(
        execution_id=execution_id,
        idempotency_key=execution_id,
        request_digest=ContentDigest(algorithm="sha256", hex=digest_hex),
        issued_at=now,
        expires_at=expires_at if expires_at is not None else now + timedelta(hours=1),
        required_fingerprints=(
            required_fingerprints
            if required_fingerprints is not None
            else container.coordinator.fingerprints()
        ),
        model_bundle=MODEL_BUNDLE,
        processed_pipes=ProcessedPipelineV1(pipes=(
            ProcessedPipeV1(pipe_id="p1", pipe_type=pipe_type, config=pipe_config or {}, inputs={}),
        )),
        limits=limits or ExecutionLimitsV1(),
        input_assets=input_assets,
    )


def _asset_entry(logical_id: str, content: bytes) -> InputAssetV1:
    import hashlib

    return InputAssetV1(
        logical_id=logical_id,
        relative_path=f"inputs/{logical_id}/{logical_id}.bin",
        digest=ContentDigest(algorithm="sha256", hex=hashlib.sha256(content).hexdigest()),
        size_bytes=len(content),
    )


def _wait_for_terminal(container: WorkerContainer, execution_id: str) -> None:
    import time

    for _ in range(100):
        record = container.coordinator.record_for(execution_id)
        if record and record.is_terminal:
            return
        time.sleep(0.02)
    raise AssertionError(f"execution {execution_id!r} never reached a terminal state")


# -- auth -----------------------------------------------------------------

def test_worker_info_requires_a_token(client):
    resp = client.get("/v1/worker")
    assert resp.status_code == 401


def test_worker_info_rejects_a_wrong_token(client):
    resp = client.get("/v1/worker", headers=_auth("not-the-token"))
    assert resp.status_code == 401


# -- handshake --------------------------------------------------------------

def test_worker_info_validates_and_carries_all_three_fingerprint_domains(client):
    resp = client.get("/v1/worker", headers=_auth())
    assert resp.status_code == 200
    info = read_envelope(resp.json())
    assert info.worker_id == "worker-test"
    assert info.provider == "manual"
    assert set(info.fingerprints.keys()) == {"pipe_catalog", "plugin_bundle", "build"}


# -- submit happy path --------------------------------------------------------

def test_submit_runs_to_succeeded_with_a_downloadable_artifact(client, container):
    from PIL import Image
    from src.pipelines.contracts import IOType, PipeConfigSpec, PipeOutputSpec

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
            from src.pipelines.outputs import ImageGenerationOutput

            image = Image.new("RGB", (2, 2))
            generation_outputs(ImageGenerationOutput(image=image, temporary=False))
            return PipeOutput(output={"image": image})

    CATALOG.pipes["image/fake"] = ImagePipe
    package = _package(container, execution_id="exec-happy", pipe_type="image/fake")

    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 202
    assert resp.json()["outcome"] == "accepted"

    events_resp = client.get(f"/v1/executions/exec-happy/events?after=0", headers=_auth())
    lines = [l for l in events_resp.text.split("\n\n") if l.startswith("data: ")]
    kinds = []
    artifact = None
    import json as jsonlib
    for line in lines:
        doc = jsonlib.loads(line[len("data: "):])
        event = read_envelope(doc)
        kinds.append(event.kind)
        if event.kind == "artifact":
            artifact = event.artifacts[0]

    assert kinds[0] == "accepted"
    assert kinds[-1] == "succeeded"
    assert artifact is not None

    art_resp = client.get(artifact.uri, headers=_auth())
    assert art_resp.status_code == 200
    import hashlib
    assert hashlib.sha256(art_resp.content).hexdigest() == artifact.digest.hex


# -- idempotency --------------------------------------------------------------

def test_resubmitting_the_same_digest_does_not_re_execute(client, container):
    CountingPipe.calls = 0
    package = _package(container, execution_id="exec-idem", digest_hex="22" * 32)

    first = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert first.status_code == 202

    for _ in range(50):
        if container.coordinator.record_for("exec-idem") and container.coordinator.record_for("exec-idem").is_terminal:
            break
        import time
        time.sleep(0.02)

    assert CountingPipe.calls == 1

    second = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate"
    assert CountingPipe.calls == 1


def test_a_different_digest_on_the_same_execution_id_is_a_conflict(client, container):
    first = _package(container, execution_id="exec-conflict", digest_hex="33" * 32)
    client.post("/v1/executions", json=envelope(first), headers=_auth())

    second = _package(container, execution_id="exec-conflict", digest_hex="44" * 32)
    resp = client.post("/v1/executions", json=envelope(second), headers=_auth())
    assert resp.status_code == 409


# -- busy -----------------------------------------------------------------

def test_a_second_submit_while_one_is_running_is_busy(client, container):
    GatedPipe.gate = threading.Event()
    holding = _package(container, execution_id="exec-hold", pipe_type="gated/fake", digest_hex="55" * 32)
    resp = client.post("/v1/executions", json=envelope(holding), headers=_auth())
    assert resp.status_code == 202

    other = _package(container, execution_id="exec-other", digest_hex="66" * 32)
    busy_resp = client.post("/v1/executions", json=envelope(other), headers=_auth())
    assert busy_resp.status_code == 429

    GatedPipe.gate.set()


# -- fingerprint mismatch -----------------------------------------------------

def test_a_fingerprint_mismatch_rejects_without_executing(client, container):
    CountingPipe.calls = 0
    package = _package(
        container, execution_id="exec-mismatch", digest_hex="77" * 32,
        required_fingerprints={"pipe_catalog": "not-the-real-value"},
    )

    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "rejected"
    assert CountingPipe.calls == 0

    record = container.coordinator.record_for("exec-mismatch")
    assert record.events[-1].kind == "rejected"
    mismatch = record.events[-1].error.fingerprint_mismatch
    assert mismatch.domain == "pipe_catalog"
    assert mismatch.expected == "not-the-real-value"
    assert mismatch.actual == container.coordinator.fingerprints()["pipe_catalog"]


# -- expiry -----------------------------------------------------------------

def test_an_expired_package_is_rejected(client, container):
    now = datetime.now(timezone.utc)
    package = _package(
        container, execution_id="exec-expired", digest_hex="88" * 32,
        issued_at=now - timedelta(hours=2), expires_at=now - timedelta(hours=1),
    )
    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "expired"

    record = container.coordinator.record_for("exec-expired")
    assert record.events[-1].error.code == "expired"


# -- resume ---------------------------------------------------------------

def test_resume_after_a_cursor_replays_only_later_events(client, container):
    package = _package(container, execution_id="exec-resume", digest_hex="99" * 32)
    client.post("/v1/executions", json=envelope(package), headers=_auth())

    for _ in range(50):
        record = container.coordinator.record_for("exec-resume")
        if record and record.is_terminal:
            break
        import time
        time.sleep(0.02)

    record = container.coordinator.record_for("exec-resume")
    assert len(record.events) >= 3
    resume_after = record.events[0].cursor

    resp = client.get(f"/v1/executions/exec-resume/events?after={resume_after}", headers=_auth())
    import json as jsonlib
    lines = [l for l in resp.text.split("\n\n") if l.startswith("data: ")]
    cursors = [read_envelope(jsonlib.loads(l[len("data: "):])).cursor for l in lines]
    assert all(c > resume_after for c in cursors)
    assert cursors == sorted(cursors)


# -- cancel -----------------------------------------------------------------

# -- input assets -------------------------------------------------------------

def test_a_package_without_a_manifest_skips_staging_and_runs_straight_through(client, container):
    CountingPipe.calls = 0
    package = _package(container, execution_id="exec-no-manifest", digest_hex="dd" * 32)

    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 202

    _wait_for_terminal(container, "exec-no-manifest")
    record = container.coordinator.record_for("exec-no-manifest")
    kinds = [e.kind for e in record.events]
    assert kinds == ["accepted", "staging", "running", "pipe_started", "succeeded"]
    assert CountingPipe.calls == 1


def test_asset_upload_for_an_unknown_logical_id_is_not_found(client, container):
    entry = _asset_entry("asset1", b"payload")
    package = _package(
        container, execution_id="exec-asset-unknown", digest_hex="d1" * 32,
        input_assets=InputAssetManifestV1(assets=(entry,)),
    )
    client.post("/v1/executions", json=envelope(package), headers=_auth())

    resp = client.post(
        "/v1/executions/exec-asset-unknown/assets/no-such-asset", content=b"data", headers=_auth(),
    )
    assert resp.status_code == 404


def test_asset_upload_with_the_wrong_content_is_rejected_and_execution_does_not_start(client, container):
    entry = _asset_entry("asset1", b"the real bytes")
    package = _package(
        container, execution_id="exec-asset-digest", digest_hex="d2" * 32,
        pipe_type="asset/fake", pipe_config={"input_path": "asset://asset1"},
        input_assets=InputAssetManifestV1(assets=(entry,)),
    )
    client.post("/v1/executions", json=envelope(package), headers=_auth())

    resp = client.post(
        "/v1/executions/exec-asset-digest/assets/asset1",
        content=b"the fake bytes", headers=_auth(),  # same length as "the real bytes"
    )
    assert resp.status_code == 422
    assert "digest mismatch" in resp.json()["detail"]["reason"]

    record = container.coordinator.record_for("exec-asset-digest")
    assert record.latest_kind == "staging"


def test_submit_waits_for_every_asset_before_running_and_the_pipe_gets_a_staged_path(client, container):
    AssetAwarePipe.received_input_path = None
    content = b"a small fake input file"
    entry = _asset_entry("asset1", content)
    package = _package(
        container, execution_id="exec-asset-happy", digest_hex="d3" * 32,
        pipe_type="asset/fake", pipe_config={"input_path": "asset://asset1"},
        input_assets=InputAssetManifestV1(assets=(entry,)),
    )

    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 202

    import time
    for _ in range(100):
        record = container.coordinator.record_for("exec-asset-happy")
        if record and record.latest_kind == "staging":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("execution never reached the staging status")

    time.sleep(0.05)
    assert container.coordinator.record_for("exec-asset-happy").latest_kind == "staging"
    assert AssetAwarePipe.received_input_path is None

    upload = client.post(
        "/v1/executions/exec-asset-happy/assets/asset1", content=content, headers=_auth(),
    )
    assert upload.status_code == 200
    assert upload.json() == {"logical_id": "asset1", "staged": True}

    _wait_for_terminal(container, "exec-asset-happy")
    record = container.coordinator.record_for("exec-asset-happy")
    assert record.events[-1].kind == "succeeded"

    staged_path = AssetAwarePipe.received_input_path
    assert staged_path is not None
    assert not staged_path.startswith("asset://")
    assert Path(staged_path).read_bytes() == content

    # a re-upload of the same, already-staged asset is idempotent
    reupload = client.post(
        "/v1/executions/exec-asset-happy/assets/asset1", content=content, headers=_auth(),
    )
    assert reupload.status_code == 200


def test_asset_upload_after_a_restart_is_unknown_execution(container):
    entry = _asset_entry("asset1", b"payload")
    package = _package(
        container, execution_id="exec-asset-restart", digest_hex="d4" * 32,
        input_assets=InputAssetManifestV1(assets=(entry,)),
    )
    first_app = create_worker_app(container=container)
    first_client = TestClient(first_app)
    first_client.post("/v1/executions", json=envelope(package), headers=_auth())

    new_journal = WorkerJournal(container.config.work_dir)
    new_coordinator = WorkerCoordinator(
        worker_id=container.config.worker_id, pipe_catalog=CATALOG, journal=new_journal,
        artifacts_dir=container.config.artifacts_dir, device=container.config.device,
        dtype=container.config.dtype, vram_limit_gb=container.config.vram_limit_gb,
        build_id=container.config.build_id,
    )
    new_container = WorkerContainer(
        config=container.config, pipe_catalog=CATALOG, journal=new_journal,
        coordinator=new_coordinator, gpu_manager=None, system_monitor=None,
    )
    new_client = TestClient(create_worker_app(container=new_container))

    resubmit = new_client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resubmit.status_code == 200
    assert resubmit.json()["outcome"] == "duplicate"

    upload = new_client.post(
        "/v1/executions/exec-asset-restart/assets/asset1", content=b"payload", headers=_auth(),
    )
    assert upload.status_code == 404
    assert upload.json()["detail"] == "unknown execution"


def test_cancel_on_an_unknown_execution_is_not_found(client):
    resp = client.post("/v1/executions/never-existed/cancel", headers=_auth())
    assert resp.status_code == 404
    assert resp.json()["result"] == "not_found"


def test_cancel_while_running_flags_the_execution(client, container):
    GatedPipe.gate = threading.Event()
    package = _package(container, execution_id="exec-cancel", pipe_type="gated/fake", digest_hex="aa" * 32)
    client.post("/v1/executions", json=envelope(package), headers=_auth())

    resp = client.post("/v1/executions/exec-cancel/cancel", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["result"] == "accepted"

    GatedPipe.gate.set()
    for _ in range(50):
        record = container.coordinator.record_for("exec-cancel")
        if record and record.is_terminal:
            break
        import time
        time.sleep(0.02)

    assert container.coordinator.record_for("exec-cancel").events[-1].kind == "cancelled"


def test_cancel_after_terminal_is_already_terminal(client, container):
    package = _package(container, execution_id="exec-terminal", digest_hex="bb" * 32)
    client.post("/v1/executions", json=envelope(package), headers=_auth())
    for _ in range(50):
        record = container.coordinator.record_for("exec-terminal")
        if record and record.is_terminal:
            break
        import time
        time.sleep(0.02)

    resp = client.post("/v1/executions/exec-terminal/cancel", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["result"] == "already_terminal"


# -- journal restart ----------------------------------------------------------

def test_a_new_app_instance_over_the_same_work_dir_is_idempotent(container):
    package = _package(container, execution_id="exec-restart", digest_hex="cc" * 32)
    first_app = create_worker_app(container=container)
    first_client = TestClient(first_app)
    first_client.post("/v1/executions", json=envelope(package), headers=_auth())

    for _ in range(50):
        record = container.coordinator.record_for("exec-restart")
        if record and record.is_terminal:
            break
        import time
        time.sleep(0.02)

    new_journal = WorkerJournal(container.config.work_dir)
    new_coordinator = WorkerCoordinator(
        worker_id=container.config.worker_id, pipe_catalog=CATALOG, journal=new_journal,
        artifacts_dir=container.config.artifacts_dir, device=container.config.device,
        dtype=container.config.dtype, vram_limit_gb=container.config.vram_limit_gb,
        build_id=container.config.build_id,
    )
    new_container = WorkerContainer(
        config=container.config, pipe_catalog=CATALOG, journal=new_journal,
        coordinator=new_coordinator, gpu_manager=None, system_monitor=None,
    )
    new_app = create_worker_app(container=new_container)
    new_client = TestClient(new_app)

    resp = new_client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "duplicate"
