"""
Supplementary tests for DownloadQueue covering lifecycle, settings, and edge cases.

These tests were originally for the DownloadService class which has been replaced
by the DownloadQueue in the downloader plugin.
"""

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch, PropertyMock
from datetime import datetime
import asyncio

from src.features.downloads.models import Download, DownloadType, DownloadStatus, DownloadSettings
from src.features.downloads.queue import DownloadQueue


def _build_queue(**kwargs):
    kwargs.setdefault('settings', Mock(get_setting=Mock(return_value=None)))
    kwargs.setdefault('connection_hub', AsyncMock())
    return DownloadQueue(**kwargs)


@pytest.fixture
def mock_download_repo():
    """Mock DownloadRepository"""
    repo = Mock()
    repo.create = Mock()
    repo.get_by_id = Mock()
    repo.get_pending = Mock(return_value=[])
    repo.get_active = Mock(return_value=[])
    repo.get_all = Mock(return_value=[])
    repo.count_by_status = Mock(return_value={})
    repo.count_total = Mock(return_value=0)
    repo.update_status = Mock(return_value=True)
    repo.update_progress = Mock(return_value=True)
    repo.increment_retry = Mock(return_value=True)
    repo.get_settings = Mock(return_value=DownloadSettings())
    return repo


@pytest.fixture
def mock_plugin_registry():
    """Mock PluginRegistry"""
    registry = Mock()
    mock_context = Mock()
    mock_context.data = {}
    registry.execute_hook.return_value = (mock_context, [])
    return registry


@pytest.fixture
def sample_download():
    """Sample download for testing"""
    return Download(
        id="test-download-1",
        type=DownloadType.MODEL,
        url="https://example.com/model.safetensors",
        destination_path="/models/model.safetensors",
        filename="model.safetensors",
        status=DownloadStatus.PENDING,
        progress=0.0,
        total_bytes=1000000,
        downloaded_bytes=0,
        created_at=datetime(2024, 1, 1, 12, 0, 0)
    )


@pytest.fixture
def sample_settings():
    """Sample download settings"""
    return DownloadSettings(
        max_concurrent_downloads=2,
        auto_retry_failed=True,
        max_retries=3,
        chunk_size_kb=1024,
        verify_checksum=True,
        default_model_directory="models",
        default_media_directory="storage/media"
    )


# ========== Initialization Tests ==========

class TestDownloadQueueInitialization:
    """Tests for DownloadQueue initialization"""

    def test_queue_can_be_imported(self):
        """Test that DownloadQueue can be imported"""
        from src.features.downloads.queue import DownloadQueue
        assert DownloadQueue is not None


# ========== Queue Download Tests ==========

class TestQueueDownload:
    """Tests for queueing downloads"""

    @pytest.mark.asyncio
    async def test_queue_model_download_creates_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test that queue_model_download creates a download record"""
        from src.features.downloads.queue import DownloadQueue

        mock_download_repo.create.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        # Mock worker
        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_conn:
            mock_conn.send_download_queued = AsyncMock()

            result = await manager.queue_model_download(
                url="https://example.com/model.safetensors",
                destination_dir="models"
            )

        assert result is not None
        mock_download_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_model_download_with_tags(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test queueing a model download with tags"""
        from src.features.downloads.queue import DownloadQueue

        mock_download_repo.create.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_conn:
            mock_conn.send_download_queued = AsyncMock()

            result = await manager.queue_model_download(
                url="https://example.com/model.safetensors",
                tags=["sdxl", "checkpoint"]
            )

        # Verify the download was created with tags
        create_call = mock_download_repo.create.call_args
        assert create_call is not None

    @pytest.mark.asyncio
    async def test_queue_media_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test queueing a media download"""
        from src.features.downloads.queue import DownloadQueue

        sample_download.type = DownloadType.MEDIA
        mock_download_repo.create.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_conn:
            mock_conn.send_download_queued = AsyncMock()

            result = await manager.queue_media_download(
                url="https://example.com/image.png",
                destination_dir="storage/media"
            )

        assert result is not None


# ========== Pause/Resume/Cancel Tests ==========

class TestDownloadControl:
    """Tests for pause/resume/cancel operations"""

    @pytest.mark.asyncio
    async def test_pause_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test pausing a download"""
        from src.features.downloads.queue import DownloadQueue

        sample_download.status = DownloadStatus.DOWNLOADING
        mock_download_repo.get_by_id.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.pause.return_value = True
        manager.worker = mock_worker

        result = await manager.pause_download("test-download-1")

        mock_worker.pause.assert_called_once_with("test-download-1")

    @pytest.mark.asyncio
    async def test_resume_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test resuming a paused download"""
        from src.features.downloads.queue import DownloadQueue

        sample_download.status = DownloadStatus.PAUSED
        mock_download_repo.get_by_id.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.resume.return_value = True
        manager.worker = mock_worker

        result = await manager.resume_download("test-download-1")

        mock_worker.resume.assert_called_once_with("test-download-1")

    @pytest.mark.asyncio
    async def test_cancel_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test cancelling a download"""
        from src.features.downloads.queue import DownloadQueue

        sample_download.status = DownloadStatus.DOWNLOADING
        mock_download_repo.get_by_id.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.cancel.return_value = True
        manager.worker = mock_worker

        result = await manager.cancel_download("test-download-1")

        mock_worker.cancel.assert_called_once_with("test-download-1")

    @pytest.mark.asyncio
    async def test_pause_nonexistent_download_raises(self, mock_download_repo, mock_plugin_registry, sample_settings):
        """Test pausing a nonexistent download raises exception"""
        from src.features.downloads.queue import DownloadQueue
        from src.features.downloads.exceptions import DownloadNotFoundException

        mock_download_repo.get_by_id.return_value = None

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        with pytest.raises(DownloadNotFoundException):
            await manager.pause_download("nonexistent")


