"""RemoteNativeBackend end to end: a REAL worker app
(`src.bootstrap.worker_app.create_worker_app`) reached over an in-process ASGI
transport (`httpx.ASGITransport` - never a real socket), driving the full
`native.remote` driver through its public `BaseBackend` surface
(`start_generation`/`cancel_generation`). A real, migrated scratch sqlite
backs `RemoteExecutionRepository` - same pattern as test_repository.py /
test_reconciler.py.

httpx.ASGITransport fully drains an ASGI response before handing bytes back to
the client rather than truly streaming it as the app produces it (see
test_reconciler.py's QuickPipe docstring for the same note) - every pipe here
either finishes quickly or is cancel-aware, so the worker's SSE response
completes promptly and the transport can deliver it, exactly the way a real
socket would just with genuine mid-flight delivery instead of a buffered
catch-up. It does not weaken what's proven: submit, staging/upload, the full
event history, artifact import, and terminal state are all real.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.remote_execution.records import RemoteExecutionState
from src.features.remote_execution.repository import RemoteExecutionRepository
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot
from src.pipelines.contracts import IOType, PipeConfigSpec, PipeOutput, PipeOutputSpec
from src.pipelines.outputs import ErrorGenerationOutput, ImageGenerationOutput, ProgressGenerationOutput
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

S = RemoteExecutionState
TOKEN = "secret-worker-token"


# -- fake pipes (shared by both the worker's catalog and the core-side
# fingerprint catalog - the SAME classes on both sides, so their contracts
# fingerprint identically without needing to fake that separately) ----------

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

        image = Image.new("RGB", (3, 3), color=(1, 2, 3))
        generation_outputs(ImageGenerationOutput(image=image, temporary=False))
        return PipeOutput(output={"image": image})


class AssetAwarePipe:
    """Records the config value it actually received - proves the worker
    staged a real file and RemoteNativeBackend uploaded real bytes, not just
    that a token was minted."""

    name = "asset/fake"
    received_content: bytes | None = None

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
        return [PipeConfigSpec(name="input_path", param_type=str, default="")]

    def process(self, pipe_input, generation_outputs):
        path = self.config["input_path"]
        type(self).received_content = Path(path).read_bytes()
        return PipeOutput(output={})


class ModelAwarePipe:
    """Records the `file_path` it actually received - proves a real model
    file was staged onto the worker's depot and the pipe ran against the
    worker's depot path, not the dispatching host's original one."""

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
        return [PipeConfigSpec(name="checkpoint", param_type=dict, default={})]

    def process(self, pipe_input, generation_outputs):
        type(self).received_file_path = self.config.get("checkpoint", {}).get("file_path")
        return PipeOutput(output={})


class CancelAwarePipe:
    name = "cancelable/fake"

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

    def process(self, pipe_input, generation_outputs, is_cancelled):
        for _ in range(200):
            if is_cancelled():
                return PipeOutput(output={})
            time.sleep(0.02)
        return PipeOutput(output={})


class RejectedByFingerprintPipe:
    """Never actually runs in the REJECTION test - only needs to exist on
    both catalogs so build_processed_pipeline can resolve it."""

    name = "rejected/fake"

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


_CLASSES = {
    "image/fake": ImagePipe,
    "asset/fake": AssetAwarePipe,
    "model_aware/fake": ModelAwarePipe,
    "cancelable/fake": CancelAwarePipe,
    "rejected/fake": RejectedByFingerprintPipe,
}


class FakeCatalog:
    """One instance per side (worker vs. core) so nothing is literally
    shared, but built from the same class dict - see module docstring."""

    def __init__(self):
        self.pipes = dict(_CLASSES)
        self.pipe_sources = {}

    def get_pipe(self, name):
        return self.pipes.get(name)

    def get_available_pipes(self):
        return list(self.pipes.values())

    def remote_relevant_plugin_ids(self):
        return set()


class FakePluginRegistry:
    def get_enabled_plugins(self):
        return []


class NativeRemoteBackendTestCase(unittest.TestCase):
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
            patch("src.features.models.repository.db", self.db),
            # ModelRepository.create -> get_by_id loads tags through the tag
            # repo's own module-bound db; unpatched it reaches the dev DB,
            # which only exists locally (CI: "no such table: tags").
            patch("src.features.tags.repository.db", self.db),
            patch("src.platform.settings.repository.db", self.db),
            # os.getenv("POTIONUI_BUILD_ID") must read as unset so the "build"
            # fingerprint domain matches the worker's build_id=None below.
            patch.dict("os.environ", {"POTIONUI_BUILD_ID": ""}),
        ]
        for p in self._patchers:
            p.start()

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

        self.repo = RemoteExecutionRepository()

        self.storage_dir = Path(self.temp_dir) / "storage"
        self.storage_dir.mkdir()
        Settings(SettingRepository()).set_setting(
            "file_storage_directory", str(self.storage_dir),
        )

        self.worker_container, self.worker_app = self._build_worker(build_id=None)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        for leftover in Path(self.temp_dir).iterdir():
            if leftover.is_file():
                leftover.unlink()
        Database._instance = None

    def _build_worker(self, *, build_id):
        worker_tmp = Path(tempfile.mkdtemp())
        config = WorkerConfig(
            token=TOKEN, worker_id="worker-1", provider="manual", host="127.0.0.1", port=0,
            work_dir=worker_tmp, artifacts_dir=worker_tmp / "artifacts", build_id=build_id,
            device="cpu", dtype="fp32", vram_limit_gb=None,
        )
        journal = WorkerJournal(config.work_dir)
        catalog = FakeCatalog()
        model_depot = ModelDepot(depot_dir=worker_tmp / "models")
        coordinator = WorkerCoordinator(
            worker_id=config.worker_id, pipe_catalog=catalog, journal=journal,
            artifacts_dir=config.artifacts_dir, device=config.device, dtype=config.dtype,
            vram_limit_gb=config.vram_limit_gb, build_id=config.build_id,
            model_depot=model_depot,
        )
        container = WorkerContainer(
            config=config, pipe_catalog=catalog, journal=journal, coordinator=coordinator,
            gpu_monitor=None, system_monitor=None, model_depot=model_depot,
        )
        return container, create_worker_app(container=container)

    def _register_model(self, *, role: str, filename: str, content: bytes, is_directory: bool = False) -> Path:
        """Index a real on-disk model file the same way the models feature
        would after a scan - `build_model_bundle`/`stage_model_bundle` both
        read this row, never the filesystem directly."""
        source_dir = Path(self.temp_dir) / "model_sources"
        source_dir.mkdir(exist_ok=True)
        source_path = source_dir / filename
        source_path.write_bytes(content)
        ModelRepository().create(Model(
            filename=filename, file_path=str(source_path), file_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(), model_type=role, is_directory=is_directory,
        ))
        return source_path

    def _plant_generation_row(self, generation_id: str) -> None:
        """RemoteExecution.generation_id FKs to generations(id) - in the real
        flow the orchestrator always inserts this row before ever calling
        backend.start_generation(); a minimal stand-in here."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, preset_id, form_data, status) VALUES (?, ?, ?, ?)",
                (generation_id, "preset-1", "{}", "pending"),
            )

    def _backend(self, app) -> RemoteNativeBackend:
        config = NativeRemoteBackendConfig(
            id="remote-1", name="Remote Worker", base_url="http://fake-worker", worker_token=TOKEN,
        )
        backend = RemoteNativeBackend(config, transport_override=httpx.ASGITransport(app=app))
        backend.bind_remote_context(pipe_catalog=FakeCatalog(), plugin_registry=FakePluginRegistry())
        return backend

    def _run_generation(self, backend, pipeline_data, *, timeout=10.0):
        """Drive start_generation to completion and return the collected outputs."""
        self._plant_generation_row(pipeline_data["generation_id"])
        outputs = []
        done = asyncio.Event()

        def emit(output):
            outputs.append(output)
            if output is None:
                done.set()

        async def scenario():
            generation_id = await backend.start_generation(pipeline_data, emit)
            await asyncio.wait_for(done.wait(), timeout=timeout)
            return generation_id

        generation_id = asyncio.run(scenario())
        return generation_id, outputs

    def _run(self, coro):
        return asyncio.run(coro)


