"""Admin model-sync ops against a REAL worker app (`httpx.ASGITransport`,
never a real socket) - sync view join, push, and fetch (with a faked
provider). A `FakeModelRepository` stands in for the database, same idiom as
`test_model_bundle_staging.py`."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.models.records import Model
from src.features.models.records import ModelInfo as ModelProviderLink
from src.features.providers import ProviderCapability, ProviderMetadata, RemoteDownloadRef
from src.features.remote_execution import ops
from src.features.remote_execution.transport import WorkerTransport
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY
from src.platform.worker_protocol import ContentDigest, ModelBundleEntryV1

TOKEN = "secret-worker-token"


class FakeCatalog:
    def get_pipe(self, name):
        return None

    def get_available_pipes(self):
        return []

    def remote_relevant_plugin_ids(self):
        return set()


class FakeModelRepository:
    """A `get_by_id`/`get_all` stand-in - no database."""

    def __init__(self):
        self._by_id: Dict[str, Model] = {}

    def add(self, model: Model) -> Model:
        self._by_id[model.id] = model
        return model

    def get_by_id(self, model_id: str, include_providers: bool = True, **_kwargs) -> Optional[Model]:
        return self._by_id.get(model_id)

    def get_all(self, *, limit=None, include_providers=True, include_tags=True, **_kwargs) -> List[Model]:
        return list(self._by_id.values())

    def update_digest(self, model_id: str, *, sha256: str, file_size: int) -> None:
        model = self._by_id[model_id]
        model.sha256, model.file_size = sha256, file_size


def _model(*, model_id: str, filename: str, role: str, file_path=None, content=None, providers=None) -> Model:
    sha256 = hashlib.sha256(content).hexdigest() if content is not None else None
    file_size = len(content) if content is not None else None
    return Model(
        id=model_id, filename=filename, model_type=role, file_path=file_path,
        sha256=sha256, file_size=file_size, providers=providers or [],
    )


def _relative_path(role: str, filename: str) -> str:
    return f"{MODEL_TYPE_TO_DIRECTORY.get(role, role)}/{filename}"


class OpsTestCase(unittest.TestCase):
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

    def _source(self, filename: str, content: bytes) -> Path:
        path = self.source_dir / filename
        path.write_bytes(content)
        return path

    def _stage_on_worker(self, *, role: str, filename: str, content: bytes) -> None:
        entry = ModelBundleEntryV1(
            logical_id=f"{role}/{filename}", role=role, relative_path=_relative_path(role, filename),
            digest=ContentDigest(algorithm="sha256", hex=hashlib.sha256(content).hexdigest()),
            size_bytes=len(content),
        )
        self.model_depot.stage(entry, [content])

    def _run(self, coro):
        return asyncio.run(coro)


# -- sync view ----------------------------------------------------------------

class TestSyncView(OpsTestCase):
    def test_the_join_reports_missing_on_worker_and_digest_mismatch(self):
        missing_content = b"checkpoint bytes" * 1000
        self.repo.add(_model(
            model_id="m-missing", filename="dit.safetensors", role="checkpoint",
            file_path=str(self._source("dit.safetensors", missing_content)), content=missing_content,
        ))

        present_content = b"vae bytes" * 500
        self.repo.add(_model(
            model_id="m-present", filename="vae.safetensors", role="vae",
            file_path=str(self._source("vae.safetensors", present_content)), content=present_content,
        ))
        self._stage_on_worker(role="vae", filename="vae.safetensors", content=present_content)

        host_content = b"lora bytes" * 200
        worker_content = b"stale lora bytes!" * 100  # different size than the host's copy
        self.repo.add(_model(
            model_id="m-stale", filename="style.safetensors", role="lora",
            file_path=str(self._source("style.safetensors", host_content)), content=host_content,
        ))
        self._stage_on_worker(role="lora", filename="style.safetensors", content=worker_content)

        rows = self._run(ops.sync_view(self.repo, MagicMock(), self.transport))
        by_id = {r["model_id"]: r for r in rows}

        self.assertEqual(by_id["m-missing"]["status"], "missing")
        self.assertEqual(by_id["m-present"]["status"], "on_worker")
        self.assertEqual(by_id["m-stale"]["status"], "digest_mismatch")

    def test_directory_layout_models_are_excluded(self):
        self.repo.add(Model(id="m-dir", filename="gemma3", model_type="llm", is_directory=True, providers=[]))

        rows = self._run(ops.sync_view(self.repo, MagicMock(), self.transport))
        self.assertEqual(rows, [])

    def test_a_linkless_model_can_fetch_when_a_provider_supports_hash_lookup(self):
        content = b"checkpoint bytes" * 100
        self.repo.add(_model(
            model_id="m-linkless", filename="dit.safetensors", role="checkpoint",
            file_path=str(self._source("dit.safetensors", content)), content=content,
        ))

        registry = MagicMock()
        registry.get_providers_with_capability.return_value = [MagicMock()]

        rows = self._run(ops.sync_view(self.repo, registry, self.transport))

        self.assertTrue(rows[0]["providers_can_fetch"])


# -- push -----------------------------------------------------------------

class TestPushModels(OpsTestCase):
    def test_a_missing_model_is_pushed_and_lands_on_the_depot_with_a_transfer_id(self):
        content = b"checkpoint bytes" * 5000
        self.repo.add(_model(
            model_id="m-1", filename="dit.safetensors", role="checkpoint",
            file_path=str(self._source("dit.safetensors", content)), content=content,
        ))

        results = self._run(ops.push_models(["m-1"], model_repository=self.repo, transport=self.transport))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["model_id"], "m-1")
        self.assertIsNotNone(results[0]["transfer_id"])
        self.assertNotIn("error", results[0])

        dest = self.model_depot.depot_dir / _relative_path("checkpoint", "dit.safetensors")
        self.assertEqual(dest.read_bytes(), content)

    def test_an_unknown_model_id_is_reported_without_failing_the_batch(self):
        content = b"checkpoint bytes" * 100
        self.repo.add(_model(
            model_id="m-1", filename="dit.safetensors", role="checkpoint",
            file_path=str(self._source("dit.safetensors", content)), content=content,
        ))

        results = self._run(
            ops.push_models(["does-not-exist", "m-1"], model_repository=self.repo, transport=self.transport)
        )

        by_id = {r["model_id"]: r for r in results}
        self.assertEqual(by_id["does-not-exist"]["error"], "model not found")
        self.assertIsNone(by_id["does-not-exist"]["transfer_id"])
        self.assertIsNotNone(by_id["m-1"]["transfer_id"])


# -- fetch ------------------------------------------------------------------

def _linked_model(*, sha256: str, file_size: int) -> Model:
    link = ModelProviderLink(model_id="m-fetch", provider="fake-provider", provider_model_id="123")
    return Model(
        id="m-fetch", filename="dit.safetensors", model_type="checkpoint", file_path="/host/dit.safetensors",
        sha256=sha256, file_size=file_size, providers=[link],
    )


class TestFetchModels(OpsTestCase):
    def _provider_registry(self, provider) -> MagicMock:
        registry = MagicMock()
        registry.get_provider.return_value = provider
        return registry

    def _fake_provider(self, *, ref: RemoteDownloadRef) -> MagicMock:
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="fake-provider", name="Fake", description="", website="",
            capabilities=[ProviderCapability.REMOTE_DOWNLOAD],
        )
        provider.resolve_remote_download = AsyncMock(return_value=ref)
        return provider

    def test_a_linked_model_is_resolved_and_handed_to_the_worker_to_pull(self):
        content = b"checkpoint bytes" * 1000
        digest = hashlib.sha256(content).hexdigest()
        self.repo.add(_linked_model(sha256=digest, file_size=len(content)))

        ref = RemoteDownloadRef(url="https://cdn.example.invalid/dit.safetensors")
        provider = self._fake_provider(ref=ref)
        registry = self._provider_registry(provider)

        results = self._run(ops.fetch_models(
            ["m-fetch"], model_repository=self.repo, provider_registry=registry, transport=self.transport,
        ))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["model_id"], "m-fetch")
        self.assertIsNotNone(results[0]["transfer_id"])
        provider.resolve_remote_download.assert_awaited_once_with("123", None)

    def test_an_unlinked_model_is_a_typed_per_model_failure_not_a_batch_failure(self):
        content = b"checkpoint bytes" * 10
        self.repo.add(_model(
            model_id="m-unlinked", filename="unlinked.safetensors", role="checkpoint",
            file_path=str(self._source("unlinked.safetensors", content)), content=content,
        ))

        results = self._run(ops.fetch_models(
            ["m-unlinked"], model_repository=self.repo, provider_registry=MagicMock(), transport=self.transport,
        ))

        self.assertEqual(results[0]["model_id"], "m-unlinked")
        self.assertIsNone(results[0]["transfer_id"])
        self.assertIn("error", results[0])


if __name__ == "__main__":
    unittest.main()
