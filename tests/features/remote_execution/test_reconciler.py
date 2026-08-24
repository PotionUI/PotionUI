"""RemoteExecutionReconciler against a real migrated sqlite file, same
patching pattern as test_repository.py. The event-resume half talks to a REAL
worker app over an in-process ASGI transport (never a real socket) so a
"resume after a restart" scenario is proven against the real wire, not a mock
of it.
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import httpx

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.backends.backend_config import NATIVE_LOCAL_DRIVER, NATIVE_REMOTE_DRIVER
from src.features.remote_execution.reconciler import RemoteExecutionReconciler
from src.features.remote_execution.records import RemoteExecution, RemoteExecutionState
from src.features.remote_execution.repository import RemoteExecutionRepository
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.pipelines.contracts import PipeOutput
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationManager
from src.platform.worker_protocol import ContentDigest, ExecutionLimitsV1, ExecutionPackageV1, ModelBundleManifestV1, ProcessedPipelineV1, ProcessedPipeV1

S = RemoteExecutionState
TOKEN = "secret-worker-token"


class QuickPipe:
    """Finishes immediately - a real worker run whose whole event history
    (including the terminal event) already exists in the worker's journal by
    the time the reconciler asks for it. httpx.ASGITransport fully drains an
    ASGI response before handing bytes back to the client rather than
    streaming them incrementally as they're produced (unlike a real socket),
    so a genuinely still-running SSE connection can't be exercised through it
    - this pipe sidesteps that by making the "worker finished the run while
    core was down" restart scenario, not a "core reconnects mid-run" one."""

    name = "quick/fake"

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


CATALOG = FakeCatalog({"quick/fake": QuickPipe})
MODEL_BUNDLE = ModelBundleManifestV1(
    bundle_id="b", bundle_digest=ContentDigest(algorithm="sha256", hex="ab" * 32), entries=(),
)


class FakeRemoteConfig:
    driver = NATIVE_REMOTE_DRIVER

    def __init__(self, base_url, worker_token=TOKEN):
        self.base_url = base_url
        self.worker_token = worker_token


class FakeLocalConfig:
    """A non-remote backend - the reconciler must skip its rows entirely,
    since it has no worker to resume events from."""
    driver = NATIVE_LOCAL_DRIVER


class FakeBackendConfigManager:
    def __init__(self, backends: dict):
        self._backends = backends

    def get_backend(self, backend_id):
        return self._backends.get(backend_id)


class RemoteExecutionReconcilerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
            patch("src.features.remote_execution.repository.db", self.db),
        ]
        for p in self._patchers:
            p.start()

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationManager().run_migrations()
        finally:
            sys.stdout = old_stdout

        self.repo = RemoteExecutionRepository()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        for leftover in Path(self.temp_dir).iterdir():
            leftover.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _new(self, key: str, **overrides) -> RemoteExecution:
        fields = {
            "id": "", "provider": "native.remote", "state": S.PENDING,
            "idempotency_key": key, "request_digest": "sha256:" + "a" * 64,
        }
        fields.update(overrides)
        return self.repo.create(RemoteExecution(**fields))

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)


class TestPureSweeps(RemoteExecutionReconcilerTestCase):
    """The three no-network sweeps - correctness already proven at the
    repository level; this proves the reconciler actually calls them."""

    def test_a_lapsed_lease_is_reclaimed(self):
        created = self._new("k1")
        self.repo.claim_for_dispatch("dead-dispatcher", 60, now=1_000)

        reconciler = RemoteExecutionReconciler(repository=self.repo, backend_config_manager=None)
        with patch("src.features.remote_execution.repository.now_ms", return_value=61_000):
            result = self._run(reconciler.reconcile())

        self.assertEqual(result["leases_reclaimed"], 1)
        self.assertEqual(self.repo.get_by_id(created.id).state, S.PENDING)

    def test_an_overdue_row_expires(self):
        self._new("k1", expires_at_ms=1)

        reconciler = RemoteExecutionReconciler(repository=self.repo, backend_config_manager=None)
        with patch("src.features.remote_execution.repository.now_ms", return_value=10**15):
            result = self._run(reconciler.reconcile())

        self.assertEqual(result["expired"], 1)

    def test_no_backend_config_manager_skips_the_live_resume_cleanly(self):
        self._new("k1")
        reconciler = RemoteExecutionReconciler(repository=self.repo, backend_config_manager=None)

        result = self._run(reconciler.reconcile())

        self.assertEqual(result["events_resumed"], 0)
        self.assertEqual(result["unreachable"], 0)


