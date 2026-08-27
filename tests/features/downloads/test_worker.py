"""
Tests for DownloadWorker async download handling.
"""

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
import asyncio

from src.features.downloads.exceptions import DownloadAuthenticationException
from src.features.downloads.worker import DownloadWorker
from src.features.downloads.models import Download, DownloadStatus, DownloadType, DownloadSettings


@pytest.fixture
def mock_settings():
    """Create mock download settings."""
    return DownloadSettings(
        max_concurrent_downloads=2,
        auto_retry_failed=True,
        max_retries=3,
        chunk_size_kb=1024
    )


@pytest.fixture
def mock_repository():
    """Create a mock download repository."""
    repo = Mock()
    repo.get_all.return_value = []
    repo.get_active.return_value = []
    repo.get_pending.return_value = []
    return repo


@pytest.fixture
def worker(mock_settings, mock_repository):
    """Create a DownloadWorker instance with mocked dependencies."""
    return DownloadWorker(
        settings=mock_settings,
        repo=mock_repository,
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None,
    )


class TestWorkerLifecycle:
    """Tests for worker start/stop operations."""

    @pytest.mark.asyncio
    async def test_start_creates_workers(self, worker, mock_repository):
        """Test that start creates worker tasks."""
        await worker.start()

        try:
            assert worker.running is True
            assert len(worker.workers) == 2  # max_concurrent_downloads
            assert worker.session is not None
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, worker, mock_repository):
        """Test that stop cleans up resources."""
        await worker.start()
        await worker.stop()

        assert worker.running is False
        assert len(worker.workers) == 0
        assert len(worker.active_downloads) == 0
        assert worker.session is None

    @pytest.mark.asyncio
    async def test_start_twice_warns(self, worker, mock_repository, caplog):
        """Test that starting twice logs a warning."""
        await worker.start()
        try:
            await worker.start()
            assert "already running" in caplog.text
        finally:
            await worker.stop()


class TestQueueOperations:
    """Tests for queue management."""

    @pytest.mark.asyncio
    async def test_enqueue_adds_to_queue(self, worker):
        """Test that enqueue adds download ID to queue."""
        await worker.enqueue('test-download-id')

        assert worker.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_get_queue_position(self, worker):
        """Test getting queue position."""
        await worker.enqueue('download-1')
        await worker.enqueue('download-2')

        assert worker.get_queue_position() == 2


class TestPauseResumeCancelRetry:
    """Tests for pause, resume, cancel, and retry operations."""

    @pytest.mark.asyncio
    async def test_pause_active_download(self, worker, mock_repository):
        """Test pausing an active download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_download.status = DownloadStatus.DOWNLOADING
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.pause('test-id')

        assert result is True
        assert 'test-id' in worker.paused_downloads

    @pytest.mark.asyncio
    async def test_pause_non_downloading(self, worker, mock_repository):
        """Test pausing a non-downloading download returns False."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.status = DownloadStatus.PENDING
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.pause('test-id')

        assert result is False

    @pytest.mark.asyncio
    async def test_pause_not_found(self, worker, mock_repository):
        """Test pausing a non-existent download returns False."""
        mock_repository.get_by_id.return_value = None

        result = await worker.pause('nonexistent')

        assert result is False

    @pytest.mark.asyncio
    async def test_resume_paused_download(self, worker, mock_repository):
        """Test resuming a paused download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_download.status = DownloadStatus.PAUSED
        mock_repository.get_by_id.return_value = mock_download

        worker.paused_downloads.add('test-id')

        result = await worker.resume('test-id')

        assert result is True
        assert 'test-id' not in worker.paused_downloads
        mock_repository.update_status.assert_called_once_with('test-id', DownloadStatus.PENDING)

    @pytest.mark.asyncio
    async def test_resume_failed_download(self, worker, mock_repository):
        """Test resuming a failed download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.status = DownloadStatus.FAILED
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.resume('test-id')

        assert result is True

    @pytest.mark.asyncio
    async def test_resume_non_resumable(self, worker, mock_repository):
        """Test resuming a download that can't be resumed."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.status = DownloadStatus.COMPLETED
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.resume('test-id')

        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_pending_download(self, worker, mock_repository):
        """Test cancelling a pending download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_download.status = DownloadStatus.PENDING
        mock_download.destination_path = '/tmp/test.safetensors'
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.cancel('test-id')

        assert result is True
        assert 'test-id' in worker.cancelled_downloads
        mock_repository.update_status.assert_called_once_with('test-id', DownloadStatus.CANCELLED)

    @pytest.mark.asyncio
    async def test_cancel_already_completed(self, worker, mock_repository):
        """Test cancelling an already completed download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.status = DownloadStatus.COMPLETED
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.cancel('test-id')

        assert result is False

    @pytest.mark.asyncio
    async def test_retry_failed_download(self, worker, mock_repository):
        """Test retrying a failed download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.filename = 'test.safetensors'
        mock_download.status = DownloadStatus.FAILED
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.retry('test-id')

        assert result is True
        mock_repository.update_status.assert_called_once_with('test-id', DownloadStatus.PENDING)

    @pytest.mark.asyncio
    async def test_retry_non_failed(self, worker, mock_repository):
        """Test retrying a non-failed download."""
        mock_download = Mock()
        mock_download.id = 'test-id'
        mock_download.status = DownloadStatus.PENDING
        mock_repository.get_by_id.return_value = mock_download

        result = await worker.retry('test-id')

        assert result is False


