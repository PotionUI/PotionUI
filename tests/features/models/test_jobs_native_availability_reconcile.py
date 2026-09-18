
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.features.downloads.models import Download, DownloadStatus
from src.features.models.jobs import ModelJobs

class FakeDownloadQueue:
    def __init__(self, download: Download):
        self._download = download

    async def queue_model_download(self, **kwargs):
        return self._download

    def get_download(self, download_id):
        return self._download

def _job(tmp_path: Path, reconciler=None, backend_registry=None) -> ModelJobs:
    destination = tmp_path / "model.safetensors"
    destination.write_bytes(b"weights")
    download = Download(id="d1", status=DownloadStatus.COMPLETED, destination_path=str(destination))

    scanner = MagicMock()
    scanner.index_single_model.return_value = MagicMock(id="m1")

    job = ModelJobs(
        model_repository=MagicMock(),
        plugin_registry=MagicMock(),
        scanner=scanner,
        download_queue=FakeDownloadQueue(download),
        backend_registry=backend_registry,
        native_availability_reconciler=reconciler,
    )
    return job

def test_run_download_and_index_reconciles_native_availability(tmp_path):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    backend_registry = MagicMock()

    job = _job(tmp_path, reconciler=reconciler, backend_registry=backend_registry)

    with patch("src.features.models.jobs.asyncio.sleep", new=AsyncMock()), \
         patch("src.platform.settings.repository.SettingRepository") as mock_settings:
        mock_settings.return_value.get_setting_by_key.return_value = None
        asyncio.run(job.run_download_and_index(
            name="test-model", link="https://1.1.1.1/model.safetensors", sha256="",
        ))

    reconciler.reconcile.assert_awaited_once_with(backend_registry)

def test_run_download_and_index_skips_reconcile_when_indexing_fails(tmp_path):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()

    job = _job(tmp_path, reconciler=reconciler)
    job.scanner.index_single_model.return_value = None

    with patch("src.features.models.jobs.asyncio.sleep", new=AsyncMock()), \
         patch("src.platform.settings.repository.SettingRepository") as mock_settings:
        mock_settings.return_value.get_setting_by_key.return_value = None
        asyncio.run(job.run_download_and_index(
            name="test-model", link="https://1.1.1.1/model.safetensors", sha256="",
        ))

    reconciler.reconcile.assert_not_awaited()
