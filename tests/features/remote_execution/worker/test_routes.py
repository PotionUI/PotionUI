"""The worker's model routes end to end: a REAL worker app
(TestClient, mirrors tests/bootstrap/test_worker_app.py's own convention) -
inventory, chunked staging, and path remap threaded through a real submit.
"""

from __future__ import annotations

import hashlib
import json as jsonlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot
from src.pipelines.contracts import PipeConfigSpec, PipeOutput
from src.platform.worker_protocol import (
    ContentDigest,
    ExecutionLimitsV1,
    ExecutionPackageV1,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)
from src.platform.worker_protocol.envelope import envelope, read_envelope
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1

TOKEN = "secret-worker-token"
MODEL_CONTENT = b"fake checkpoint bytes" * 100
MODEL_DIGEST = hashlib.sha256(MODEL_CONTENT).hexdigest()


class ModelAwarePipe:
    """Records the config value it actually received - proves a remapped
    path, not the dispatching host's original one, reached the pipe."""

    name = "model_aware/fake"
    received_file_path: str | None = None

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
        return [PipeConfigSpec(name="model", param_type=dict, default={})]

    def process(self, pipe_input, generation_outputs):
        type(self).received_file_path = self.config.get("model", {}).get("file_path")
        return PipeOutput(output={})


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


CATALOG = FakeCatalog({"model_aware/fake": ModelAwarePipe})

MODEL_ENTRY = ModelBundleEntryV1(
    logical_id="checkpoint/model.safetensors",
    role="checkpoint",
    relative_path="checkpoint/model.safetensors",
    digest=ContentDigest(algorithm="sha256", hex=MODEL_DIGEST),
    size_bytes=len(MODEL_CONTENT),
)
MODEL_BUNDLE = ModelBundleManifestV1(
    bundle_id="bundle-1", bundle_digest=ContentDigest(algorithm="sha256", hex="ab" * 32),
    entries=(MODEL_ENTRY,),
)
EMPTY_MODEL_BUNDLE = ModelBundleManifestV1(
    bundle_id="bundle-empty", bundle_digest=ContentDigest(algorithm="sha256", hex="cd" * 32), entries=(),
)


@pytest.fixture
def container(tmp_path: Path) -> WorkerContainer:
    config = WorkerConfig(
        token=TOKEN, worker_id="worker-test", provider="manual", host="127.0.0.1", port=0,
        work_dir=tmp_path / "work", artifacts_dir=tmp_path / "work" / "artifacts",
        build_id="test-build", device="cpu", dtype="fp32", vram_limit_gb=None,
        model_dir=tmp_path / "models",
    )
    journal = WorkerJournal(config.work_dir)
    model_depot = ModelDepot(depot_dir=config.model_dir)
    coordinator = WorkerCoordinator(
        worker_id=config.worker_id, pipe_catalog=CATALOG, journal=journal,
        artifacts_dir=config.artifacts_dir, device=config.device, dtype=config.dtype,
        vram_limit_gb=config.vram_limit_gb, build_id=config.build_id,
        model_depot=model_depot,
    )
    return WorkerContainer(
        config=config, pipe_catalog=CATALOG, journal=journal, coordinator=coordinator,
        gpu_monitor=None, system_monitor=None, model_depot=model_depot,
    )


@pytest.fixture
def client(container: WorkerContainer) -> TestClient:
    return TestClient(create_worker_app(container=container))


def _auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _package(
    container: WorkerContainer, *, execution_id: str, pipe_config: dict, model_bundle=MODEL_BUNDLE,
) -> ExecutionPackageV1:
    now = datetime.now(timezone.utc)
    return ExecutionPackageV1(
        execution_id=execution_id, idempotency_key=execution_id,
        request_digest=ContentDigest(algorithm="sha256", hex="11" * 32),
        issued_at=now, expires_at=now + timedelta(hours=1),
        required_fingerprints=container.coordinator.fingerprints(),
        model_bundle=model_bundle,
        processed_pipes=ProcessedPipelineV1(pipes=(
            ProcessedPipeV1(pipe_id="p1", pipe_type="model_aware/fake", config=pipe_config, inputs={}),
        )),
        limits=ExecutionLimitsV1(),
    )


