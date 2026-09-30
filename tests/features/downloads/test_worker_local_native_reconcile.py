
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
             "src.features.models.native_availability_reconciler.native_availability_projector"
         ) as mock_reconciler:
        mock_reconciler.reconcile = AsyncMock()
        await worker._process_download('dl-1')

    mock_reconciler.reconcile.assert_awaited_once_with(backend_registry)

@pytest.mark.asyncio
async def test_remote_destination_download_does_not_use_the_local_reconciler(worker, mock_repository):
    download = _local_download()
    download.destination_backend_id = 'remote-1'
    mock_repository.get_by_id.return_value = download
    worker.backend_registry.get_backend.return_value = None  # keeps _index_remote_backend a no-op

    async def fake_fetch_remote(self, dl):
        return True

    with patch.object(DownloadWorker, "_fetch_remote", fake_fetch_remote), \
         patch(
             "src.features.models.native_availability_reconciler.native_availability_projector"
         ) as mock_reconciler:
        mock_reconciler.reconcile = AsyncMock()
        await worker._process_download('dl-1')

    mock_reconciler.reconcile.assert_not_awaited()

@pytest.mark.asyncio
async def test_local_model_download_completion_indexes_the_downloaded_file(mock_settings, mock_repository, backend_registry):
    download = _local_download()
    mock_repository.get_by_id.return_value = download
    indexer = Mock(return_value={"indexed": True, "model_id": "m1", "duplicate_of": None})
    worker = DownloadWorker(
        settings=mock_settings, repo=mock_repository, connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None, backend_registry=backend_registry,
        local_model_indexer=indexer,
    )

    async def fake_download_file(self, dl):
        return True

    with patch.object(DownloadWorker, "_download_file", fake_download_file):
        await worker._process_download('dl-1')

    indexer.assert_called_once_with(download.destination_path)

@pytest.mark.asyncio
async def test_indexer_failure_leaves_the_download_completed(mock_settings, mock_repository, backend_registry):
    download = _local_download()
    mock_repository.get_by_id.return_value = download
    indexer = Mock(side_effect=RuntimeError("outside every model root"))
    worker = DownloadWorker(
        settings=mock_settings, repo=mock_repository, connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None, backend_registry=backend_registry,
        local_model_indexer=indexer,
    )

    async def fake_download_file(self, dl):
        return True

    with patch.object(DownloadWorker, "_download_file", fake_download_file):
        await worker._process_download('dl-1')

    mock_repository.update_status.assert_any_call('dl-1', DownloadStatus.COMPLETED)
    assert not any(c.args[1] == DownloadStatus.FAILED for c in mock_repository.update_status.call_args_list)

@pytest.mark.asyncio
async def test_hf_repo_child_download_is_not_indexed_as_a_model(mock_settings, mock_repository, backend_registry):
    download = _local_download()
    download.group_id = 'parent-1'
    mock_repository.get_by_id.return_value = download
    indexer = Mock()
    worker = DownloadWorker(
        settings=mock_settings, repo=mock_repository, connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None, backend_registry=backend_registry,
        local_model_indexer=indexer,
    )

    async def fake_download_file(self, dl):
        return True

    with patch.object(DownloadWorker, "_download_file", fake_download_file), \
         patch("src.features.models.native_availability_reconciler.native_availability_projector") as rec:
        rec.reconcile = AsyncMock()
        await worker._process_download('dl-1')

    indexer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("warning", "expected"),
    [
        ("header unreadable", [("dl-1", DownloadStatus.COMPLETED), ("dl-1", DownloadStatus.COMPLETED, "header unreadable")]),
        (None, [("dl-1", DownloadStatus.COMPLETED)]),
    ],
)
async def test_the_completed_record_carries_the_classification_warning_only_when_there_is_one(
    mock_settings, mock_repository, backend_registry, warning, expected
):
    download = _local_download()
    mock_repository.get_by_id.return_value = download
    indexer = Mock(return_value={"indexed": True, "model_id": "m1", "duplicate_of": None, "warning": warning})
    worker = DownloadWorker(
        settings=mock_settings, repo=mock_repository, connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None, backend_registry=backend_registry,
        local_model_indexer=indexer,
    )

    async def fake_download_file(self, dl):
        return True

    with patch.object(DownloadWorker, "_download_file", fake_download_file):
        await worker._process_download('dl-1')

    completed = [c.args for c in mock_repository.update_status.call_args_list if c.args[1] == DownloadStatus.COMPLETED]
    assert completed == expected
    assert not any(c.args[1] == DownloadStatus.FAILED for c in mock_repository.update_status.call_args_list)