class TestResumePending:
    """Tests for resume pending downloads on startup."""

    @pytest.mark.asyncio
    async def test_resume_pending_marks_downloading_as_pending(self, worker, mock_repository):
        """Test that downloads in DOWNLOADING status are reset to PENDING."""
        active_download = Mock()
        active_download.id = 'active-id'
        mock_repository.get_active.return_value = [active_download]
        mock_repository.get_pending.return_value = []

        await worker._resume_pending_downloads()

        mock_repository.update_status.assert_called_once_with('active-id', DownloadStatus.PENDING)

    @pytest.mark.asyncio
    async def test_resume_pending_queues_pending(self, worker, mock_repository):
        """Test that pending downloads are queued."""
        pending_download = Mock()
        pending_download.id = 'pending-id'
        mock_repository.get_active.return_value = []
        mock_repository.get_pending.return_value = [pending_download]

        await worker._resume_pending_downloads()

        assert worker.queue.qsize() == 1


class TestAuthFailureFastPath:
    """401/403 auth failures fail a download immediately instead of burning
    the generic retry budget - the credentials/license won't change between
    attempts, so retrying only delays an actionable message."""

    @pytest.mark.asyncio
    async def test_auth_failure_skips_retry_and_fails_immediately(self, worker, mock_repository):
        download = Download(
            id='dl-1', type=DownloadType.MODEL, filename='tokenizer.json',
            url='https://huggingface.co/google/gemma-3-12b-it/resolve/main/tokenizer.json',
        )
        mock_repository.get_by_id.return_value = download

        async def fake_download_file(self, dl):
            raise DownloadAuthenticationException(
                "Access denied (HTTP 401) fetching 'google/gemma-3-12b-it' from "
                "huggingface.co - this repo is gated or private."
            )

        with patch.object(DownloadWorker, "_download_file", fake_download_file):
            await worker._process_download('dl-1')

        mock_repository.increment_retry.assert_not_called()
        assert worker.queue.qsize() == 0

        failed_calls = [
            c for c in mock_repository.update_status.call_args_list
            if c.args[1] == DownloadStatus.FAILED
        ]
        assert len(failed_calls) == 1
        assert failed_calls[0].args[0] == 'dl-1'
        assert "gated" in failed_calls[0].args[2]

    @pytest.mark.asyncio
    async def test_generic_failure_still_uses_retry_budget(self, worker, mock_repository):
        """Sanity check the pre-existing retry path for non-auth failures is untouched."""
        download = Download(
            id='dl-2', type=DownloadType.MODEL, filename='model.safetensors',
            url='https://example.com/model.safetensors',
        )
        mock_repository.get_by_id.return_value = download
        mock_repository.increment_retry.return_value = 1

        async def fake_download_file(self, dl):
            raise Exception("HTTP 500: Internal Server Error")

        with patch.object(DownloadWorker, "_download_file", fake_download_file):
            await worker._process_download('dl-2')

        mock_repository.increment_retry.assert_called_once_with('dl-2')
        assert worker.queue.qsize() == 1
        failed_calls = [
            c for c in mock_repository.update_status.call_args_list
            if c.args[1] == DownloadStatus.FAILED
        ]
        assert failed_calls == []

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_then_fails(self, worker, mock_repository):
        """Non-auth failures still eventually fail once retries are exhausted."""
        download = Download(
            id='dl-3', type=DownloadType.MODEL, filename='model.safetensors',
            url='https://example.com/model.safetensors',
        )
        mock_repository.get_by_id.return_value = download
        mock_repository.increment_retry.return_value = 4  # > max_retries=3

        async def fake_download_file(self, dl):
            raise Exception("HTTP 500: Internal Server Error")

        with patch.object(DownloadWorker, "_download_file", fake_download_file):
            await worker._process_download('dl-3')

        assert worker.queue.qsize() == 0
        failed_calls = [
            c for c in mock_repository.update_status.call_args_list
            if c.args[1] == DownloadStatus.FAILED
        ]
        assert len(failed_calls) == 1


