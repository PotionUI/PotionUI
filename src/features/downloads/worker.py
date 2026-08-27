"""Download worker for async file downloads.

Handles the actual downloading of files with:
- Async download queue with configurable concurrent workers
- Progress tracking with speed calculation
- Pause/Resume/Cancel functionality
- SHA256 checksum verification
- Auto-retry on failure
- WebSocket progress broadcasting
- Grouped-download (hf_repo) aggregate refresh

Provider authentication is resolved through the marketplace-provider seam
(`MarketplaceProviderBase.prepare_download` / `matches_download_url`) - core
never names a concrete provider.
"""

import asyncio
import aiohttp
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Dict, List
from urllib.parse import urlparse

from src.features.downloads.exceptions import DownloadAuthenticationException
from src.features.downloads.models import Download, DownloadStatus, DownloadType, DownloadSettings
from src.features.downloads.repository import DownloadRepository
from src.features.downloads.utils import extract_filename_from_url
from src.platform.websocket.download_connection_hub import DownloadConnectionHub

logger = logging.getLogger(__name__)

_GROUP_STATUS_EVENTS = {
    DownloadStatus.DOWNLOADING: 'started',
    DownloadStatus.COMPLETED: 'completed',
    DownloadStatus.FAILED: 'failed',
    DownloadStatus.CANCELLED: 'cancelled',
    DownloadStatus.PAUSED: 'paused',
}


def _default_provider_registry():
    from src.features.providers.registry import get_provider_registry

    return get_provider_registry()


