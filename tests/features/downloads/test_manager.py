"""
Tests for DownloadManager business logic.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from src.features.downloads.manager import DownloadManager
from src.features.downloads.exceptions import (
    DownloadNotFoundException,
    DownloadQueueException,
    DownloadOperationException,
    InvalidStatusException,
    InvalidTypeException,
)
from src.features.downloads.models import Download, DownloadStatus, DownloadType, DownloadSettings


@pytest.fixture
def mock_repository():
    """Create a mock download repository."""
    repo = Mock()
    repo.get_all.return_value = []
    repo.count_by_status.return_value = {}
    repo.count_total.return_value = 0
    return repo


@pytest.fixture
def mock_plugin_registry():
    """Create a mock plugin registry."""
    registry = Mock()
    # Default: no hooks block
    mock_context = Mock()
    mock_context.data = {}
    registry.execute_hook.return_value = (mock_context, [])
    return registry


def _settings_manager(models_dir=None, file_storage=None):
    sm = Mock()
    sm.get_setting.side_effect = lambda key, default=None: {
        'models_dir': models_dir,
        'file_storage_directory': file_storage,
    }.get(key, default)
    return sm


@pytest.fixture
def manager(mock_repository, mock_plugin_registry):
    """Create a DownloadManager instance with mocked dependencies."""
    return DownloadManager(
        download_repository=mock_repository,
        plugin_registry=mock_plugin_registry,
        settings_manager=_settings_manager(),
        connection_manager=AsyncMock(),
    )


class TestListDownloads:
    """Tests for list_downloads method."""

    def test_list_downloads_empty(self, manager, mock_repository):
        """Test listing downloads when none exist."""
        result = manager.list_downloads()

        assert result['downloads'] == []
        assert result['total'] == 0
        mock_repository.get_all.assert_called_once()

    def test_list_downloads_with_results(self, manager, mock_repository):
        """Test listing downloads returns proper data."""
        mock_download = Mock()
        mock_download.to_dict.return_value = {'id': 'test-id', 'filename': 'test.safetensors'}
        mock_repository.get_all.return_value = [mock_download]
        mock_repository.count_by_status.return_value = {'pending': 1}
        mock_repository.count_total.return_value = 1

        result = manager.list_downloads()

        assert len(result['downloads']) == 1
        assert result['downloads'][0]['id'] == 'test-id'
        assert result['counts'] == {'pending': 1}
        assert result['total'] == 1

    def test_list_downloads_with_status_filter(self, manager, mock_repository):
        """Test listing downloads with status filter."""
        manager.list_downloads(status='pending')

        mock_repository.get_all.assert_called_once()
        call_kwargs = mock_repository.get_all.call_args[1]
        assert call_kwargs['status'] == DownloadStatus.PENDING

    def test_list_downloads_with_type_filter(self, manager, mock_repository):
        """Test listing downloads with type filter."""
        manager.list_downloads(download_type='model')

        mock_repository.get_all.assert_called_once()
        call_kwargs = mock_repository.get_all.call_args[1]
        assert call_kwargs['download_type'] == DownloadType.MODEL

    def test_list_downloads_invalid_status(self, manager):
        """Test listing downloads with invalid status raises exception."""
        with pytest.raises(InvalidStatusException):
            manager.list_downloads(status='invalid_status')

    def test_list_downloads_invalid_type(self, manager):
        """Test listing downloads with invalid type raises exception."""
        with pytest.raises(InvalidTypeException):
            manager.list_downloads(download_type='invalid_type')


class TestGetDownload:
    """Tests for get_download method."""

    def test_get_download_found(self, manager, mock_repository):
        """Test getting a download that exists."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_repository.get_by_id.return_value = mock_download

        result = manager.get_download('test-id')

        assert result == mock_download
        mock_repository.get_by_id.assert_called_once_with('test-id')

    def test_get_download_not_found(self, manager, mock_repository):
        """Test getting a download that doesn't exist."""
        mock_repository.get_by_id.return_value = None

        with pytest.raises(DownloadNotFoundException):
            manager.get_download('nonexistent-id')


