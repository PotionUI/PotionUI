"""WorkerCoordinator's per-pipe compatibility gate (submit()'s pipe_contracts
check), independent of the whole-catalog fallback covered by other coordinator
callers - see tests/features/remote_execution/test_native_remote_backend.py's
TestPerPipelineCompatibilityGate for the same scenarios end to end."""

from datetime import datetime, timedelta, timezone

from src.features.remote_execution.worker.coordinator import SubmitOutcome, WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.pipelines.remote_fingerprint import compute_pipe_contract_fingerprint
from src.platform.worker_protocol import (
    ExecutionPackageV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)

from tests.platform.worker_protocol.factories import make_digest


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


class _DriftedPipe(_Pipe):
    """Same registered name, a config shape that changed - contract drift."""

    @classmethod
    def configuration(cls):
        from src.pipelines.contracts import PipeConfigSpec

        return [PipeConfigSpec(name="extra", param_type=str, default="new")]


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


def _coordinator(tmp_path, classes):
    return WorkerCoordinator(
        worker_id="worker-1",
        pipe_catalog=_FakeCatalog(classes),
        journal=WorkerJournal(tmp_path),
        artifacts_dir=tmp_path / "artifacts",
        device="cpu",
        dtype="fp32",
        vram_limit_gb=None,
        build_id=None,
    )


def _package(execution_id: str, *, pipe_contracts=None, required_fingerprints=None) -> ExecutionPackageV1:
    issued_at = datetime.now(timezone.utc)
    return ExecutionPackageV1(
        execution_id=execution_id,
        idempotency_key=execution_id,
        request_digest=make_digest("d"),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=1),
        required_fingerprints=required_fingerprints or {},
        pipe_contracts=pipe_contracts or {},
        model_bundle=ModelBundleManifestV1(bundle_id="b", bundle_digest=make_digest("b")),
        processed_pipes=ProcessedPipelineV1(
            pipes=(ProcessedPipeV1(pipe_id="p1", pipe_type="fake/pipe", config={}, inputs={}),)
        ),
    )


def test_contract_drift_is_rejected_naming_the_pipe(tmp_path):
    coordinator = _coordinator(tmp_path, {"fake/pipe": _DriftedPipe})
    required = compute_pipe_contract_fingerprint(_Pipe)  # what the host actually shipped

    result = coordinator.submit(_package("exec-drift", pipe_contracts={"fake/pipe": required}))

    assert result.outcome == SubmitOutcome.REJECTED
    record = coordinator.record_for("exec-drift")
    message = record.events[-1].error.message
    assert "fake/pipe" in message
    assert "contract differs" in message


def test_a_matching_contract_is_accepted(tmp_path):
    coordinator = _coordinator(tmp_path, {"fake/pipe": _Pipe})
    required = compute_pipe_contract_fingerprint(_Pipe)

    result = coordinator.submit(_package("exec-match", pipe_contracts={"fake/pipe": required}))

    assert result.outcome == SubmitOutcome.ACCEPTED


def test_empty_pipe_contracts_falls_back_to_whole_catalog_comparison(tmp_path):
    coordinator = _coordinator(tmp_path, {"fake/pipe": _Pipe})

    result = coordinator.submit(_package(
        "exec-fallback", pipe_contracts={}, required_fingerprints={"pipe_catalog": "not-the-real-value"},
    ))

    assert result.outcome == SubmitOutcome.REJECTED
    assert result.detail == "pipe_catalog"
    record = coordinator.record_for("exec-fallback")
    assert record.events[-1].error.message == "pipe_catalog fingerprint mismatch"
