"""`DownloadWorker`'s remote destination: fetch straight onto a
`native.remote` worker's depot instead of local disk, against a REAL worker
app (`httpx.ASGITransport`/`httpx.MockTransport`, never a real socket) - same
idiom as `tests/features/remote_execution/test_ops.py`.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.bootstrap.worker_app import create_worker_app
from src.bootstrap.worker_container import WorkerContainer
from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.features.downloads.exceptions import DownloadAuthenticationException
from src.features.downloads.models import Download, DownloadSettings, DownloadStatus, DownloadType
from src.features.downloads.worker import DownloadWorker
from src.features.models.availability_records import ModelAvailability
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.providers import ProviderCapability, ProviderMetadata
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.features.remote_execution.worker.model_depot import ModelDepot

TOKEN = "secret-worker-token"
BACKEND_ID = "remote-1"


class FakeCatalog:
    def get_pipe(self, name):
        return None

    def get_available_pipes(self):
        return []

    def remote_relevant_plugin_ids(self):
        return set()


class FakeModelRepository:
    def __init__(self):
        self._by_id: Dict[str, object] = {}

    def get_all(self, *, include_providers=True, include_tags=True, **_kwargs):
        return list(self._by_id.values())

    def create(self, model):
        self._by_id[model.id] = model
        return model


class FakeAvailabilityRepository:
    def __init__(self):
        self.upserted: List[ModelAvailability] = []

    def upsert(self, availability: ModelAvailability) -> None:
        self.upserted.append(availability)

    def delete_for_backend(self, backend_id: str, keep_model_ids) -> int:
        return 0


class FakeDownloadRepository:
    """An in-memory stand-in tracking one `Download` row's mutations, the
    same subset of `DownloadRepository` the worker actually calls."""

    def __init__(self, download: Download):
        self.download = download

    def get_by_id(self, download_id: str) -> Optional[Download]:
        return self.download if download_id == self.download.id else None

    def update_status(self, download_id: str, status: DownloadStatus, error_message: str = None) -> bool:
        self.download.status = status
        self.download.error_message = error_message
        return True

    def update_progress(self, download_id: str, progress: float, downloaded_bytes: int, speed) -> bool:
        self.download.progress = progress
        self.download.downloaded_bytes = downloaded_bytes
        return True

    def update_total_bytes(self, download_id: str, total_bytes) -> bool:
        self.download.total_bytes = total_bytes
        return True

    def increment_retry(self, download_id: str) -> int:
        self.download.retry_count += 1
        return self.download.retry_count


class RemoteDestinationTestCase(unittest.IsolatedAsyncioTestCase):
    """Wires a real worker app (fetch endpoint + depot) behind
    `httpx.ASGITransport`, with the depot's own upstream fetch pointed at a
    fake CDN via `httpx.MockTransport` - two distinct fakes for the two hops
    a remote download makes (core -> worker, worker -> upstream)."""

    UPSTREAM_CONTENT = b"checkpoint weights" * 1000

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.upstream_calls: List[str] = []
        self.model_depot = ModelDepot(
            depot_dir=self.work_dir / "models",
            http_transport=httpx.MockTransport(self._upstream_handler),
        )
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
        worker_container = WorkerContainer(
            config=config, pipe_catalog=catalog, journal=journal, coordinator=coordinator,
            gpu_monitor=None, system_monitor=None, model_depot=self.model_depot,
        )
        self.worker_app = create_worker_app(container=worker_container)
        self.worker_asgi_transport = httpx.ASGITransport(app=self.worker_app)

        self.models_root = self.work_dir / "host-models"
        self.backend_config = NativeRemoteBackendConfig(
            id=BACKEND_ID, name="Remote One", base_url="http://fake-worker", worker_token=TOKEN,
        )
        # `list_models()` (all indexing needs) only touches `_transport()` -
        # no `bind_remote_context()`, that's dispatch-fingerprint-only setup.
        self.backend = RemoteNativeBackend(
            self.backend_config, transport_override=self.worker_asgi_transport,
        )

        self.model_repository = FakeModelRepository()
        self.availability_repository = FakeAvailabilityRepository()
        self.indexer = BackendModelIndexer(
            model_repository=self.model_repository, availability_repository=self.availability_repository,
        )
        self.backend_registry = _FakeBackendRegistry(self.backend_config, self.backend)

    def _upstream_handler(self, request: httpx.Request) -> httpx.Response:
        self.upstream_calls.append(str(request.url))
        return httpx.Response(200, content=self.UPSTREAM_CONTENT)

    def _download(self, *, url: str, provider_id: Optional[str] = None) -> Download:
        return Download(
            id="dl-1", type=DownloadType.MODEL, url=url,
            destination_path=str(self.models_root / "checkpoints" / "model.safetensors"),
            filename="model.safetensors", status=DownloadStatus.PENDING,
            destination_backend_id=BACKEND_ID, provider_id=provider_id,
        )

    def _worker(self, *, download: Download, provider_registry_factory=None) -> DownloadWorker:
        return DownloadWorker(
            settings=DownloadSettings(default_model_directory=str(self.models_root)),
            repo=FakeDownloadRepository(download),
            connection_hub=AsyncMock(),
            provider_registry_factory=provider_registry_factory or (lambda: None),
            backend_registry=self.backend_registry,
            backend_model_indexer=self.indexer,
            worker_transport_override=self.worker_asgi_transport,
        )


class _FakeBackendConfigStore:
    def __init__(self, config):
        self._config = config

    def get_backend(self, backend_id):
        return self._config if backend_id == self._config.id else None


class _FakeBackendRegistry:
    """Just enough of `BackendRegistry` for `resolve_remote_backend_config`
    (`.backend_config_store.get_backend` -> the config) and
    `DownloadWorker._index_remote_backend` (`.get_backend` -> the instance) -
    two distinct lookups a real `BackendRegistry` also keeps separate."""

    def __init__(self, config, backend):
        self.backend_config_store = _FakeBackendConfigStore(config)
        self._config = config
        self._backend = backend

    def get_backend(self, backend_id):
        return self._backend if backend_id == self._config.id else None


class TestRemoteDownloadHappyPath(RemoteDestinationTestCase):
    async def test_a_direct_url_completes_and_reindexes_the_backend(self):
        download = self._download(url="https://cdn.example.invalid/model.safetensors")
        worker = self._worker(download=download)
        worker.session = MagicMock()  # unused: no provider claims this URL

        await worker._process_download(download.id)

        self.assertEqual(download.status, DownloadStatus.COMPLETED)
        self.assertEqual(download.total_bytes, len(self.UPSTREAM_CONTENT))
        self.assertEqual(download.downloaded_bytes, len(self.UPSTREAM_CONTENT))

        # The bytes are really on the worker's depot, not this host's disk.
        self.assertFalse(Path(download.destination_path).exists())
        entries = self.model_depot.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["relative_path"], "checkpoints/model.safetensors")

        # Indexed exactly like a manual Admin -> Backends -> Models sync would.
        self.assertEqual(len(self.model_repository._by_id), 1)
        created = next(iter(self.model_repository._by_id.values()))
        self.assertEqual(created.model_type, "checkpoint")
        self.assertEqual(created.filename, "model.safetensors")
        self.assertIsNone(created.file_path)  # a remote ref, not a local path
        self.assertEqual(len(self.availability_repository.upserted), 1)
        self.assertEqual(self.availability_repository.upserted[0].backend_id, BACKEND_ID)

        worker.conn.send_download_status.assert_any_call(
            download.id, 'completed', download.filename, path=download.destination_path,
        )
        # At least one progress broadcast reached 100%.
        final_progress_calls = [
            call for call in worker.conn.send_download_progress.await_args_list
            if call.args[1] == 1.0
        ]
        self.assertTrue(final_progress_calls)


class TestRemoteDownloadChecksumNormalization(RemoteDestinationTestCase):
    async def test_an_uppercase_checksum_is_lowercased_for_the_pod_fetch(self):
        download = self._download(url="https://cdn.example.invalid/model.safetensors")
        download.checksum_sha256 = hashlib.sha256(self.UPSTREAM_CONTENT).hexdigest().upper()
        worker = self._worker(download=download)
        worker.session = MagicMock()

        await worker._process_download(download.id)

        self.assertEqual(download.status, DownloadStatus.COMPLETED)
        entries = self.model_depot.list_entries()
        self.assertEqual(len(entries), 1)


class TestRemoteDownloadProviderCapabilityGuard(RemoteDestinationTestCase):
    async def test_a_provider_without_remote_download_capability_is_refused_without_retry(self):
        provider = MagicMock()
        provider.get_metadata.return_value = ProviderMetadata(
            id="fake-provider", name="Fake Provider", description="", website="",
            capabilities=[],  # no REMOTE_DOWNLOAD
        )
        registry = MagicMock()
        registry.get_provider.return_value = provider

        download = self._download(
            url="https://fake-provider.example/model.safetensors", provider_id="fake-provider",
        )
        worker = self._worker(download=download, provider_registry_factory=lambda: registry)
        worker.session = MagicMock()

        with self.assertRaises(DownloadAuthenticationException) as ctx:
            await worker._fetch_remote(download)
        self.assertIn("Fake Provider", str(ctx.exception))
        provider.prepare_download.assert_not_called()


class TestRemoteDownloadPodTransferFailure(RemoteDestinationTestCase):
    def _upstream_handler(self, request: httpx.Request) -> httpx.Response:
        self.upstream_calls.append(str(request.url))
        return httpx.Response(500, content=b"upstream is down")

    async def test_a_failed_pod_transfer_fails_the_download_with_the_workers_error(self):
        download = self._download(url="https://cdn.example.invalid/model.safetensors")
        worker = self._worker(download=download)
        worker.session = MagicMock()
        worker.settings.auto_retry_failed = False

        await worker._process_download(download.id)

        self.assertEqual(download.status, DownloadStatus.FAILED)
        self.assertIn("HTTP 500", download.error_message or "")
        self.assertEqual(len(self.model_repository._by_id), 0)


if __name__ == "__main__":
    unittest.main()