def _wait_for_terminal(container: WorkerContainer, execution_id: str) -> None:
    for _ in range(100):
        record = container.coordinator.record_for(execution_id)
        if record and record.is_terminal:
            return
        time.sleep(0.02)
    raise AssertionError(f"execution {execution_id!r} never reached a terminal state")


# -- inventory ----------------------------------------------------------------

def test_inventory_requires_a_token(client):
    resp = client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE))
    assert resp.status_code == 401


def test_inventory_reports_missing_for_an_unstaged_bundle(client):
    resp = client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())
    assert resp.status_code == 200
    body = read_envelope(resp.json())
    assert body.bundle_id == "bundle-1"
    assert body.entries[0].status == "missing"


def test_inventory_rejects_an_envelope_of_the_wrong_kind(client, container):
    from src.platform.worker_protocol import WorkerInfoV1
    from src.features.remote_execution.worker.capabilities import probe_capabilities

    wrong_kind = WorkerInfoV1(
        worker_id="w", provider="manual", engine="native",
        capabilities=probe_capabilities(container.config.work_dir),
        fingerprints=container.coordinator.fingerprints(),
        started_at=datetime.now(timezone.utc),
    )
    resp = client.post("/v1/models/inventory", json=envelope(wrong_kind), headers=_auth())
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "wrong_kind"


# -- staging --------------------------------------------------------------

def test_uploading_an_unregistered_bundle_entry_is_not_found(client):
    resp = client.post(
        "/v1/models/never-inventoried/checkpoint/model.safetensors",
        content=MODEL_CONTENT, headers=_auth(),
    )
    assert resp.status_code == 404


def test_staging_registers_via_inventory_then_uploads_and_persists_to_the_depot(client, container):
    client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())

    resp = client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=MODEL_CONTENT, headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["logical_id"] == MODEL_ENTRY.logical_id
    assert body["staged"] is True
    assert body["transfer_id"]

    dest = container.model_depot.depot_dir / MODEL_ENTRY.relative_path
    assert dest.read_bytes() == MODEL_CONTENT

    # inventory now reports it present
    followup = client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())
    assert read_envelope(followup.json()).entries[0].status == "present"


def test_staging_a_digest_mismatch_is_rejected(client):
    client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())

    resp = client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=b"y" * len(MODEL_CONTENT),  # same length as MODEL_CONTENT, different bytes
        headers=_auth(),
    )
    assert resp.status_code == 422
    assert "digest mismatch" in resp.json()["detail"]["reason"]


def test_re_uploading_an_already_staged_entry_is_idempotent(client):
    client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())
    first = client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=MODEL_CONTENT, headers=_auth(),
    )
    second = client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=MODEL_CONTENT, headers=_auth(),
    )
    assert first.status_code == 200
    assert second.status_code == 200


# -- path remap threaded through a real submit -------------------------------

def test_a_submitted_execution_receives_the_depot_path_not_the_host_path(client, container):
    client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())
    client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=MODEL_CONTENT, headers=_auth(),
    )

    package = _package(
        container, execution_id="exec-remap-happy",
        pipe_config={"model": {"file_path": "/dispatching-host/models/checkpoint/model.safetensors"}},
    )
    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 202

    _wait_for_terminal(container, "exec-remap-happy")
    record = container.coordinator.record_for("exec-remap-happy")
    assert record.events[-1].kind == "succeeded"

    expected = str(container.model_depot.depot_dir / MODEL_ENTRY.relative_path)
    assert ModelAwarePipe.received_file_path == expected
    assert ModelAwarePipe.received_file_path != "/dispatching-host/models/checkpoint/model.safetensors"