class TestFindActiveDownloadForRepo:
    """Tests for find_active_download_for_repo - the status-endpoint seam
    that lets a reloading admin client reconstruct "a fetch is already
    running" without any page-local downloadId->asset bookkeeping."""

    def test_delegates_to_repository_and_returns_its_result(self, manager, mock_repository):
        mock_download = Mock()
        mock_repository.find_active_by_repo_id.return_value = mock_download

        result = manager.find_active_download_for_repo('BAAI/bge-small-en-v1.5')

        assert result == mock_download
        mock_repository.find_active_by_repo_id.assert_called_once_with('BAAI/bge-small-en-v1.5')

    def test_returns_none_when_no_active_job(self, manager, mock_repository):
        mock_repository.find_active_by_repo_id.return_value = None

        assert manager.find_active_download_for_repo('BAAI/bge-small-en-v1.5') is None


class TestQueueModelDownload:
    """Tests for queue_model_download method."""

    @pytest.mark.asyncio
    async def test_queue_model_download_success(self, manager, mock_repository, mock_plugin_registry):
        """Test queueing a model download successfully."""
        mock_download = Mock()
        mock_download.id = 'new-download-id'
        mock_download.filename = 'model.safetensors'
        mock_download.to_dict.return_value = {'id': 'new-download-id'}
        mock_repository.create.return_value = mock_download

        # Mock worker
        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_admin:
            mock_admin.send_download_queued = AsyncMock()

            result = await manager.queue_model_download(
                url='https://example.com/model.safetensors',
                filename='model.safetensors',
                created_by='user-123'
            )

        assert result == mock_download
        mock_repository.create.assert_called_once()
        mock_worker.enqueue.assert_called_once_with('new-download-id')

    @pytest.mark.asyncio
    async def test_queue_model_download_hook_blocks(self, manager, mock_repository, mock_plugin_registry):
        """Test that a blocking hook prevents queueing."""
        mock_context = Mock()
        mock_context.data = {'blocked': True, 'block_reason': 'Test block'}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(DownloadQueueException) as exc_info:
            await manager.queue_model_download(
                url='https://example.com/model.safetensors'
            )

        assert 'Test block' in str(exc_info.value)


class TestQueueModelDownloadDestinationResolution:
    """A queued model download must land inside the *configured* model
    depot, never the process CWD (the checkout, when the app runs via
    `python api.py`) and never outside the depot at all.

    The bug this guards: `ModelCatalog.get_model_types()` used to hand the
    frontend a hardcoded relative string like ``'models/checkpoints'`` (see
    `src/features/models/catalog.py`), which `AddDownloadModal.svelte` echoed
    straight back as `destination_dir`. The old `queue_model_download` used
    that string as-is via `os.path.join(destination_dir, filename)` - which
    resolves relative to the CWD, not the admin-configured depot - so any
    non-default depot silently downloaded into the checkout instead.
    """

    @pytest.fixture
    def manager_with_depot(self, mock_repository, mock_plugin_registry, tmp_path):
        """A DownloadManager pointed at a depot that is NOT the CWD, so a
        regression back to CWD-relative resolution is caught by path identity,
        not by coincidence (the default `default_model_directory` is the
        literal string "models", which - run from the repo root - happens to
        collide with the CWD-relative interpretation of the old bug)."""
        depot = tmp_path / "custom-depot"
        manager = DownloadManager(
            download_repository=mock_repository,
            plugin_registry=mock_plugin_registry,
            settings_manager=_settings_manager(models_dir=str(depot)),
            connection_manager=AsyncMock(),
        )
        manager.worker = AsyncMock()
        manager.worker.get_queue_position.return_value = 0
        mock_repository.create.side_effect = lambda d: d
        return manager, depot

    async def _queue(self, manager, **kwargs):
        with patch.object(manager, 'conn', AsyncMock()):
            return await manager.queue_model_download(
                url='https://example.com/model.safetensors',
                filename='model.safetensors',
                **kwargs,
            )

    @pytest.mark.asyncio
    async def test_legacy_relative_destination_dir_lands_inside_custom_depot(
        self, manager_with_depot
    ):
        """The exact shape the (fixed) frontend used to send: a relative
        'models/checkpoints' string. It must resolve inside the configured
        depot, not the process CWD."""
        manager, depot = manager_with_depot
        result = await self._queue(manager, destination_dir='models/checkpoints')

        resolved = Path(result.destination_path).resolve()
        assert depot.resolve() in resolved.parents
        # And specifically not the checkout's CWD-relative "models/checkpoints" -
        # the exact path the pre-fix bug wrote into instead of the depot.
        cwd_relative = (Path.cwd() / 'models' / 'checkpoints').resolve()
        assert resolved.parent != cwd_relative

    @pytest.mark.asyncio
    async def test_model_type_resolves_via_type_dir_map(self, manager_with_depot):
        """The new, authoritative resolution path: a `model_type` maps through
        `TYPE_DIR_MAP` to the depot subdir the indexer actually scans."""
        manager, depot = manager_with_depot
        result = await self._queue(manager, model_type='checkpoint')

        assert Path(result.destination_path).resolve() == (
            depot / 'checkpoints' / 'model.safetensors'
        ).resolve()

    @pytest.mark.asyncio
    async def test_relative_traversal_escape_is_rejected(self, manager_with_depot):
        manager, _depot = manager_with_depot
        with pytest.raises(DownloadQueueException):
            await self._queue(manager, destination_dir='../../etc')

    @pytest.mark.asyncio
    async def test_absolute_path_outside_depot_is_rejected(self, manager_with_depot):
        manager, _depot = manager_with_depot
        with pytest.raises(DownloadQueueException):
            await self._queue(manager, destination_dir='/etc/cron.d')

    @pytest.mark.asyncio
    async def test_absolute_path_already_inside_depot_is_accepted(self, manager_with_depot):
        """The one caller that already computed a correct, absolute,
        depot-rooted path server-side (`queue_recommendation_download` before
        it was simplified to pass `model_type` instead) must keep working."""
        manager, depot = manager_with_depot
        already_resolved = str(depot / 'checkpoints')

        result = await self._queue(manager, destination_dir=already_resolved)

        assert Path(result.destination_path).resolve() == (
            depot / 'checkpoints' / 'model.safetensors'
        ).resolve()

    @pytest.mark.asyncio
    async def test_no_destination_given_falls_back_to_depot_root(self, manager_with_depot):
        manager, depot = manager_with_depot
        result = await self._queue(manager)

        assert Path(result.destination_path).resolve() == (
            depot / 'model.safetensors'
        ).resolve()