class TestLiveEventResume(RemoteExecutionReconcilerTestCase):
    def _worker(self, tmp_path):
        config = WorkerConfig(
            token=TOKEN, worker_id="worker-1", provider="manual", host="127.0.0.1", port=0,
            work_dir=tmp_path, artifacts_dir=tmp_path / "artifacts", build_id="b",
            device="cpu", dtype="fp32", vram_limit_gb=None,
        )
        journal = WorkerJournal(config.work_dir)
        coordinator = WorkerCoordinator(
            worker_id=config.worker_id, pipe_catalog=CATALOG, journal=journal,
            artifacts_dir=config.artifacts_dir, device=config.device, dtype=config.dtype,
            vram_limit_gb=config.vram_limit_gb, build_id=config.build_id,
        )
        container = WorkerContainer(
            config=config, pipe_catalog=CATALOG, journal=journal, coordinator=coordinator,
            gpu_manager=None, system_monitor=None,
        )
        return container, create_worker_app(container=container)

    def _package(self, execution_id, container):
        now = datetime.now(timezone.utc)
        return ExecutionPackageV1(
            execution_id=execution_id, idempotency_key=execution_id,
            request_digest=ContentDigest(algorithm="sha256", hex="11" * 32),
            issued_at=now, expires_at=now + timedelta(hours=1),
            required_fingerprints=container.coordinator.fingerprints(),
            model_bundle=MODEL_BUNDLE,
            processed_pipes=ProcessedPipelineV1(pipes=(
                ProcessedPipeV1(pipe_id="p1", pipe_type="quick/fake", config={}, inputs={}),
            )),
            limits=ExecutionLimitsV1(),
        )

    def _wait_for_terminal(self, container, execution_id):
        import time

        for _ in range(100):
            record = container.coordinator.record_for(execution_id)
            if record and record.is_terminal:
                return
            time.sleep(0.02)
        raise AssertionError(f"{execution_id} never reached a terminal state")

    def test_resumes_events_and_lands_the_row_on_the_worker_s_true_outcome(self):
        import tempfile as _tempfile

        tmp_path = Path(_tempfile.mkdtemp())
        container, app = self._worker(tmp_path)
        package = self._package("exec-live", container)
        container.coordinator.submit(package)
        # The worker finished its run while this simulates core having been
        # down (a restart) - the row core kept was never told any of it.
        self._wait_for_terminal(container, "exec-live")

        row = self._new("exec-live", id="exec-live")
        self.repo.apply_state(row.id, S.DISPATCHING)
        self.repo.apply_state(row.id, S.STAGING)
        self.assertEqual(row.event_cursor, 0)

        backends = FakeBackendConfigManager({"backend-1": FakeRemoteConfig("http://fake-worker")})
        reconciler = RemoteExecutionReconciler(
            repository=self.repo, backend_config_manager=backends, event_pull_timeout_seconds=5.0,
        )

        with patch("src.features.remote_execution.reconciler.WorkerTransport") as fake_transport_cls:
            from src.features.remote_execution.transport import WorkerTransport as RealTransport

            def _build(base_url, token, **kwargs):
                return RealTransport(base_url, token, transport=httpx.ASGITransport(app=app))

            fake_transport_cls.side_effect = _build

            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE remote_executions SET backend_id = ? WHERE id = ?",
                    ("backend-1", row.id),
                )

            result = self._run(reconciler.reconcile())

        journaled = container.coordinator.record_for("exec-live").events
        self.assertEqual(result["events_resumed"], len(journaled))
        resumed_row = self.repo.get_by_id(row.id)
        self.assertEqual(resumed_row.state, S.SUCCEEDED)
        self.assertEqual(resumed_row.event_cursor, journaled[-1].cursor)
        self.assertTrue(resumed_row.is_terminal)

    def test_only_events_after_the_persisted_cursor_are_newly_applied(self):
        """A row that already saw the first few events (before the outage)
        must not re-apply them - only what's genuinely new is counted."""
        tmp_path = Path(tempfile.mkdtemp())
        container, app = self._worker(tmp_path)
        package = self._package("exec-partial", container)
        container.coordinator.submit(package)
        self._wait_for_terminal(container, "exec-partial")
        journaled = container.coordinator.record_for("exec-partial").events

        row = self._new("exec-partial", id="exec-partial")
        self.repo.apply_state(row.id, S.DISPATCHING)
        self.repo.apply_state(row.id, S.STAGING)
        # Pretend core already applied the first event before the outage.
        self.repo.apply_job_event(row.id, journaled[0])
        self.assertEqual(self.repo.get_by_id(row.id).event_cursor, journaled[0].cursor)

        backends = FakeBackendConfigManager({"backend-1": FakeRemoteConfig("http://fake-worker")})
        reconciler = RemoteExecutionReconciler(
            repository=self.repo, backend_config_manager=backends, event_pull_timeout_seconds=5.0,
        )
        with patch("src.features.remote_execution.reconciler.WorkerTransport") as fake_transport_cls:
            from src.features.remote_execution.transport import WorkerTransport as RealTransport

            fake_transport_cls.side_effect = lambda base_url, token, **kw: RealTransport(
                base_url, token, transport=httpx.ASGITransport(app=app),
            )
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE remote_executions SET backend_id = ? WHERE id = ?", ("backend-1", row.id),
                )
            result = self._run(reconciler.reconcile())

        self.assertEqual(result["events_resumed"], len(journaled) - 1)

    def test_a_non_remote_backend_id_is_skipped(self):
        row = self._new("exec-local", id="exec-local")
        self.repo.apply_state(row.id, S.DISPATCHING)
        self.repo.apply_state(row.id, S.RUNNING)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE remote_executions SET backend_id = ? WHERE id = ?", ("native", row.id),
            )

        backends = FakeBackendConfigManager({"native": FakeLocalConfig()})
        reconciler = RemoteExecutionReconciler(repository=self.repo, backend_config_manager=backends)

        result = self._run(reconciler.reconcile())

        self.assertEqual(result["events_resumed"], 0)
        self.assertEqual(result["unreachable"], 0)

    def test_an_unreachable_worker_is_counted_and_never_raises(self):
        row = self._new("exec-unreachable", id="exec-unreachable")
        self.repo.apply_state(row.id, S.DISPATCHING)
        self.repo.apply_state(row.id, S.RUNNING)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE remote_executions SET backend_id = ? WHERE id = ?", ("backend-1", row.id),
            )

        backends = FakeBackendConfigManager({
            "backend-1": FakeRemoteConfig("http://127.0.0.1:1"),  # nothing listens here
        })
        reconciler = RemoteExecutionReconciler(
            repository=self.repo, backend_config_manager=backends, event_pull_timeout_seconds=1.0,
        )

        result = self._run(reconciler.reconcile())  # must not raise

        self.assertEqual(result["unreachable"], 1)
        self.assertEqual(self.repo.get_by_id(row.id).state, S.RUNNING)  # left alone, not crashed into FAILED