# -- fingerprint pre-gate -----------------------------------------------------

class TestFingerprintPreGate(NativeRemoteBackendTestCase):
    def test_a_mismatched_worker_is_rejected_before_any_submit_reaches_it(self):
        mismatched_container, mismatched_app = self._build_worker(build_id="a-different-build")
        backend = self._backend(mismatched_app)

        pipeline_data = {
            "generation_id": "gen-mismatch", "preset_id": "preset-1",
            "pipes": [{"name": "image/fake", "id": "p1", "enabled": True, "config": {}, "input": []}],
        }
        generation_id, outputs = self._run_generation(backend, pipeline_data)

        # Never reached the worker at all.
        self.assertIsNone(mismatched_container.coordinator.record_for(generation_id))

        row = self.repo.get_by_id(generation_id)
        self.assertEqual(row.state, S.FAILED)
        self.assertEqual(row.error_code, "fingerprint_mismatch")
        self.assertIn("build", row.error_message)

        errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
        self.assertEqual(len(errors), 1)


# -- happy path with an input asset -------------------------------------------

class TestHappyPathWithInputAsset(NativeRemoteBackendTestCase):
    def test_submit_uploads_the_asset_imports_the_artifact_and_succeeds(self):
        AssetAwarePipe.received_content = None
        source = self.storage_dir / "uploads" / "input.bin"
        source.parent.mkdir(parents=True)
        content = b"a real uploaded file, not a fixture stub"
        source.write_bytes(content)

        backend = self._backend(self.worker_app)
        pipeline_data = {
            "generation_id": "gen-happy", "preset_id": "preset-1",
            "pipes": [
                {"name": "asset/fake", "id": "p1", "enabled": True,
                 "config": {"input_path": "uploads/input.bin"}, "input": []},
                {"name": "image/fake", "id": "p2", "enabled": True, "config": {}, "input": []},
            ],
        }

        generation_id, outputs = self._run_generation(backend, pipeline_data)

        self.assertEqual(AssetAwarePipe.received_content, content)

        images = [o for o in outputs if isinstance(o, ImageGenerationOutput)]
        self.assertEqual(len(images), 1)
        self.assertFalse(images[0].temporary)
        # The output is a genuine local file the standard handler pipeline
        # can open - not an in-memory stub.
        self.assertIsNone(images[0].image.fp)  # already .load()ed, see outputs.py

        row = self.repo.get_by_id(generation_id)
        self.assertEqual(row.state, S.SUCCEEDED)
        self.assertGreater(row.event_cursor, 0)
        events = self.repo.list_events(generation_id)
        self.assertEqual(len(events), row.event_cursor)
        self.assertEqual(events[-1].kind, "succeeded")

        imports_dir = self.storage_dir / "remote_imports" / generation_id
        imported_files = list(imports_dir.glob("*"))
        self.assertEqual(len(imported_files), 1)
        self.assertFalse(imported_files[0].name.endswith(".part"))


