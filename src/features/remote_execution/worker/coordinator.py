"""Single-slot execution coordination: one execution runs at a time; every
other fact about it (idempotency, cursor-ordered events, terminal result) is
answered from the journal, not from process memory, so a resubmit after a
restart is still correct.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.features.remote_execution.worker.assets import AssetStager
from src.features.remote_execution.worker.executor import (
    PipeExecutionError,
    WorkerEvent,
    WorkerPipelineExecutor,
)
from src.features.remote_execution.worker.journal import ExecutionRecord, WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot
from src.features.remote_execution.worker.path_remap import ModelRemapError, remap_model_paths
from src.pipelines.catalog import PipeCatalog
from src.pipelines.remote_fingerprint import (
    compute_build_fingerprint,
    compute_pipe_catalog_fingerprint,
    compute_pipe_contract_fingerprint,
    compute_remote_plugin_bundle_fingerprint,
)
from src.platform.worker_protocol import (
    ExecutionPackageV1,
    FingerprintMismatchV1,
    JobErrorV1,
)
from src.platform.worker_protocol.worker_info import FINGERPRINT_DOMAINS

_DEFAULT_STAGING_TIMEOUT_SECONDS = 300


class SubmitOutcome:
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    DIGEST_CONFLICT = "digest_conflict"
    BUSY = "busy"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class _MismatchReport:
    mismatch: FingerprintMismatchV1
    message: str


@dataclass
class SubmitResult:
    outcome: str
    execution_id: str
    record: Optional[ExecutionRecord] = None
    detail: Optional[str] = None


class CancelOutcome:
    ACCEPTED = "accepted"
    ALREADY_TERMINAL = "already_terminal"
    NOT_FOUND = "not_found"


class WorkerCoordinator:
    def __init__(
        self,
        *,
        worker_id: str,
        pipe_catalog: PipeCatalog,
        journal: WorkerJournal,
        artifacts_dir: Path,
        device: str,
        dtype: str,
        vram_limit_gb: Optional[float],
        build_id: Optional[str] = None,
        gpu_monitor: Any = None,
        system_monitor: Any = None,
        model_lifecycle: Any = None,
        model_depot: Optional[ModelDepot] = None,
    ):
        self._worker_id = worker_id
        self._catalog = pipe_catalog
        self._journal = journal
        self._artifacts_dir = artifacts_dir
        self._device = device
        self._dtype = dtype
        self._vram_limit_gb = vram_limit_gb
        self._build_id = build_id
        self._gpu_monitor = gpu_monitor
        self._system_monitor = system_monitor
        self._model_lifecycle = model_lifecycle
        self._model_depot = model_depot

        self._lock = threading.Lock()
        self._running_execution_id: Optional[str] = None
        self._cancel_flags: Dict[str, threading.Event] = {}
        self._packages: Dict[str, ExecutionPackageV1] = {}
        self._stagers: Dict[str, AssetStager] = {}
        self._artifact_paths: Dict[str, Path] = {}

    def fingerprints(self) -> Dict[str, str]:
        return {
            "pipe_catalog": compute_pipe_catalog_fingerprint(self._catalog),
            "plugin_bundle": compute_remote_plugin_bundle_fingerprint(self._catalog, ()),
            "build": compute_build_fingerprint(self._build_id),
        }

    def package_for(self, execution_id: str) -> Optional[ExecutionPackageV1]:
        return self._packages.get(execution_id)

    def artifact_path(self, artifact_id: str) -> Optional[Path]:
        return self._artifact_paths.get(artifact_id)

    # -- submission -----------------------------------------------------

    def submit(self, package: ExecutionPackageV1) -> SubmitResult:
        execution_id = package.execution_id
        digest = str(package.request_digest)

        existing = self._journal.get(execution_id)
        if existing is not None:
            if existing.request_digest != digest:
                return SubmitResult(SubmitOutcome.DIGEST_CONFLICT, execution_id, record=existing)
            return SubmitResult(SubmitOutcome.DUPLICATE, execution_id, record=existing)

        report = self._fingerprint_mismatch(package)
        if report is not None:
            self._journal.start(execution_id, digest)
            self._packages[execution_id] = package
            self._append(
                execution_id,
                kind="rejected",
                error=JobErrorV1(
                    code="fingerprint_mismatch",
                    message=report.message,
                    retryable=False,
                    fingerprint_mismatch=report.mismatch,
                ),
            )
            return SubmitResult(SubmitOutcome.REJECTED, execution_id, detail=report.mismatch.domain)

        if package.expires_at is not None and package.expires_at <= datetime.now(timezone.utc):
            self._journal.start(execution_id, digest)
            self._packages[execution_id] = package
            self._append(
                execution_id,
                kind="rejected",
                error=JobErrorV1(code="expired", message="package expired before execution", retryable=False),
            )
            return SubmitResult(SubmitOutcome.EXPIRED, execution_id)

        with self._lock:
            if self._running_execution_id is not None:
                return SubmitResult(SubmitOutcome.BUSY, execution_id)
            self._running_execution_id = execution_id
            cancel_event = threading.Event()
            self._cancel_flags[execution_id] = cancel_event

        self._packages[execution_id] = package
        record = self._journal.start(execution_id, digest)
        self._stagers[execution_id] = AssetStager(self._execution_dir(execution_id) / "assets")
        self._append(execution_id, kind="accepted")

        thread = threading.Thread(target=self._run, args=(package, cancel_event), daemon=True)
        thread.start()
        return SubmitResult(SubmitOutcome.ACCEPTED, execution_id, record=record)

    def _fingerprint_mismatch(self, package: ExecutionPackageV1) -> Optional[_MismatchReport]:
        """Gates per-pipe when pipe_contracts is populated; empty (pre-gate
        host) falls back to the old whole-catalog comparison."""
        if not package.pipe_contracts:
            actual = self.fingerprints()
            for domain in FINGERPRINT_DOMAINS:
                required = package.required_fingerprints.get(domain)
                if required is not None and required != actual.get(domain):
                    mismatch = FingerprintMismatchV1(
                        domain=domain, expected=required, actual=actual.get(domain) or "",
                    )
                    return _MismatchReport(mismatch, f"{domain} fingerprint mismatch")
            return None

        required_build = package.required_fingerprints.get("build")
        if required_build is not None:
            actual_build = compute_build_fingerprint(self._build_id)
            if required_build != actual_build:
                mismatch = FingerprintMismatchV1(domain="build", expected=required_build, actual=actual_build)
                return _MismatchReport(mismatch, "build fingerprint mismatch")

        for pipe_type, required_contract in package.pipe_contracts.items():
            pipe_class = self._catalog.get_pipe(pipe_type)
            if pipe_class is None:
                mismatch = FingerprintMismatchV1(
                    domain="pipe_catalog", expected=required_contract, actual="missing",
                )
                return _MismatchReport(
                    mismatch,
                    f"worker has no pipe '{pipe_type}' - the plugin providing it is not "
                    "installed in the worker image",
                )
            actual_contract = compute_pipe_contract_fingerprint(pipe_class)
            if actual_contract != required_contract:
                mismatch = FingerprintMismatchV1(
                    domain="pipe_catalog", expected=required_contract, actual=actual_contract,
                )
                return _MismatchReport(
                    mismatch,
                    f"pipe '{pipe_type}' contract differs between host and worker - "
                    "rebuild/update the worker image",
                )
        return None

    # -- staging (assets) -------------------------------------------------

    def stager_for(self, execution_id: str) -> Optional[AssetStager]:
        return self._stagers.get(execution_id)

    def _execution_dir(self, execution_id: str) -> Path:
        return self._artifacts_dir.parent / "executions" / execution_id

    # -- run --------------------------------------------------------------

    def _run(self, package: ExecutionPackageV1, cancel_event: threading.Event) -> None:
        execution_id = package.execution_id
        try:
            self._append(execution_id, kind="staging")

            stager = self._stagers.get(execution_id)
            if stager is not None and not self._wait_for_assets(package, stager, cancel_event):
                if cancel_event.is_set():
                    self._append(execution_id, kind="cancelled")
                    return
                raise PipeExecutionError(
                    "staging_timeout", "input assets did not finish staging in time", retryable=True,
                )

            if cancel_event.is_set():
                self._append(execution_id, kind="cancelled")
                return

            self._append(execution_id, kind="running")

            pipeline = package.processed_pipes
            if package.model_bundle.entries:
                if self._model_depot is None:
                    raise PipeExecutionError(
                        "model_depot_unavailable",
                        "this worker has no configured model depot, but the package "
                        "references models",
                        retryable=False,
                    )
                try:
                    pipeline = remap_model_paths(
                        pipeline, package.model_bundle, self._model_depot.depot_dir,
                    )
                except ModelRemapError as exc:
                    raise PipeExecutionError(
                        "model_not_staged", str(exc), retryable=False,
                    ) from exc

            executor = WorkerPipelineExecutor(
                self._catalog,
                device=self._device,
                dtype=self._dtype,
                vram_limit_gb=self._vram_limit_gb,
                artifacts_dir=self._artifacts_dir,
                gpu_monitor=self._gpu_monitor,
                system_monitor=self._system_monitor,
                model_lifecycle=self._model_lifecycle,
                resolve_asset=(stager.resolve if stager is not None else None),
            )

            def emit(worker_event: WorkerEvent) -> None:
                self._append(
                    execution_id,
                    kind=worker_event.kind,
                    pipe_id=worker_event.pipe_id,
                    progress=worker_event.progress,
                    detail=worker_event.detail,
                    artifacts=worker_event.artifacts,
                    payload=worker_event.payload,
                )
                for artifact in worker_event.artifacts:
                    self._artifact_paths[artifact.artifact_id] = self._artifact_file(artifact)

            executor.run(pipeline, emit=emit, is_cancelled=cancel_event.is_set)

            if cancel_event.is_set():
                self._append(execution_id, kind="cancelled")
            else:
                self._append(execution_id, kind="succeeded")
        except PipeExecutionError as exc:
            self._append(
                execution_id, kind="failed", pipe_id=exc.pipe_id,
                error=JobErrorV1(code=exc.code, message=exc.message, retryable=exc.retryable),
            )
        except Exception as exc:  # a bug here must still terminate the row, not hang it forever
            self._append(
                execution_id, kind="failed",
                error=JobErrorV1(code="worker_error", message=str(exc), retryable=True),
            )
        finally:
            with self._lock:
                if self._running_execution_id == execution_id:
                    self._running_execution_id = None
            self._cancel_flags.pop(execution_id, None)

    def _wait_for_assets(self, package: ExecutionPackageV1, stager: AssetStager, cancel_event: threading.Event) -> bool:
        timeout = (
            package.limits.max_staging_seconds
            if package.limits.max_staging_seconds is not None
            else _DEFAULT_STAGING_TIMEOUT_SECONDS
        )
        deadline = time.monotonic() + timeout
        while not stager.all_staged(package):
            if cancel_event.is_set():
                return False
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def _artifact_file(self, artifact) -> Path:
        # the executor wrote it under self._artifacts_dir with this exact name
        return self._artifacts_dir / (artifact.filename or f"{artifact.artifact_id}")

    # -- events / cancel ----------------------------------------------------

    def events_after(self, execution_id: str, after_cursor: int):
        return self._journal.events_after(execution_id, after_cursor)

    def record_for(self, execution_id: str) -> Optional[ExecutionRecord]:
        return self._journal.get(execution_id)

    def cancel(self, execution_id: str) -> str:
        record = self._journal.get(execution_id)
        if record is None:
            return CancelOutcome.NOT_FOUND
        if record.is_terminal:
            return CancelOutcome.ALREADY_TERMINAL
        event = self._cancel_flags.get(execution_id)
        if event is not None:
            event.set()
        return CancelOutcome.ACCEPTED

    def _append(
        self,
        execution_id: str,
        *,
        kind: str,
        pipe_id: Optional[str] = None,
        progress: Optional[float] = None,
        detail: Optional[str] = None,
        artifacts=(),
        payload: Optional[Dict[str, Any]] = None,
        error: Optional[JobErrorV1] = None,
    ):
        from src.platform.worker_protocol import JobEventV1

        record = self._journal.get(execution_id)
        cursor = record.next_cursor if record is not None else 1
        event = JobEventV1(
            execution_id=execution_id,
            worker_id=self._worker_id,
            cursor=cursor,
            emitted_at=datetime.now(timezone.utc),
            kind=kind,
            pipe_id=pipe_id,
            progress=progress,
            detail=detail,
            artifacts=tuple(artifacts),
            error=error,
            payload=payload or {},
        )
        self._journal.append(execution_id, event)
        return event
