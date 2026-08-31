"""stage_model_bundle against a REAL worker app (`httpx.ASGITransport`, never
a real socket) - inventory, chunked push, resume, and progress events."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional

import httpx

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.models.records import Model
from src.features.remote_execution.model_bundle_staging import (
    ModelStagingSourceError,
    stage_model_bundle,
)
from src.features.remote_execution.transport import WorkerTransport
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot
from src.pipelines.outputs import ProgressGenerationOutput
from src.platform.worker_protocol import ContentDigest, ModelBundleEntryV1, ModelBundleManifestV1

TOKEN = "secret-worker-token"


class FakeCatalog:
    def get_pipe(self, name):
        return None

    def get_available_pipes(self):
        return []

    def remote_relevant_plugin_ids(self):
        return set()


class FakeModelRepository:
    """A `get_by_identity`-only stand-in - no database."""

    def __init__(self):
        self._by_identity: Dict[tuple, Model] = {}

    def register(self, *, role: str, filename: str, file_path: str) -> None:
        self._by_identity[(role, filename)] = Model(
            id=f"m-{filename}", filename=filename, file_path=file_path, model_type=role,
        )

    def get_by_identity(self, model_type: str, filename: str, include_providers: bool = True) -> Optional[Model]:
        return self._by_identity.get((model_type, filename))


def _entry(role: str, filename: str, content: bytes) -> ModelBundleEntryV1:
    digest = hashlib.sha256(content).hexdigest()
    return ModelBundleEntryV1(
        logical_id=f"{role}/{filename}", role=role, relative_path=f"{role}/{filename}",
        digest=ContentDigest(algorithm="sha256", hex=digest), size_bytes=len(content),
    )


def _bundle(*entries: ModelBundleEntryV1) -> ModelBundleManifestV1:
    canonical = "".join(e.logical_id for e in entries).encode()
    return ModelBundleManifestV1(
        bundle_id="bundle-staging-test",
        bundle_digest=ContentDigest(algorithm="sha256", hex=hashlib.sha256(canonical).hexdigest()),
        entries=entries,
    )


class ModelBundleStagingTestCase(unittest.TestCase):
    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.model_depot = ModelDepot(depot_dir=self.work_dir / "models")
        config = WorkerConfig(
            token=TOKEN, worker_id="worker-1", provider="manual", host="127.0.0.1", port=0,
            work_dir=self.work_dir / "work", artifacts_dir=self.work_dir / "work" / "artifacts",
            build_id="test-build", device="cpu", dtype="fp32", vram_limit_gb=None,
        )
        journal = WorkerJournal(config.work_dir)
        catalog = FakeCatalog()
        coordinator = WorkerCoordinator(
            worker_id=config.worker_id, pipe_catalog=catalog, journal=journal,
            artifacts_dir=config.artifacts_dir, device=config.device, dtype=config.dtype,
            vram_limit_gb=config.vram_limit_gb, build_id=config.build_id,
            model_depot=self.model_depot,
        )
        self.container = WorkerContainer(
            config=config, pipe_catalog=catalog, journal=journal, coordinator=coordinator,
            gpu_monitor=None, system_monitor=None, model_depot=self.model_depot,
        )
        self.app = create_worker_app(container=self.container)
        self.transport = WorkerTransport(
            "http://fake-worker", TOKEN, transport=httpx.ASGITransport(app=self.app),
        )
        self.repo = FakeModelRepository()
        self.source_dir = self.work_dir / "sources"
        self.source_dir.mkdir()

    def _register_source(self, role: str, filename: str, content: bytes) -> Path:
        path = self.source_dir / filename
        path.write_bytes(content)
        self.repo.register(role="checkpoint" if role == "checkpoint" else role, filename=filename, file_path=str(path))
        return path


class TestHappyPathPush(ModelBundleStagingTestCase):
    def test_a_missing_entry_is_pushed_and_lands_on_the_depot(self):
        content = b"checkpoint bytes" * 1000
        self._register_source("checkpoint", "dit.safetensors", content)
        entry = _entry("checkpoint", "dit.safetensors", content)
        bundle = _bundle(entry)

        events = []
        import asyncio
        asyncio.run(stage_model_bundle(bundle, self.transport, events.append, model_repository=self.repo))

        dest = self.model_depot.depot_dir / entry.relative_path
        self.assertEqual(dest.read_bytes(), content)

        progress_events = [e for e in events if isinstance(e, ProgressGenerationOutput)]
        self.assertGreaterEqual(len(progress_events), 2)
        self.assertEqual(progress_events[0].state, "staging_models")
        self.assertEqual(progress_events[-1].progress.current, 100)


class TestResumeAfterPartial(ModelBundleStagingTestCase):
    def test_an_already_present_entry_is_never_re_uploaded(self):
        import asyncio

        content_a = b"already-staged bytes" * 1000
        content_b = b"still-missing bytes" * 1000
        self._register_source("checkpoint", "already.safetensors", content_a)
        self._register_source("vae", "missing.safetensors", content_b)
        entry_a = _entry("checkpoint", "already.safetensors", content_a)
        entry_b = _entry("vae", "missing.safetensors", content_b)
        bundle = _bundle(entry_a, entry_b)

        # Pre-stage entry_a directly on the depot, simulating an earlier
        # (possibly partial) dispatch that already got this one across.
        self.model_depot.stage(entry_a, [content_a])

        # A repository that raises if asked to resolve entry_a's source -
        # proves the already-present entry is never even looked up, let
        # alone uploaded.
        class NoReRegisterRepo(FakeModelRepository):
            def get_by_identity(self, model_type, filename, include_providers=True):
                if (model_type, filename) == ("checkpoint", "already.safetensors"):
                    raise AssertionError("already-staged entry must not be resolved for re-upload")
                return super().get_by_identity(model_type, filename, include_providers)

        repo = NoReRegisterRepo()
        repo.register(role="vae", filename="missing.safetensors", file_path=str(self.source_dir / "missing.safetensors"))

        events = []
        asyncio.run(stage_model_bundle(bundle, self.transport, events.append, model_repository=repo))

        dest_b = self.model_depot.depot_dir / entry_b.relative_path
        self.assertEqual(dest_b.read_bytes(), content_b)

        progress_events = [e for e in events if isinstance(e, ProgressGenerationOutput)]
        # total_bytes only counts what actually needed pushing (entry_b alone).
        self.assertEqual(progress_events[-1].progress.current, 100)
        self.assertIn(f"{len(content_b) / 1_000_000_000:.1f}", progress_events[-1].title)


class TestNoModelsInBundle(ModelBundleStagingTestCase):
    def test_an_empty_bundle_never_calls_the_worker(self):
        import asyncio

        empty = ModelBundleManifestV1(
            bundle_id="empty", bundle_digest=ContentDigest(algorithm="sha256", hex="ab" * 32), entries=(),
        )
        events = []
        asyncio.run(stage_model_bundle(empty, self.transport, events.append, model_repository=self.repo))
        self.assertEqual(events, [])


class TestSourceGoneMissing(ModelBundleStagingTestCase):
    def test_an_entry_whose_source_no_longer_resolves_raises_cleanly(self):
        import asyncio

        content = b"orphaned bytes" * 1000
        entry = _entry("checkpoint", "orphaned.safetensors", content)
        bundle = _bundle(entry)

        with self.assertRaises(ModelStagingSourceError):
            asyncio.run(stage_model_bundle(bundle, self.transport, lambda o: None, model_repository=self.repo))


if __name__ == "__main__":
    unittest.main()