def test_a_submitted_execution_referencing_an_unstaged_model_fails_cleanly(client, container):
    # deliberately never inventoried/staged
    package = _package(
        container, execution_id="exec-remap-missing",
        pipe_config={"model": {"file_path": "/dispatching-host/models/checkpoint/model.safetensors"}},
    )
    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 202

    _wait_for_terminal(container, "exec-remap-missing")
    record = container.coordinator.record_for("exec-remap-missing")
    assert record.events[-1].kind == "failed"
    assert record.events[-1].error.code == "model_not_staged"
    assert record.events[-1].error.retryable is False


def test_a_pipeline_with_no_model_references_is_unaffected_by_the_depot(client, container):
    package = _package(
        container, execution_id="exec-no-models",
        pipe_config={"model": {}}, model_bundle=EMPTY_MODEL_BUNDLE,
    )
    resp = client.post("/v1/executions", json=envelope(package), headers=_auth())
    assert resp.status_code == 202

    _wait_for_terminal(container, "exec-no-models")
    record = container.coordinator.record_for("exec-no-models")
    assert record.events[-1].kind == "succeeded"


# -- depot listing --------------------------------------------------------

def test_listing_an_empty_depot_returns_no_entries(client):
    resp = client.get("/v1/models", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"entries": []}


def test_listing_the_depot_reports_a_staged_entry_with_its_digest(client):
    client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())
    client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=MODEL_CONTENT, headers=_auth(),
    )

    resp = client.get("/v1/models", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["entries"] == [{
        "relative_path": MODEL_ENTRY.relative_path,
        "size_bytes": len(MODEL_CONTENT),
        "digest": MODEL_DIGEST,
    }]


# -- upload transfer tracking ----------------------------------------------

def test_a_staged_upload_reports_a_completed_transfer_with_full_bytes_received(client):
    client.post("/v1/models/inventory", json=envelope(MODEL_BUNDLE), headers=_auth())
    resp = client.post(
        f"/v1/models/{MODEL_BUNDLE.bundle_id}/{MODEL_ENTRY.logical_id}",
        content=MODEL_CONTENT, headers=_auth(),
    )
    transfer_id = resp.json()["transfer_id"]

    transfer = client.get(f"/v1/models/transfers/{transfer_id}", headers=_auth()).json()
    assert transfer["kind"] == "upload"
    assert transfer["state"] == "completed"
    assert transfer["received_bytes"] == transfer["total_bytes"] == len(MODEL_CONTENT)
    assert transfer["digest"] == MODEL_DIGEST
    assert transfer["size_bytes"] == len(MODEL_CONTENT)

    listing = client.get("/v1/models/transfers", headers=_auth()).json()["transfers"]
    assert any(t["id"] == transfer_id for t in listing)


def test_an_unknown_transfer_id_is_not_found(client):
    resp = client.get("/v1/models/transfers/does-not-exist", headers=_auth())
    assert resp.status_code == 404


# -- fetch ------------------------------------------------------------------

FETCH_CONTENT = b"fetched checkpoint bytes" * 5000
FETCH_DIGEST = hashlib.sha256(FETCH_CONTENT).hexdigest()


def _fetch_request(
    *,
    relative_path: str = "fetched/model.safetensors",
    url: str = "https://example.invalid/model.safetensors",
    digest: str = FETCH_DIGEST,
    size: int = len(FETCH_CONTENT),
) -> ModelFetchRequestV1:
    return ModelFetchRequestV1(
        relative_path=relative_path,
        expected_digest=ContentDigest(algorithm="sha256", hex=digest),
        expected_size=size,
        url=url,
    )


def _upstream(content: bytes, *, redirect_once_from: str | None = None):
    """A fake CDN: optionally redirects the first request for one path once,
    then always answers 200 with *content*."""
    state = {"redirected": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if redirect_once_from and request.url.path == redirect_once_from and not state["redirected"]:
            state["redirected"] = True
            return httpx.Response(302, headers={"Location": "https://example.invalid/direct"})
        return httpx.Response(200, content=content)

    return handler


def test_fetching_a_url_verifies_digest_and_publishes_to_the_depot(client, container):
    container.model_depot.http_transport = httpx.MockTransport(_upstream(FETCH_CONTENT))

    resp = client.post("/v1/models/fetch", json=envelope(_fetch_request()), headers=_auth())
    assert resp.status_code == 202
    transfer_id = resp.json()["transfer_id"]

    transfer = client.get(f"/v1/models/transfers/{transfer_id}", headers=_auth()).json()
    assert transfer["kind"] == "fetch"
    assert transfer["state"] == "completed"
    assert transfer["received_bytes"] == len(FETCH_CONTENT)
    assert transfer["digest"] == FETCH_DIGEST
    assert transfer["size_bytes"] == len(FETCH_CONTENT)

    dest = container.model_depot.depot_dir / "fetched/model.safetensors"
    assert dest.read_bytes() == FETCH_CONTENT
    sidecar = dest.with_name(dest.name + ".digest")
    assert jsonlib.loads(sidecar.read_text())["digest"] == FETCH_DIGEST


def test_fetching_a_url_with_no_expected_digest_publishes_and_reports_the_computed_digest(client, container):
    container.model_depot.http_transport = httpx.MockTransport(_upstream(FETCH_CONTENT))

    request = ModelFetchRequestV1(
        relative_path="fetched/unknown.safetensors", url="https://example.invalid/unknown",
    )
    resp = client.post("/v1/models/fetch", json=envelope(request), headers=_auth())
    assert resp.status_code == 202
    transfer_id = resp.json()["transfer_id"]

    transfer = client.get(f"/v1/models/transfers/{transfer_id}", headers=_auth()).json()
    assert transfer["state"] == "completed"
    assert transfer["digest"] == FETCH_DIGEST
    assert transfer["size_bytes"] == len(FETCH_CONTENT)

    dest = container.model_depot.depot_dir / "fetched/unknown.safetensors"
    assert dest.read_bytes() == FETCH_CONTENT
    sidecar = dest.with_name(dest.name + ".digest")
    assert jsonlib.loads(sidecar.read_text())["digest"] == FETCH_DIGEST

    listing = client.get("/v1/models/transfers", headers=_auth()).json()["transfers"]
    assert any(t["id"] == transfer_id and t["digest"] == FETCH_DIGEST for t in listing)


def test_fetching_a_url_that_redirects_once_still_succeeds(client, container):
    container.model_depot.http_transport = httpx.MockTransport(
        _upstream(FETCH_CONTENT, redirect_once_from="/redirected")
    )
    request = _fetch_request(
        relative_path="fetched/redirected.safetensors", url="https://example.invalid/redirected",
    )
    resp = client.post("/v1/models/fetch", json=envelope(request), headers=_auth())
    transfer_id = resp.json()["transfer_id"]

    transfer = client.get(f"/v1/models/transfers/{transfer_id}", headers=_auth()).json()
    assert transfer["state"] == "completed"


def test_a_fetch_digest_mismatch_fails_the_transfer_and_publishes_nothing(client, container):
    wrong_content = b"y" * len(FETCH_CONTENT)  # same length, wrong bytes
    container.model_depot.http_transport = httpx.MockTransport(_upstream(wrong_content))

    request = _fetch_request(relative_path="fetched/bad.safetensors")
    resp = client.post("/v1/models/fetch", json=envelope(request), headers=_auth())
    transfer_id = resp.json()["transfer_id"]

    transfer = client.get(f"/v1/models/transfers/{transfer_id}", headers=_auth()).json()
    assert transfer["state"] == "failed"
    assert "digest mismatch" in transfer["error"]

    dest = container.model_depot.depot_dir / "fetched/bad.safetensors"
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_fetch_rejects_a_relative_path_that_escapes_the_depot(client):
    body = envelope(_fetch_request())
    body["payload"]["relative_path"] = "../../etc/passwd"

    resp = client.post("/v1/models/fetch", json=body, headers=_auth())
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_payload"


def test_fetch_requires_a_token(client):
    resp = client.post("/v1/models/fetch", json=envelope(_fetch_request()))
    assert resp.status_code == 401
