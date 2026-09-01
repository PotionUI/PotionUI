"""WorkerCoordinator's pre-GPU model_bundle digest gate (submit()'s
`_model_bundle_mismatch` check) - the worker's own defense-in-depth
verification against its depot, independent of the host's inventory-based
pre-dispatch gate (see test_native_remote_backend.py's TestWorkerRejection
for the same scenario end to end through a real host + worker)."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from src.features.remote_execution.worker.coordinator import SubmitOutcome, WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot
from src.platform.worker_protocol import (
    ContentDigest,
    ExecutionPackageV1,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)

from tests.platform.worker_protocol.factories import make_digest

CONTENT = b"fake checkpoint bytes" * 100
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class _Pipe:
    name = "fake/pipe"

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
        from src.pipelines.contracts import PipeOutput

        return PipeOutput(output={})


class _FakeCatalog:
    def __init__(self, classes):
        self.pipes = dict(classes)
        self.pipe_sources = {}

    def get_pipe(self, name):
        return self.pipes.get(name)

    def get_available_pipes(self):
        return list(self.pipes.values())

    def remote_relevant_plugin_ids(self):
        return set()


def _entry(relative_path="checkpoint/model.safetensors", digest=DIGEST, size=len(CONTENT)):
    return ModelBundleEntryV1(
        logical_id="checkpoint/model.safetensors",
        role="checkpoint",
        relative_path=relative_path,
        digest=ContentDigest(algorithm="sha256", hex=digest),
        size_bytes=size,
    )


def _coordinator(tmp_path, *, model_depot=None):
    return WorkerCoordinator(
        worker_id="worker-1",
        pipe_catalog=_FakeCatalog({"fake/pipe": _Pipe}),
        journal=WorkerJournal(tmp_path / "journal"),
        artifacts_dir=tmp_path / "artifacts",
        device="cpu",
        dtype="fp32",
        vram_limit_gb=None,
        build_id=None,
        model_depot=model_depot,
    )


def _package(execution_id: str, *, model_bundle: ModelBundleManifestV1) -> ExecutionPackageV1:
    issued_at = datetime.now(timezone.utc)
    return ExecutionPackageV1(
        execution_id=execution_id,
        idempotency_key=execution_id,
        request_digest=make_digest("d"),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=1),
        required_fingerprints={},
        pipe_contracts={},
        model_bundle=model_bundle,
        processed_pipes=ProcessedPipelineV1(
            pipes=(ProcessedPipeV1(pipe_id="p1", pipe_type="fake/pipe", config={}, inputs={}),)
        ),
    )


def test_a_matching_digest_proceeds(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path / "depot")
    entry = _entry()
    depot.stage(entry, [CONTENT])
    coordinator = _coordinator(tmp_path, model_depot=depot)

    result = coordinator.submit(_package(
        "exec-match", model_bundle=ModelBundleManifestV1(bundle_id="b", bundle_digest=make_digest("b"), entries=(entry,)),
    ))

    assert result.outcome == SubmitOutcome.ACCEPTED


def test_a_mismatched_digest_is_rejected_pre_gpu_naming_the_file(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path / "depot")
    entry = _entry()
    dest = depot.depot_dir / entry.relative_path
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * len(CONTENT))  # same size, wrong bytes, no sidecar
    coordinator = _coordinator(tmp_path, model_depot=depot)

    result = coordinator.submit(_package(
        "exec-mismatch", model_bundle=ModelBundleManifestV1(bundle_id="b", bundle_digest=make_digest("b"), entries=(entry,)),
    ))

    assert result.outcome == SubmitOutcome.REJECTED
    record = coordinator.record_for("exec-mismatch")
    error = record.events[-1].error
    assert error.code == "model_digest_mismatch"
    assert entry.relative_path in error.message
    assert DIGEST in error.message


def test_a_missing_file_is_rejected_with_a_distinct_message(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path / "depot")  # never staged
    entry = _entry()
    coordinator = _coordinator(tmp_path, model_depot=depot)

    result = coordinator.submit(_package(
        "exec-missing", model_bundle=ModelBundleManifestV1(bundle_id="b", bundle_digest=make_digest("b"), entries=(entry,)),
    ))

    assert result.outcome == SubmitOutcome.REJECTED
    record = coordinator.record_for("exec-missing")
    error = record.events[-1].error
    assert error.code == "model_digest_mismatch"
    assert entry.relative_path in error.message
    assert "not staged" in error.message
    assert "digest mismatch" not in error.message


def test_a_file_with_no_sidecar_is_hashed_once_and_proceeds(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path / "depot")
    entry = _entry()
    dest = depot.depot_dir / entry.relative_path
    dest.parent.mkdir(parents=True)
    dest.write_bytes(CONTENT)  # correct bytes, but no sidecar yet
    coordinator = _coordinator(tmp_path, model_depot=depot)

    result = coordinator.submit(_package(
        "exec-hash-once", model_bundle=ModelBundleManifestV1(bundle_id="b", bundle_digest=make_digest("b"), entries=(entry,)),
    ))

    assert result.outcome == SubmitOutcome.ACCEPTED
    sidecar = dest.with_name(dest.name + ".digest")
    assert json.loads(sidecar.read_text())["digest"] == DIGEST


def test_an_empty_manifest_skips_verification_entirely(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path / "depot")
    coordinator = _coordinator(tmp_path, model_depot=depot)

    result = coordinator.submit(_package(
        "exec-empty", model_bundle=ModelBundleManifestV1(bundle_id="b", bundle_digest=make_digest("b")),
    ))

    assert result.outcome == SubmitOutcome.ACCEPTED