class TestQueueMediaDownload:
    """Tests for queue_media_download method."""

    @pytest.mark.asyncio
    async def test_queue_media_download_success(self, manager, mock_repository, mock_plugin_registry):
        """Test queueing a media download successfully."""
        mock_download = Mock()
        mock_download.id = 'new-download-id'
        mock_download.filename = 'image.png'
        mock_download.to_dict.return_value = {'id': 'new-download-id'}
        mock_repository.create.return_value = mock_download

        # Mock worker
        mock_worker = AsyncMock()
        mock_worker.get_queue_position.return_value = 0
        manager.worker = mock_worker

        with patch.object(manager, 'conn', AsyncMock()) as mock_admin:
            mock_admin.send_download_queued = AsyncMock()

            result = await manager.queue_media_download(
                url='https://example.com/image.png'
            )

        assert result == mock_download
        mock_repository.create.assert_called_once()


class TestPauseDownload:
    """Tests for pause_download method."""

    @pytest.mark.asyncio
    async def test_pause_download_success(self, manager, mock_repository, mock_plugin_registry):
        """Test pausing a download successfully."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_repository.get_by_id.return_value = mock_download

        mock_worker = AsyncMock()
        mock_worker.pause.return_value = True
        manager.worker = mock_worker

        result = await manager.pause_download('test-id')

        mock_worker.pause.assert_called_once_with('test-id')

    @pytest.mark.asyncio
    async def test_pause_download_not_found(self, manager, mock_repository):
        """Test pausing a download that doesn't exist."""
        mock_repository.get_by_id.return_value = None

        with pytest.raises(DownloadNotFoundException):
            await manager.pause_download('nonexistent-id')

    @pytest.mark.asyncio
    async def test_pause_download_fails(self, manager, mock_repository):
        """Test pausing a download that cannot be paused."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_repository.get_by_id.return_value = mock_download

        mock_worker = AsyncMock()
        mock_worker.pause.return_value = False
        manager.worker = mock_worker

        with pytest.raises(DownloadOperationException):
            await manager.pause_download('test-id')


class TestResumeDownload:
    """Tests for resume_download method."""

    @pytest.mark.asyncio
    async def test_resume_download_success(self, manager, mock_repository, mock_plugin_registry):
        """Test resuming a download successfully."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_repository.get_by_id.return_value = mock_download

        mock_worker = AsyncMock()
        mock_worker.resume.return_value = True
        manager.worker = mock_worker

        result = await manager.resume_download('test-id')

        mock_worker.resume.assert_called_once_with('test-id')