class TestAuthFailureMessage:
    """`_auth_failure_message` / `_repo_label` build an actionable, provider-
    named message without core hardcoding a provider identifier."""

    def test_names_repo_and_provider_for_a_direct_download(self, worker, mock_repository):
        download = Download(
            filename='model.safetensors', repo_id=None,
            url='https://huggingface.co/org/gated-model/resolve/main/model.safetensors',
        )
        provider = Mock()
        metadata = Mock()
        metadata.name = 'HuggingFace'
        provider.get_metadata.return_value = metadata

        message = worker._auth_failure_message(download, 401, provider)

        assert "401" in message
        assert "huggingface.co" in message
        assert "gated" in message
        assert "Admin -> Plugins" in message

    def test_uses_group_parent_repo_id_for_a_grouped_child(self, worker, mock_repository):
        parent = Download(id='parent-1', type=DownloadType.HF_REPO, repo_id='google/gemma-3-12b-it')
        child = Download(
            filename='tokenizer.json', group_id='parent-1',
            url='https://huggingface.co/google/gemma-3-12b-it/resolve/main/tokenizer.json',
        )
        mock_repository.get_by_id.return_value = parent

        label = worker._repo_label(child)

        assert label == 'google/gemma-3-12b-it'

    def test_falls_back_to_filename_without_a_provider(self, worker, mock_repository):
        download = Download(
            filename='weights.safetensors', url='https://example.com/weights.safetensors',
        )

        message = worker._auth_failure_message(download, 403, None)

        assert "weights.safetensors" in message
        assert "example.com" in message


class TestDownloadFileAuthStatus:
    """`_download_file` raises `DownloadAuthenticationException` (not a bare
    `Exception`) on 401/403 so the retry loop can fail fast."""

    class _FakeResponse:
        def __init__(self, status):
            self.status = status
            self.reason = "Unauthorized" if status == 401 else "Forbidden"
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @pytest.mark.asyncio
    async def test_401_raises_authentication_exception(self, worker, tmp_path):
        download = Download(
            filename='tokenizer.json',
            url='https://huggingface.co/google/gemma-3-12b-it/resolve/main/tokenizer.json',
            destination_path=str(tmp_path / 'tokenizer.json'),
        )

        response = self._FakeResponse(401)

        class _Session:
            def get(self, url, headers=None):
                return response

        worker.session = _Session()

        with pytest.raises(DownloadAuthenticationException) as exc_info:
            await worker._download_file(download)

        assert "401" in str(exc_info.value)
        assert "huggingface.co" in str(exc_info.value)
        assert "tokenizer.json" in str(exc_info.value)