# -- worker-side rejection ----------------------------------------------------

class TestWorkerRejection(NativeRemoteBackendTestCase):
    """A package the WORKER itself refuses (its own required_fingerprints
    check, independent of core's pre-gate) must still land the row on FAILED
    with the structured reason - entered one level below start_generation
    (directly at _consume_events) so this exercises the worker's REJECTED path
    specifically, rather than core's own pre-gate (covered above)."""

    def test_a_rejected_event_fails_the_row_with_the_structured_reason(self):
        from datetime import datetime, timedelta, timezone

        from src.features.generation.package_assembly import (
            assemble_execution_package,
            build_processed_pipeline,
        )
        from src.features.generation.pipeline_builder import BuiltPipeline
        from src.features.remote_execution.model_bundle_builder import build_model_bundle
        from src.features.remote_execution.records import RemoteExecution
        from src.features.remote_execution.transport import WorkerTransport

        backend = self._backend(self.worker_app)
        pipes = [{"name": "image/fake", "id": "p1", "enabled": True, "config": {}, "input": []}]
        built = BuiltPipeline(
            generation_id="gen-rejected", preset_id="preset-1", preset_template=None, pipes=pipes,
        )
        processed = build_processed_pipeline(pipes, backend._pipe_catalog)
        package = assemble_execution_package(
            built, pipe_catalog=backend._pipe_catalog,
            model_bundle=build_model_bundle(processed.pipes),
            engine="native",
            # Deliberately wrong - the WORKER will refuse this at submit time.
            required_fingerprints={"pipe_catalog": "not-the-real-value"},
            storage_dir=self.storage_dir,
        )

        row = self.repo.create(RemoteExecution(
            id="gen-rejected", provider="native.remote", state=S.PENDING,
            idempotency_key="gen-rejected", request_digest=str(package.request_digest),
        ))
        self.repo.claim_specific(row.id, "test-owner", 60)

        transport = WorkerTransport(
            "http://fake-worker", TOKEN, transport=httpx.ASGITransport(app=self.worker_app),
        )
        self._run(transport.submit(package))

        outputs = []
        self._run(backend._consume_events(
            "gen-rejected", package, transport, self.storage_dir, outputs.append,
        ))

        final = self.repo.get_by_id("gen-rejected")
        self.assertEqual(final.state, S.FAILED)
        self.assertEqual(final.error_code, "fingerprint_mismatch")

        errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
        self.assertEqual(len(errors), 1)
        self.assertIn("pipe_catalog", errors[0].error or errors[0].detail or "")


