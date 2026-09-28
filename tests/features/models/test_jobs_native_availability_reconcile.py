import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.features.downloads.models import Download, DownloadStatus
from src.features.models.jobs import ModelJobs
from unittest.mock import patch


class FakeDownloadQueue:
    def __init__(self, download: Download):
        self._download = download

    async def queue_model_download(self, **kwargs):
        return self._download

    def get_download(self, download_id):
        return self._download


def _job(tmp_path: Path, coordinator=None, backend_registry=None) -> ModelJobs:
    destination = tmp_path / "model.safetensors"
    destination.write_bytes(b"weights")
    download = Download(id="d1", status=DownloadStatus.COMPLETED, destination_path=str(destination))

    scanner = MagicMock()
    coordinator = coordinator or MagicMock()

    job = ModelJobs(
        model_repository=MagicMock(),
        plugin_registry=MagicMock(),
        scanner=scanner,
        download_queue=FakeDownloadQueue(download),
        coordinator=coordinator,
        backend_registry=backend_registry,
    )
    return job


def test_run_download_and_index_indexes_through_the_coordinator(tmp_path):
    coordinator = MagicMock()
    coordinator.index_path.return_value = {"model_id": "m1", "indexed": True}
    backend_registry = MagicMock()

    job = _job(tmp_path, coordinator=coordinator, backend_registry=backend_registry)

    with patch("src.features.models.jobs.asyncio.sleep", new=AsyncMock()):
        asyncio.run(job.run_download_and_index(
            name="test-model", link="https://1.1.1.1/model.safetensors", sha256="",
        ))

    coordinator.index_path.assert_called_once()


def test_run_download_and_index_fires_no_hook_when_indexing_fails(tmp_path):
    coordinator = MagicMock()
    coordinator.index_path.return_value = {"model_id": None, "indexed": False}

    job = _job(tmp_path, coordinator=coordinator)

    with patch("src.features.models.jobs.asyncio.sleep", new=AsyncMock()), \
         patch("src.features.models.jobs.execute_hook") as mock_hook:
        mock_hook.return_value = ({}, False)
        asyncio.run(job.run_download_and_index(
            name="test-model", link="https://1.1.1.1/model.safetensors", sha256="",
        ))

    mock_hook.assert_not_called()


def test_run_download_and_index_indexes_off_the_event_loop_thread(tmp_path):
    coordinator = MagicMock()
    loop_thread = threading.get_ident()
    index_threads = []

    def recording_index(path):
        index_threads.append(threading.get_ident())
        return {"model_id": "m1", "indexed": True}

    coordinator.index_path.side_effect = recording_index

    job = _job(tmp_path, coordinator=coordinator)

    with patch("src.features.models.jobs.asyncio.sleep", new=AsyncMock()):
        asyncio.run(job.run_download_and_index(
            name="test-model", link="https://1.1.1.1/model.safetensors", sha256="",
        ))

    assert len(index_threads) == 1
    assert index_threads[0] != loop_thread
