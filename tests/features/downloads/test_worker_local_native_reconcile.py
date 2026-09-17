"""A local (non-remote-destination) download only writes a file to this
host's disk - unlike a remote-destination fetch (see
`test_worker_remote_destination.py`'s `_index_remote_backend` coverage), it
never re-indexes anything. Once a native backend has been indexed, that
leaves the new file invisible on Generate until someone runs "Index models"
by hand. `DownloadWorker._process_download` must reconcile native
availability itself once the download completes - see
`src.features.models.native_availability_reconciler`.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.features.downloads.models import Download, DownloadSettings, DownloadStatus, DownloadType
from src.features.downloads.worker import DownloadWorker


@pytest.fixture
def mock_settings():
    return DownloadSettings(
        max_concurrent_downloads=2, auto_retry_failed=True, max_retries=3, chunk_size_kb=1024,
        verify_checksum=False,
    )


@pytest.fixture
def mock_repository():
    repo = Mock()
    repo.get_all.return_value = []
    repo.get_active.return_value = []
    repo.get_pending.return_value = []
    return repo


@pytest.fixture
def backend_registry():
    return MagicMock()


@pytest.fixture
def worker(mock_settings, mock_repository, backend_registry):
    return DownloadWorker(
        settings=mock_settings,
        repo=mock_repository,
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None,
        backend_registry=backend_registry,
    )


def _local_download() -> Download:
    return Download(
        id='dl-1', type=DownloadType.MODEL, filename='model.safetensors',
        url='https://example.com/model.safetensors', destination_path='/models/checkpoints/model.safetensors',
    )


@pytest.mark.asyncio
async def test_local_download_completion_reconciles_native_availability(worker, mock_repository, backend_registry):
    download = _local_download()
    mock_repository.get_by_id.return_value = download

    async def fake_download_file(self, dl):
        return True

    with patch.object(DownloadWorker, "_download_file", fake_download_file), \
         patch(
             "src.features.models.native_availability_reconciler.native_availability_reconciler"
         ) as mock_reconciler:
        mock_reconciler.reconcile = AsyncMock()
        await worker._process_download('dl-1')

    mock_reconciler.reconcile.assert_awaited_once_with(backend_registry)


@pytest.mark.asyncio
async def test_remote_destination_download_does_not_use_the_local_reconciler(worker, mock_repository):
    """A remote-destination download already re-indexes its destination
    backend via `_index_remote_backend` - the local reconciler is for the
    local-disk path only and must not double-run for it."""
    download = _local_download()
    download.destination_backend_id = 'remote-1'
    mock_repository.get_by_id.return_value = download
    worker.backend_registry.get_backend.return_value = None  # keeps _index_remote_backend a no-op

    async def fake_fetch_remote(self, dl):
        return True

    with patch.object(DownloadWorker, "_fetch_remote", fake_fetch_remote), \
         patch(
             "src.features.models.native_availability_reconciler.native_availability_reconciler"
         ) as mock_reconciler:
        mock_reconciler.reconcile = AsyncMock()
        await worker._process_download('dl-1')

    mock_reconciler.reconcile.assert_not_awaited()