# -- cancel mid-run -----------------------------------------------------------

class TestCancelMidRun(NativeRemoteBackendTestCase):
    def test_cancelling_a_running_generation_lands_on_cancelled(self):
        backend = self._backend(self.worker_app)
        pipeline_data = {
            "generation_id": "gen-cancel", "preset_id": "preset-1",
            "pipes": [{"name": "cancelable/fake", "id": "p1", "enabled": True, "config": {}, "input": []}],
        }
        self._plant_generation_row("gen-cancel")

        outputs = []
        done = asyncio.Event()

        def emit(output):
            outputs.append(output)
            if output is None:
                done.set()

        async def scenario():
            generation_id = await backend.start_generation(pipeline_data, emit)

            for _ in range(200):
                record = self.worker_container.coordinator.record_for(generation_id)
                if record and record.latest_kind == "pipe_started":
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("worker never reached pipe_started")

            cancelled = await backend.cancel_generation(generation_id)
            self.assertTrue(cancelled)

            await asyncio.wait_for(done.wait(), timeout=10.0)
            return generation_id

        generation_id = self._run(scenario())

        row = self.repo.get_by_id(generation_id)
        self.assertEqual(row.state, S.CANCELLED)
        self.assertEqual([o for o in outputs if isinstance(o, ErrorGenerationOutput)], [])


# -- digest-corrupted artifact -------------------------------------------------