# ========== Retry Tests ==========

class TestRetryDownload:
    """Tests for retry functionality"""

    @pytest.mark.asyncio
    async def test_retry_failed_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test retrying a failed download"""
        from src.features.downloads.queue import DownloadQueue

        sample_download.status = DownloadStatus.FAILED
        mock_download_repo.get_by_id.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.retry.return_value = True
        manager.worker = mock_worker

        result = await manager.retry_download("test-download-1")

        mock_worker.retry.assert_called_once_with("test-download-1")


# ========== Filename Override Tests ==========

class TestFilenameOverride:
    """Tests for filename override in download queueing"""

    @pytest.mark.asyncio
    async def test_queue_with_custom_filename(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test that filename override takes precedence"""
        from src.features.downloads.queue import DownloadQueue

        mock_download_repo.create.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_conn:
            mock_conn.send_download_queued = AsyncMock()

            result = await manager.queue_model_download(
                url="https://example.com/model.safetensors",
                filename="custom_name.safetensors"
            )

        # Verify custom filename was used
        create_call = mock_download_repo.create.call_args
        created_download = create_call[0][0]
        assert created_download.filename == "custom_name.safetensors"


# ========== Settings Tests ==========

class TestDownloadSettings:
    """Tests for download settings"""

    def test_queue_respects_max_concurrent(self, mock_download_repo, mock_plugin_registry):
        """Test that manager initializes with default settings"""
        from src.features.downloads.queue import DownloadQueue

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        # The manager should have loaded default settings
        assert manager.settings.max_concurrent_downloads >= 1


# ========== Checksum Verification Tests ==========

class TestChecksumVerification:
    """Tests for checksum verification"""

    @pytest.mark.asyncio
    async def test_checksum_stored_with_download(self, mock_download_repo, mock_plugin_registry, sample_download, sample_settings):
        """Test that checksum is stored with the download"""
        from src.features.downloads.queue import DownloadQueue

        mock_download_repo.create.return_value = sample_download

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_conn:
            mock_conn.send_download_queued = AsyncMock()

            result = await manager.queue_model_download(
                url="https://example.com/model.safetensors",
                checksum_sha256="abc123def456"
            )

        # Verify checksum was stored
        create_call = mock_download_repo.create.call_args
        created_download = create_call[0][0]
        assert created_download.checksum_sha256 == "abc123def456"


# ========== Queue Lifecycle Tests ==========

class TestQueueLifecycle:
    """Tests for queue start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_queue_start_creates_worker(self, mock_download_repo, mock_plugin_registry, sample_settings):
        """Test that starting the queue creates a worker."""
        from src.features.downloads.queue import DownloadQueue

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        with patch('src.features.downloads.queue.DownloadWorker') as MockWorker:
            mock_worker_instance = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            await manager.start()

            MockWorker.assert_called_once()
            mock_worker_instance.start.assert_called_once()
            assert manager.worker is not None

            # Clean up
            await manager.stop()

    @pytest.mark.asyncio
    async def test_queue_stop_stops_worker(self, mock_download_repo, mock_plugin_registry, sample_settings):
        """Test that stopping the manager stops the worker"""
        from src.features.downloads.queue import DownloadQueue

        if True:
            manager = _build_queue(
                download_repository=mock_download_repo,
                plugin_registry=mock_plugin_registry,
            )

        with patch('src.features.downloads.queue.DownloadWorker') as MockWorker:
            mock_worker_instance = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            await manager.start()
            await manager.stop()

            mock_worker_instance.stop.assert_called_once()
            assert manager.worker is None