class TestCancelDownload:
    """Tests for cancel_download method."""

    @pytest.mark.asyncio
    async def test_cancel_download_success(self, manager, mock_repository, mock_plugin_registry):
        """Test cancelling a download successfully."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_repository.get_by_id.return_value = mock_download

        mock_worker = AsyncMock()
        mock_worker.cancel.return_value = True
        manager.worker = mock_worker

        result = await manager.cancel_download('test-id')

        mock_worker.cancel.assert_called_once_with('test-id')


class TestDeleteDownload:
    """Tests for delete_download method."""

    @pytest.mark.asyncio
    async def test_delete_download_success(self, manager, mock_repository, mock_plugin_registry):
        """Test deleting a download successfully."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_download.status = DownloadStatus.COMPLETED
        mock_repository.get_by_id.return_value = mock_download

        await manager.delete_download('test-id')

        mock_repository.delete.assert_called_once_with('test-id')

    @pytest.mark.asyncio
    async def test_delete_download_cancels_active(self, manager, mock_repository, mock_plugin_registry):
        """Test deleting an active download cancels it first."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_download.status = DownloadStatus.DOWNLOADING
        mock_repository.get_by_id.return_value = mock_download

        mock_worker = AsyncMock()
        manager.worker = mock_worker

        await manager.delete_download('test-id')

        mock_worker.cancel.assert_called_once_with('test-id')
        mock_repository.delete.assert_called_once()


class TestClearCompleted:
    """Tests for clear_completed method."""

    def test_clear_completed(self, manager, mock_repository, mock_plugin_registry):
        """Test clearing completed downloads."""
        mock_repository.delete_completed.return_value = 5

        count = manager.clear_completed()

        assert count == 5
        mock_repository.delete_completed.assert_called_once()


class TestClearCancelled:
    """Tests for clear_cancelled method."""

    def test_clear_cancelled(self, manager, mock_repository, mock_plugin_registry):
        """Test clearing cancelled downloads."""
        mock_repository.delete_cancelled.return_value = 3

        count = manager.clear_cancelled()

        assert count == 3
        mock_repository.delete_cancelled.assert_called_once()


class TestSettings:
    """Tests for settings operations."""

    def test_get_settings(self, manager):
        """Test getting settings."""
        settings = manager.get_settings()

        assert isinstance(settings, DownloadSettings)
        assert settings.max_concurrent_downloads >= 1

    def test_update_settings(self, manager):
        """Test updating settings."""
        new_settings = DownloadSettings(
            max_concurrent_downloads=5,
            auto_retry_failed=False
        )

        result = manager.update_settings(new_settings)

        assert result.max_concurrent_downloads == 5
        assert result.auto_retry_failed is False


class TestLoadSettings:
    """The download destination comes from the `models_dir` setting."""

    def _manager_with_setting(self, mock_repository, mock_plugin_registry, value):
        sm = _settings_manager(models_dir=value)
        mgr = DownloadManager(
            download_repository=mock_repository,
            plugin_registry=mock_plugin_registry,
            settings_manager=sm,
            connection_manager=AsyncMock(),
        )
        return mgr, sm

    def test_reads_the_models_dir_setting_key(self, mock_repository, mock_plugin_registry):
        """`model_dir` (singular) is not a registered key — reading it silently
        discards the admin's configured directory."""
        mgr, sm = self._manager_with_setting(
            mock_repository, mock_plugin_registry, "/srv/weights"
        )

        sm.get_setting.assert_any_call('models_dir')
        assert mgr.settings.default_model_directory == "/srv/weights"

    def test_normalises_leading_dot_slash(self, mock_repository, mock_plugin_registry):
        """The stored value is './models'; os.path.join would carry the './' into
        every persisted destination_path.

        Uses a non-default directory so this cannot pass by falling back to the
        `models` default when the setting key is wrong.
        """
        mgr, _ = self._manager_with_setting(
            mock_repository, mock_plugin_registry, "./srv/weights"
        )

        assert mgr.settings.default_model_directory == "srv/weights"