class TestCorruptedArtifactDownload(NativeRemoteBackendTestCase):
    def test_a_corrupted_artifact_fails_the_generation_and_imports_nothing(self):
        backend = self._backend(self.worker_app)
        pipeline_data = {
            "generation_id": "gen-corrupt", "preset_id": "preset-1",
            "pipes": [{"name": "image/fake", "id": "p1", "enabled": True, "config": {}, "input": []}],
        }
        self._plant_generation_row("gen-corrupt")

        outputs = []
        done = asyncio.Event()
        corrupted = {"done": False}

        def emit(output):
            outputs.append(output)
            if output is None:
                done.set()

        async def scenario():
            generation_id = await backend.start_generation(pipeline_data, emit)

            # As soon as the worker has produced (and journaled) the artifact,
            # corrupt the bytes it will actually serve - simulating on-disk
            # corruption / a misbehaving worker, not a fabricated digest.
            for _ in range(200):
                record = self.worker_container.coordinator.record_for(generation_id)
                if record and any(e.kind == "artifact" for e in record.events) and not corrupted["done"]:
                    artifact = next(e.artifacts[0] for e in record.events if e.kind == "artifact")
                    path = self.worker_container.coordinator.artifact_path(artifact.artifact_id)
                    path.write_bytes(b"corrupted bytes, not the real PNG")
                    corrupted["done"] = True
                if record and record.is_terminal:
                    break
                await asyncio.sleep(0.01)

            await asyncio.wait_for(done.wait(), timeout=10.0)
            return generation_id

        generation_id = self._run(scenario())
        self.assertTrue(corrupted["done"], "test never got a chance to corrupt the artifact")

        row = self.repo.get_by_id(generation_id)
        self.assertEqual(row.state, S.FAILED)

        images = [o for o in outputs if isinstance(o, ImageGenerationOutput)]
        self.assertEqual(images, [])

        imports_dir = self.storage_dir / "remote_imports" / generation_id
        if imports_dir.exists():
            self.assertEqual(list(imports_dir.glob("*")), [])


# -- event stream reconnect ----------------------------------------------------

class TestEventStreamReconnect(NativeRemoteBackendTestCase):
    def test_a_stream_that_closes_before_a_terminal_event_is_resumed_from_the_last_cursor(self):
        backend = self._backend(self.worker_app)
        real_transport = backend._transport()

        class DroppedOnceTransport:
            """Wraps the real transport; the FIRST `stream_events` call
            yields exactly one event then closes early - simulating an SSE
            connection dropped before the worker's terminal event arrived.
            Every other method, and every later `stream_events` call
            (the reconnect), passes straight through."""

            def __init__(self):
                self._first_call = True

            def __getattr__(self, name):
                return getattr(real_transport, name)

            async def stream_events(self, execution_id, after=0):
                if self._first_call:
                    self._first_call = False
                    async for event in real_transport.stream_events(execution_id, after=after):
                        yield event
                        return
                async for event in real_transport.stream_events(execution_id, after=after):
                    yield event

        backend._transport = lambda: DroppedOnceTransport()

        pipeline_data = {
            "generation_id": "gen-dropped-stream", "preset_id": "preset-1",
            "pipes": [{"name": "image/fake", "id": "p1", "enabled": True, "config": {}, "input": []}],
        }
        generation_id, outputs = self._run_generation(backend, pipeline_data)

        row = self.repo.get_by_id(generation_id)
        self.assertEqual(row.state, S.SUCCEEDED)
        images = [o for o in outputs if isinstance(o, ImageGenerationOutput)]
        self.assertEqual(len(images), 1)


# -- model bundle staging ------------------------------------------------------

class TestModelBundleStagingHappyPath(NativeRemoteBackendTestCase):
    def test_a_referenced_model_is_pushed_before_submit_with_progress_and_runs_from_the_depot(self):
        ModelAwarePipe.received_file_path = None
        content = b"fake checkpoint bytes" * 5000
        self._register_model(role="checkpoint", filename="dit.safetensors", content=content)

        backend = self._backend(self.worker_app)
        pipeline_data = {
            "generation_id": "gen-model-happy", "preset_id": "preset-1",
            "pipes": [{
                "name": "model_aware/fake", "id": "p1", "enabled": True,
                "config": {"checkpoint": {"file_path": str(Path(self.temp_dir) / "model_sources" / "dit.safetensors")}},
                "input": [],
            }],
        }

        generation_id, outputs = self._run_generation(backend, pipeline_data)

        row = self.repo.get_by_id(generation_id)
        self.assertEqual(row.state, S.SUCCEEDED)

        # The pipe ran against the worker's depot copy, not the dispatching
        # host's original path.
        depot_dir = self.worker_container.model_depot.depot_dir
        self.assertTrue(ModelAwarePipe.received_file_path.startswith(str(depot_dir)))
        self.assertEqual(Path(ModelAwarePipe.received_file_path).read_bytes(), content)

        staging_events = [
            o for o in outputs if isinstance(o, ProgressGenerationOutput) and o.state == "staging_models"
        ]
        self.assertGreaterEqual(len(staging_events), 2)
        self.assertEqual(staging_events[0].progress.current, 0)
        self.assertEqual(staging_events[-1].progress.current, 100)
        self.assertIn("Staging models", staging_events[0].title)