class DownloadWorker:
    """
    Handles async download operations with worker queue.

    Features:
    - Multiple concurrent download workers
    - Progress tracking with speed calculation
    - Pause/Resume/Cancel functionality
    - Provider authentication via the provider seam
    - Checksum verification
    - Auto-retry on failure
    """

    def __init__(
        self,
        settings: DownloadSettings,
        repo: DownloadRepository,
        connection_hub: DownloadConnectionHub,
        provider_registry_factory: Optional[Callable] = None,
    ):
        """Initialize download worker.

        Args:
            settings: Download service settings
            repo: Download repository for data access
            connection_hub: WebSocket broadcast surface for progress/status
            provider_registry_factory: Returns the ProviderRegistry; injectable
                for tests, defaults to the module-level registry (resolved lazily)
        """
        self.settings = settings
        self.repo = repo
        self.conn = connection_hub
        self._provider_registry_factory = provider_registry_factory or _default_provider_registry

        # Worker management
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers: List[asyncio.Task] = []
        self.active_downloads: Dict[str, asyncio.Task] = {}
        self.paused_downloads: set = set()
        self.cancelled_downloads: set = set()

        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None

        # State
        self.running = False

    # ========== Lifecycle ==========

    async def start(self) -> None:
        """Start the download worker and processing queue."""
        if self.running:
            logger.warning("Download worker is already running")
            return

        self.running = True

        # Create HTTP session with reasonable timeouts
        timeout = aiohttp.ClientTimeout(total=3600, connect=30)  # 1 hour total, 30s connect
        self.session = aiohttp.ClientSession(timeout=timeout)

        # Start workers
        for i in range(self.settings.max_concurrent_downloads):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)

        # Resume any downloads that were in progress
        await self._resume_pending_downloads()

        logger.info(f"Download worker started with {self.settings.max_concurrent_downloads} workers")

    async def stop(self) -> None:
        """Stop the download worker gracefully."""
        self.running = False

        # Cancel all active downloads
        for download_id, task in self.active_downloads.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Cancel workers
        for worker in self.workers:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        self.workers.clear()
        self.active_downloads.clear()

        # Close HTTP session
        if self.session:
            await self.session.close()
            self.session = None

        logger.info("Download worker stopped")

    # ========== Queue Management ==========

    async def enqueue(self, download_id: str) -> None:
        """Add a download to the processing queue.

        Args:
            download_id: ID of the download to queue
        """
        await self.queue.put(download_id)

    async def pause(self, download_id: str) -> bool:
        """Pause an active download.

        Args:
            download_id: ID of the download to pause

        Returns:
            True if download was paused, False otherwise
        """
        download = self.repo.get_by_id(download_id)
        if not download:
            return False

        if download.status != DownloadStatus.DOWNLOADING:
            return False

        # Mark as paused
        self.paused_downloads.add(download_id)

        # Cancel the active task if running
        if download_id in self.active_downloads:
            self.active_downloads[download_id].cancel()

        logger.info(f"Paused download: {download.filename}")
        return True

    async def resume(self, download_id: str) -> bool:
        """Resume a paused download.

        Args:
            download_id: ID of the download to resume

        Returns:
            True if download was resumed, False otherwise
        """
        download = self.repo.get_by_id(download_id)
        if not download:
            return False

        if download.status not in (DownloadStatus.PAUSED, DownloadStatus.FAILED):
            return False

        # Remove from paused set
        self.paused_downloads.discard(download_id)

        # Reset status to pending
        self.repo.update_status(download_id, DownloadStatus.PENDING)

        # Re-queue
        await self.queue.put(download_id)

        logger.info(f"Resumed download: {download.filename}")
        return True

    async def cancel(self, download_id: str) -> bool:
        """Cancel an active or pending download.

        Args:
            download_id: ID of the download to cancel

        Returns:
            True if download was cancelled, False otherwise
        """
        download = self.repo.get_by_id(download_id)
        if not download:
            return False

        if download.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED):
            return False

        # Mark as cancelled
        self.cancelled_downloads.add(download_id)
        self.paused_downloads.discard(download_id)

        # Cancel active task if running
        if download_id in self.active_downloads:
            self.active_downloads[download_id].cancel()
        else:
            # Not currently running, just update status
            self.repo.update_status(download_id, DownloadStatus.CANCELLED)

        # Delete partial file if exists
        temp_path = Path(download.destination_path + '.part')
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete partial file: {e}")

        logger.info(f"Cancelled download: {download.filename}")
        return True

    async def retry(self, download_id: str) -> bool:
        """Retry a failed download.

        Args:
            download_id: ID of the download to retry

        Returns:
            True if download was queued for retry, False otherwise
        """
        download = self.repo.get_by_id(download_id)
        if not download:
            return False

        if download.status != DownloadStatus.FAILED:
            return False

        # Reset status to pending
        self.repo.update_status(download_id, DownloadStatus.PENDING)

        # Re-queue
        await self.queue.put(download_id)

        logger.info(f"Retrying download: {download.filename}")
        return True

    def get_queue_position(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()

    # ========== Worker Operations ==========

    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine that processes downloads from the queue.

        Args:
            worker_id: ID of this worker for logging
        """
        logger.debug(f"Download worker {worker_id} started")

        while self.running:
            try:
                # Get download ID from queue with timeout
                try:
                    download_id = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Skip if cancelled
                if download_id in self.cancelled_downloads:
                    self.cancelled_downloads.discard(download_id)
                    continue

                # Skip if paused
                if download_id in self.paused_downloads:
                    continue

                # Process download
                await self._process_download(download_id)

            except asyncio.CancelledError:
                break
            except RuntimeError as e:
                # This task's own event loop is gone (closed out from under
                # it - see PersistentLoop, which replaces a dead consumer
                # rather than trying to keep this one alive). Every further
                # `await` here would raise the exact same way, including
                # during interpreter/task finalization - which, caught by a
                # bare `except Exception` with no exit, reproduces
                # synchronously and indefinitely (verified: this task then
                # spins forever consuming CPU instead of settling as
                # garbage). Retrying can't succeed, so stop this worker
                # cleanly instead.
                logger.error(f"Worker {worker_id} stopping (its event loop is gone): {e}")
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")

        logger.debug(f"Download worker {worker_id} stopped")

    async def _process_download(self, download_id: str) -> None:
        """Process a single download.

        Args:
            download_id: ID of the download to process
        """
        download = self.repo.get_by_id(download_id)
        if not download:
            logger.error(f"Download {download_id} not found")
            return

        if download.type == DownloadType.HF_REPO:
            # Grouped parents are aggregates - only their children carry bytes.
            logger.warning(f"Refusing to byte-download grouped parent {download_id}")
            return

        # Track active download
        task = asyncio.current_task()
        self.active_downloads[download_id] = task

        try:
            # Update status to downloading
            self.repo.update_status(download_id, DownloadStatus.DOWNLOADING)
            download.status = DownloadStatus.DOWNLOADING
            download.started_at = datetime.now()

            # Notify via WebSocket
            await self.conn.send_download_status(
                download_id,
                'started',
                download.filename
            )
            await self._refresh_group(download)

            # Perform download
            success = await self._download_file(download)

            if success:
                # Verify checksum if provided
                if download.checksum_sha256 and self.settings.verify_checksum:
                    if not await self._verify_checksum(download):
                        raise Exception("Checksum verification failed")

                # Update status to completed
                self.repo.update_status(download_id, DownloadStatus.COMPLETED)

                # Notify via WebSocket
                await self.conn.send_download_status(
                    download_id,
                    'completed',
                    download.filename,
                    path=download.destination_path
                )

                await self.conn.send_notification(
                    'download',
                    'success',
                    'Download Complete',
                    f'{download.filename} has been downloaded successfully'
                )
                await self._refresh_group(download)

                logger.info(f"Download completed: {download.filename}")

        except asyncio.CancelledError:
            # Download was cancelled
            if download_id in self.cancelled_downloads:
                self.repo.update_status(download_id, DownloadStatus.CANCELLED)
                self.cancelled_downloads.discard(download_id)

                await self.conn.send_download_status(
                    download_id,
                    'cancelled',
                    download.filename
                )
            else:
                # Paused or service stopping
                self.repo.update_status(download_id, DownloadStatus.PAUSED)
            await self._refresh_group(download)

        except DownloadAuthenticationException as e:
            # Auth/permission failures (401/403) never succeed on retry - the
            # credentials or license acceptance won't change between attempts,
            # so burning the retry budget just delays an actionable message.
            error_message = str(e)
            logger.warning(f"Download failed for {download.filename} (authentication): {error_message}")

            self.repo.update_status(download_id, DownloadStatus.FAILED, error_message)

            await self.conn.send_download_status(
                download_id,
                'failed',
                download.filename,
                error_message=error_message
            )

            await self.conn.send_notification(
                'download',
                'error',
                'Download Failed',
                f'{download.filename}: {error_message}'
            )
            await self._refresh_group(download)

        except Exception as e:
            error_message = str(e)
            logger.error(f"Download failed for {download.filename}: {error_message}")

            # Check if we should retry
            current_retries = self.repo.increment_retry(download_id)

            if self.settings.auto_retry_failed and current_retries <= self.settings.max_retries:
                # Re-queue for retry
                logger.info(f"Retrying download {download.filename} (attempt {current_retries})")
                await self.queue.put(download_id)

                await self.conn.send_download_status(
                    download_id,
                    'retrying',
                    download.filename,
                    error_message=f"Retry {current_retries}/{self.settings.max_retries}"
                )
            else:
                # Max retries reached or auto-retry disabled
                self.repo.update_status(download_id, DownloadStatus.FAILED, error_message)

                await self.conn.send_download_status(
                    download_id,
                    'failed',
                    download.filename,
                    error_message=error_message
                )

                await self.conn.send_notification(
                    'download',
                    'error',
                    'Download Failed',
                    f'{download.filename}: {error_message}'
                )
                await self._refresh_group(download)

        finally:
            # Remove from active downloads
            if download_id in self.active_downloads:
                del self.active_downloads[download_id]

    # ========== Provider resolution ==========

    def _resolve_provider(self, download: Download):
        """The marketplace provider that authenticates this download, if any.

        An explicit `provider_id` wins; otherwise every registered provider is
        asked whether it recognizes the URL (`matches_download_url`).
        """
        try:
            registry = self._provider_registry_factory()
        except Exception as e:
            logger.warning(f"Provider registry unavailable: {e}")
            return None
        if registry is None:
            return None

        if download.provider_id:
            provider = registry.get_provider(download.provider_id)
            if provider is None:
                logger.warning(f"Provider {download.provider_id} not found")
            return provider

        try:
            return registry.find_provider_for_url(download.url)
        except Exception as e:
            logger.warning(f"Provider URL matching failed: {e}")
            return None

    def _repo_label(self, download: Download) -> str:
        """A human-readable name for what's being fetched: the owning
        hf_repo group's `repo_id` if this is a grouped child, else the
        download's own `repo_id`/filename."""
        if download.group_id:
            parent = self.repo.get_by_id(download.group_id)
            if parent is not None and parent.repo_id:
                return parent.repo_id
        return download.repo_id or download.filename

    def _auth_failure_message(self, download: Download, status: int, provider) -> str:
        """An actionable message for a 401/403 on a download - names what
        was being fetched, states it's gated, and points at where the fix
        lives (the owning provider's settings) without core hardcoding a
        provider identifier."""
        label = self._repo_label(download)
        host = urlparse(download.url).hostname or "the source"
        provider_name = provider.get_metadata().name if provider else host
        return (
            f"Access denied (HTTP {status}) fetching '{label}' from {host} - "
            f"this repo is gated or private. Configure a valid {provider_name} "
            f"access token in Admin -> Plugins, and make sure the token's "
            f"account has accepted this model's license on {host}."
        )

    async def _download_file(self, download: Download) -> bool:
        """Download a file with progress tracking.

        Supports:
        - Chunked downloads
        - Resume via Range header
        - Progress tracking
        - Speed calculation
        - Provider authentication (headers/URL rewriting/redirect dances,
          all owned by the provider via `prepare_download`)

        Args:
            download: The download object

        Returns:
            True if download completed successfully
        """
        if not self.session:
            raise Exception("Download worker not started")

        # Ensure destination directory exists
        dest_path = Path(download.destination_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if partial file exists for resume
        temp_path = Path(str(dest_path) + '.part')
        resume_from = 0
        if temp_path.exists():
            resume_from = temp_path.stat().st_size
            download.downloaded_bytes = resume_from

        # Prepare headers and let the owning provider authenticate/rewrite the URL
        headers = {}
        download_url = download.url
        logger.info(f"Starting download for {download.filename}, provider_id={download.provider_id}")

        provider = self._resolve_provider(download)
        if provider:
            logger.debug(f"Using provider: {provider.provider_id}")
            download_url = await provider.prepare_download(self.session, download_url, headers)

            if download_url != download.url:
                # A provider that resolved the request to a CDN/pre-signed URL
                # may have surfaced the real filename along the way.
                extracted_filename = extract_filename_from_url(download_url)
                if extracted_filename and extracted_filename != download.filename:
                    await self._update_download_filename(download, extracted_filename)
                    dest_path = Path(download.destination_path)
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    temp_path = Path(str(dest_path) + '.part')
                    resume_from = temp_path.stat().st_size if temp_path.exists() else 0
                    download.downloaded_bytes = resume_from

        if resume_from > 0:
            headers['Range'] = f'bytes={resume_from}-'
            logger.info(f"Resuming download from byte {resume_from}")

        # Start download
        async with self.session.get(download_url, headers=headers) as response:
            # Check response status
            if response.status == 416:
                # Range not satisfiable - file may be complete
                if temp_path.exists():
                    temp_path.rename(dest_path)
                    return True
                raise Exception("Invalid range request")

            if response.status in (401, 403):
                raise DownloadAuthenticationException(
                    self._auth_failure_message(download, response.status, provider)
                )

            if response.status not in (200, 206):
                raise Exception(f"HTTP {response.status}: {response.reason}")

            # Validate Content-Type - catch HTML responses
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type.lower():
                raise Exception(
                    f"Server returned HTML instead of a file. "
                    f"This usually means authentication failed or the model requires special permissions."
                )

            # Get total size
            content_length = response.headers.get('Content-Length')
            if content_length:
                if response.status == 206:
                    # Partial content - parse content range
                    content_range = response.headers.get('Content-Range', '')
                    if '/' in content_range:
                        download.total_bytes = int(content_range.split('/')[-1])
                    else:
                        download.total_bytes = resume_from + int(content_length)
                else:
                    download.total_bytes = int(content_length)

            # Update total bytes in database
            if download.total_bytes:
                self.repo.update_total_bytes(download.id, download.total_bytes)
                self.repo.update_progress(
                    download.id,
                    download.progress,
                    download.downloaded_bytes,
                    None
                )

            # Download in chunks
            chunk_size = self.settings.chunk_size_kb * 1024
            mode = 'ab' if resume_from > 0 else 'wb'

            downloaded = resume_from
            last_update = asyncio.get_event_loop().time()
            last_downloaded = downloaded
            speed = 0.0

            with open(temp_path, mode) as f:
                async for chunk in response.content.iter_chunked(chunk_size):
                    # Check for cancellation
                    if download.id in self.cancelled_downloads:
                        raise asyncio.CancelledError()

                    # Check for pause
                    if download.id in self.paused_downloads:
                        raise asyncio.CancelledError()

                    # Write chunk
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Calculate speed and progress
                    current_time = asyncio.get_event_loop().time()
                    elapsed = current_time - last_update

                    if elapsed >= 0.5:  # Update every 500ms
                        speed = (downloaded - last_downloaded) / elapsed
                        last_downloaded = downloaded
                        last_update = current_time

                        # Calculate progress
                        progress = downloaded / download.total_bytes if download.total_bytes else 0

                        # Update database
                        self.repo.update_progress(download.id, progress, downloaded, speed)

                        # Send WebSocket update
                        await self.conn.send_download_progress(
                            download.id,
                            progress,
                            downloaded,
                            download.total_bytes,
                            speed,
                            download.filename
                        )
                        await self._refresh_group(download, broadcast_status=False)

        # Rename temp file to final destination
        logger.debug(f"Download loop finished. Downloaded {downloaded} bytes, total_bytes={download.total_bytes}")
        temp_path.rename(dest_path)

        # Final progress update
        self.repo.update_progress(download.id, 1.0, download.total_bytes or downloaded, speed)

        await self.conn.send_download_progress(
            download.id,
            1.0,
            download.total_bytes or downloaded,
            download.total_bytes,
            speed,
            download.filename
        )

        return True

    # ========== Group aggregation ==========

    async def _refresh_group(self, download: Download, broadcast_status: bool = True) -> None:
        """Recompute and broadcast the aggregate state of a grouped parent
        after one of its children moved."""
        if not download.group_id:
            return

        try:
            parent, status_changed = self.repo.refresh_group(download.group_id)
        except Exception as e:
            logger.error(f"Failed to refresh download group {download.group_id}: {e}")
            return
        if parent is None:
            return

        await self.conn.send_download_progress(
            parent.id,
            parent.progress,
            parent.downloaded_bytes,
            parent.total_bytes,
            parent.speed_bytes_per_sec,
            parent.filename
        )

        if broadcast_status and status_changed:
            event = _GROUP_STATUS_EVENTS.get(parent.status)
            if event:
                await self.conn.send_download_status(
                    parent.id,
                    event,
                    parent.filename,
                    error_message=parent.error_message,
                    path=parent.destination_path if parent.status == DownloadStatus.COMPLETED else None
                )
            if parent.status == DownloadStatus.COMPLETED:
                await self.conn.send_notification(
                    'download',
                    'success',
                    'Download Complete',
                    f'{parent.filename} has been downloaded successfully'
                )

    async def _verify_checksum(self, download: Download) -> bool:
        """Verify SHA256 checksum of downloaded file.

        Args:
            download: The download with checksum to verify

        Returns:
            True if checksum matches, False otherwise
        """
        if not download.checksum_sha256:
            return True

        logger.info(f"Verifying checksum for {download.filename}")

        sha256_hash = hashlib.sha256()
        chunk_size = 8192

        try:
            with open(download.destination_path, 'rb') as f:
                while chunk := f.read(chunk_size):
                    sha256_hash.update(chunk)

            calculated = sha256_hash.hexdigest().lower()
            expected = download.checksum_sha256.lower()

            if calculated != expected:
                logger.error(f"Checksum mismatch for {download.filename}: expected {expected}, got {calculated}")
                return False

            logger.info(f"Checksum verified for {download.filename}")
            return True

        except Exception as e:
            logger.error(f"Checksum verification error for {download.filename}: {e}")
            return False

    async def _update_download_filename(self, download: Download, new_filename: str) -> None:
        """Update a download's filename and destination path.

        Called when the real filename surfaces from a provider-resolved URL.

        Args:
            download: The download to update
            new_filename: The new filename
        """
        old_filename = download.filename
        old_dest_path = download.destination_path

        # Update filename
        download.filename = new_filename

        # Update destination path (same directory, new filename)
        dest_dir = os.path.dirname(old_dest_path)
        download.destination_path = os.path.join(dest_dir, new_filename)

        # Update in database
        self.repo.update_filename(download.id, new_filename, download.destination_path)

        logger.info(f"Updated download filename: {old_filename} -> {new_filename}")

    async def _resume_pending_downloads(self) -> None:
        """Resume downloads that were in progress when worker stopped."""
        # Mark any "downloading" status back to pending
        active_downloads = self.repo.get_active()
        for download in active_downloads:
            if download.type == DownloadType.HF_REPO:
                continue
            self.repo.update_status(download.id, DownloadStatus.PENDING)

        # Queue pending downloads (grouped parents carry no bytes of their own)
        pending_downloads = self.repo.get_pending()
        for download in pending_downloads:
            if download.type == DownloadType.HF_REPO:
                continue
            await self.queue.put(download.id)