class TestModelBundleStagingResume(NativeRemoteBackendTestCase):
    def test_a_second_dispatch_referencing_an_already_staged_model_pushes_nothing(self):
        ModelAwarePipe.received_file_path = None
        content = b"fake checkpoint bytes" * 5000
        source_path = self._register_model(role="checkpoint", filename="dit.safetensors", content=content)

        backend = self._backend(self.worker_app)

        def _pipeline(generation_id: str) -> dict:
            return {
                "generation_id": generation_id, "preset_id": "preset-1",
                "pipes": [{
                    "name": "model_aware/fake", "id": "p1", "enabled": True,
                    "config": {"checkpoint": {"file_path": str(source_path)}}, "input": [],
                }],
            }

        first_id, first_outputs = self._run_generation(backend, _pipeline("gen-model-resume-1"))
        self.assertEqual(self.repo.get_by_id(first_id).state, S.SUCCEEDED)
        first_staging = [o for o in first_outputs if isinstance(o, ProgressGenerationOutput) and o.state == "staging_models"]
        self.assertGreater(len(first_staging), 0)

        second_id, second_outputs = self._run_generation(backend, _pipeline("gen-model-resume-2"))
        self.assertEqual(self.repo.get_by_id(second_id).state, S.SUCCEEDED)
        second_staging = [o for o in second_outputs if isinstance(o, ProgressGenerationOutput) and o.state == "staging_models"]
        # Nothing missing on the worker's depot this time - the inventory
        # check alone answers "present", and stage_model_bundle never even
        # reaches the upload loop.
        self.assertEqual(second_staging, [])


class TestDirectoryLayoutModelRefusal(NativeRemoteBackendTestCase):
    def test_a_directory_layout_model_fails_the_generation_with_a_friendly_message_naming_the_preset(self):
        self._register_model(role="llm", filename="gemma3", content=b"placeholder", is_directory=True)

        backend = self._backend(self.worker_app)
        pipeline_data = {
            "generation_id": "gen-dir-model", "preset_id": "my-preset",
            "pipes": [{
                "name": "model_aware/fake", "id": "p1", "enabled": True,
                "config": {"checkpoint": {"file_path": str(Path(self.temp_dir) / "model_sources" / "gemma3")}},
                "input": [],
            }],
        }

        generation_id, outputs = self._run_generation(backend, pipeline_data)

        # Refused before any row was ever created - never reached the worker.
        self.assertIsNone(self.repo.get_by_id(generation_id))

        errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
        self.assertEqual(len(errors), 1)
        self.assertIn("my-preset", errors[0].error)
        self.assertIn("directory", errors[0].error)
        self.assertNotIn("Traceback", errors[0].error)


class TestModelListing(unittest.TestCase):
    def test_remote_backend_lists_the_hosts_models(self):
        from unittest.mock import patch as mock_patch

        from src.features.backends.model_listing import BackendModel

        config = NativeRemoteBackendConfig(
            id="r1", name="Remote", base_url="http://w", worker_token="t",
        )
        backend = RemoteNativeBackend(config)
        self.assertTrue(backend.supports_model_listing())

        fake = [BackendModel(model_type="checkpoint", filename="a.safetensors", ref="/m/a.safetensors")]
        with mock_patch("src.features.backends.native_model_scan.scan_native_models", return_value=fake), \
             mock_patch("src.platform.settings.settings.Settings.get_models_dir", return_value="/m"):
            models = asyncio.run(backend.list_models())
        self.assertEqual(models, fake)
