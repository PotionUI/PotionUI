"""Download manager - business logic layer for download operations.

Every model/media fetch in the system routes through this manager so all of
it lands in one admin-visible history: direct URL queueing (admin UI, model
recommendations, setup runs), and whole-Hugging-Face-repo fetches
(`queue_hf_repo_download` / the synchronous `ensure_local_hf_repo` used by
lazy first-use model loaders).

`ensure_asset_file` / `ensure_asset_repo` implement the platform-layer
`AssetFetcher` port (`src/platform/assets/`), which is how pipes - forbidden
from importing this package at all - reach this manager.
"""

import asyncio
import fnmatch
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Dict, List, Optional, Sequence, Tuple, Any, TYPE_CHECKING, TypeVar
from urllib.parse import urlparse, unquote

from src.features.downloads.exceptions import (
    DownloadException,
    DownloadNotFoundException,
    DownloadQueueException,
    DownloadOperationException,
    InvalidStatusException,
    InvalidTypeException,
)
from src.platform.assets import AssetFetchError
from src.features.downloads.worker import DownloadWorker
from src.features.downloads.models import Download, DownloadStatus, DownloadType, DownloadSettings
from src.features.downloads.repository import DownloadRepository
from src.features.downloads.persistent_loop import PersistentLoop
from src.features.downloads.hooks import DOWNLOAD_HOOKS
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.websocket.download_connection_manager import DownloadConnectionManager

if TYPE_CHECKING:
    from src.platform.settings.settings import SettingsManager

_T = TypeVar("_T")

_HF_BASE_URL = "https://huggingface.co"

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Coordinates download operations - business logic layer.

    Responsibilities:
    - Validation and status transitions
    - Hook execution for plugin integration
    - Coordinates DownloadWorker for actual downloads
    - Grouped hf_repo jobs (enumerate -> per-file children -> aggregate parent)
    - Settings management
    """

    def __init__(
        self,
        download_repository: DownloadRepository,
        plugin_registry: PluginRegistry,
        settings_manager: "SettingsManager",
        connection_manager: DownloadConnectionManager,
    ):
        """Initialize DownloadManager.

        Args:
            download_repository: Repository for download data access
            plugin_registry: Plugin registry for hook execution
            settings_manager: Settings source for default directories
            connection_manager: WebSocket broadcast surface
        """
        self.repo = download_repository
        self.plugins = plugin_registry
        self.settings_manager = settings_manager
        self.conn = connection_manager
        self.settings = DownloadSettings()
        self._load_settings()
        self.worker: Optional[DownloadWorker] = None

        # The worker's queue + `_worker` consumer tasks are always
        # started on this dedicated background loop, never on whichever
        # loop happens to be running `start()` (see persistent_loop.py for
        # why that used to orphan the consumer). `_worker_loop` is the loop
        # the *current* `self.worker` was actually started on - only set by
        # `start()` itself, so a test that injects `manager.worker = Mock()`
        # directly never touches the real background thread.
        self._persistent_loop = PersistentLoop("downloads-queue-consumer")
        self._worker_loop: Optional[asyncio.AbstractEventLoop] = None

        # Per-destination locks serialising the `AssetFetcher` methods, whose
        # callers (pipes) can run concurrently on several threads and would
        # otherwise each queue the same file.
        self._asset_locks: Dict[str, threading.Lock] = {}
        self._asset_locks_guard = threading.Lock()

    def _load_settings(self) -> None:
        """Load settings from database."""
        try:
            # Normalised because the stored value may carry a leading "./"
            # that would otherwise reach destination_path via os.path.join.
            model_dir = self.settings_manager.get_setting('models_dir')
            if model_dir:
                self.settings.default_model_directory = str(Path(model_dir))

            file_storage = self.settings_manager.get_setting('file_storage_directory')
            if file_storage:
                self.settings.default_media_directory = file_storage

        except Exception as e:
            logger.warning(f"Failed to load download settings: {e}")

    # ========== Lifecycle ==========

    async def start(self) -> None:
        """Start the download manager and worker.

        The worker itself - its `asyncio.Queue`, its `_worker` consumer
        tasks, its `aiohttp.ClientSession` - is started ON the manager's own
        persistent background loop (see `persistent_loop.py`), not on
        whatever loop happens to be running this coroutine. `start()` can be
        called from the app's real request-handling loop, from a setup-run
        executor's throwaway `run_sync()` loop, or (at process boot, before
        uvicorn's loop exists) from no loop at all - binding the worker to a
        loop that is guaranteed to outlive any single one of those calls is
        what keeps queued downloads from silently never starting.
        """
        self.worker = DownloadWorker(self.settings, self.repo, self.conn)
        self._worker_loop = self._persistent_loop.ensure_running()
        await self._call_on_worker(self.worker.start())
        logger.info(
            "Download manager started (worker running on persistent loop '%s')",
            self._persistent_loop.name,
        )

    async def stop(self) -> None:
        """Stop the download manager and worker."""
        if self.worker:
            await self._call_on_worker(self.worker.stop())
            self.worker = None
            self._worker_loop = None
        logger.info("Download manager stopped")

    # ========== Persistent-loop bridging ==========

    async def _call_on_worker(self, coro: Awaitable[_T]) -> _T:
        """Await `coro` (already invoked against `self.worker`, e.g.
        `self.worker.enqueue(id)`) on the loop that actually owns it.

        Once `start()` has run, `self.worker`'s queue and tasks live on
        `self._worker_loop` (the persistent loop) - awaiting `coro` directly
        on whatever loop happens to be calling this would either hang or
        raise `RuntimeError: <Queue ...> is bound to a different event
        loop`. Tests that inject a worker directly (`manager.worker =
        Mock()`, bypassing `start()`) never set `_worker_loop`, so this is a
        plain `await` for them.
        """
        if self._worker_loop is not None:
            future = asyncio.run_coroutine_threadsafe(coro, self._worker_loop)
            return await asyncio.wrap_future(future)
        return await coro

    async def _ensure_worker_ready(self) -> None:
        """Self-healing: (re)start the worker if it's missing, or if
        the persistent loop that used to run it has died. A prior worker's
        background thread can die for reasons outside this process's
        control; the next queue use just gets a fresh worker + a fresh
        persistent loop instead of silently enqueuing into a dead consumer.
        """
        if self.worker is None:
            await self.start()
        elif self._worker_loop is not None and not self._worker_loop.is_running():
            await self.start()

    # ========== Query Operations ==========

    def list_downloads(
        self,
        status: Optional[str] = None,
        download_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """List downloads with optional filtering.

        Grouped children are hidden - an hf_repo job reads as one history row.

        Args:
            status: Optional status filter
            download_type: Optional type filter (model/media/hf_repo)
            limit: Maximum number of downloads to return
            offset: Pagination offset
            user_id: Optional user ID filter

        Returns:
            Dict with downloads, counts, and total

        Raises:
            InvalidStatusException: If status is invalid
            InvalidTypeException: If download_type is invalid
        """
        # Parse status filter
        status_enum = None
        if status:
            try:
                status_enum = DownloadStatus(status)
            except ValueError:
                raise InvalidStatusException(f"Invalid status: {status}")

        # Parse type filter
        type_enum = None
        if download_type:
            try:
                type_enum = DownloadType(download_type)
            except ValueError:
                raise InvalidTypeException(f"Invalid type: {download_type}")

        # Get downloads
        downloads = self.repo.get_all(
            limit=limit,
            offset=offset,
            status=status_enum,
            download_type=type_enum,
            created_by=user_id,
            top_level_only=True
        )

        # Get counts by status
        counts = self.repo.count_by_status(top_level_only=True)

        return {
            'downloads': [d.to_dict() for d in downloads],
            'counts': counts,
            'total': self.repo.count_total(top_level_only=True)
        }

    def get_download(self, download_id: str) -> Download:
        """Get a specific download by ID.

        Args:
            download_id: The download ID

        Returns:
            Download object

        Raises:
            DownloadNotFoundException: If download not found
        """
        download = self.repo.get_by_id(download_id)
        if not download:
            raise DownloadNotFoundException(f"Download '{download_id}' not found")
        return download

    def find_active_download_for_repo(self, repo_id: str) -> Optional[Download]:
        """The in-flight `hf_repo` download job for `repo_id`, if any.

        Used by asset-status endpoints (e.g. the semantic-search settings
        pane's embedding/tagger/vision status) so a reloading or reconnecting
        client can reconstruct "a fetch is already running" from this call
        alone, rather than from state a page-local map would lose on reload.
        """
        return self.repo.find_active_by_repo_id(repo_id)

    # ========== Destination resolution ==========

    @staticmethod
    def _resolve_contained_dir(
        root: str,
        requested: Optional[str] = None,
        trusted_subdir: Optional[str] = None,
    ) -> Path:
        """Resolve a download's destination directory against `root`, refusing
        to let it land outside `root`.

        `root` is the admin-configured depot (`default_model_directory` /
        `default_media_directory`), never the process CWD. `trusted_subdir`
        (server-computed, e.g. from a `model_type` via `TYPE_DIR_MAP`) is
        joined onto `root` and wins when given. Otherwise `requested` - a
        directory string that may have come straight from an HTTP request, or
        from a plugin's `before_queue` hook - is treated as untrusted and
        joined onto `root`.

        The join alone does not contain it: a `../..`-laden relative path
        survives the join, and under `pathlib` semantics an absolute path
        (e.g. `/etc`) replaces `root` entirely rather than nesting under it.
        Containment comes from what happens next - the joined result is
        realpath-resolved (so a symlink hop out of the depot is caught too)
        and checked against `root`; if it resolves to anywhere outside `root`,
        this raises `DownloadQueueException` rather than silently re-rooting
        the path.

        A destination this process already resolved against the depot must NOT
        come back through here - joining a depot-rooted path onto the depot
        root again doubles the prefix, and the doubled path is still inside the
        depot, so containment passes and the mistake is silent. Verify such a
        path with `_verify_contained_dir` instead.
        """
        root_path = Path(root)
        subdir = trusted_subdir if trusted_subdir is not None else requested
        candidate = root_path / subdir if subdir else root_path
        return DownloadManager._verify_contained_dir(root, candidate, label=subdir)

    @staticmethod
    def _verify_contained_dir(
        root: str,
        candidate: Path,
        label: Optional[str] = None,
    ) -> Path:
        """`candidate` as given if it is `root` or a descendant, else
        `DownloadQueueException`. The only containment check in this module.

        Takes a complete directory rather than a subdir to join, which is what
        an already-resolved destination needs: the check is what makes a path
        acceptable, and it holds however the path was built. `candidate` is
        realpath-resolved before the comparison, so a `../..` component or a
        symlink hop out of the depot is caught rather than smuggled through.

        Returns the path as given, not its resolved form: `models/checkpoints`
        is a symlink into shared storage on some installs, and baking the
        target into `destination_path` would record a location the depot's own
        layout no longer explains. `label` names the path in the refusal when
        the caller has a more meaningful spelling of it than the joined result.
        """
        root_path = Path(root)
        resolved_root = root_path.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
            raise DownloadQueueException(
                f"Destination directory '{label if label is not None else candidate}' "
                f"escapes the configured directory '{root}'"
            )
        return candidate

    # ========== Queue Operations ==========

    async def queue_model_download(
        self,
        url: str,
        destination_dir: Optional[str] = None,
        filename: Optional[str] = None,
        tags: Optional[List[str]] = None,
        checksum_sha256: Optional[str] = None,
        provider_id: Optional[str] = None,
        created_by: Optional[str] = None,
        model_type: Optional[str] = None,
    ) -> Download:
        """Queue a model file for download.

        Executes hooks:
        - download.before_queue: Can modify/validate data or block
        - download.after_queue: Notification of successful queue

        Args:
            url: URL to download from
            destination_dir: Optional destination directory. Untrusted: always
                resolved against the configured model depot (see
                `_resolve_contained_dir`), never used as a standalone path -
                a caller cannot point a download outside the depot this way.
            filename: Optional filename override
            tags: Optional tags list
            checksum_sha256: Optional SHA256 checksum
            provider_id: Optional provider ID for authentication
            created_by: Optional user ID
            model_type: Optional model type (e.g. "checkpoint", "lora"). When
                given, takes precedence over `destination_dir` and resolves
                the destination via `TYPE_DIR_MAP` - the same mapping the
                indexer scans by - so the file lands where it will actually
                be found.

        Returns:
            Created Download object

        Raises:
            DownloadQueueException: If queueing fails, is blocked, or the
                resolved destination would escape the configured depot
        """
        # Execute before_queue hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_queue,
            {
                "url": url,
                "type": "model",
                "destination_dir": destination_dir,
                "filename": filename,
                "tags": tags,
                "checksum_sha256": checksum_sha256,
                "provider_id": provider_id,
                "created_by": created_by,
                "model_type": model_type,
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Download queue blocked")
            logger.warning(f"Model download queue blocked by plugin: {reason}")
            raise DownloadQueueException(reason)

        # Allow hooks to modify data
        url = hook_data.get("url", url)
        destination_dir = hook_data.get("destination_dir", destination_dir)
        filename = hook_data.get("filename", filename)
        tags = hook_data.get("tags", tags)
        model_type = hook_data.get("model_type", model_type)

        trusted_subdir = None
        if model_type:
            from src.features.models.jobs import TYPE_DIR_MAP

            trusted_subdir = TYPE_DIR_MAP.get(model_type, model_type)

        destination_dir = str(self._resolve_contained_dir(
            self.settings.default_model_directory,
            requested=destination_dir,
            trusted_subdir=trusted_subdir,
        ))

        # Extract filename from URL if not provided
        if not filename:
            parsed = urlparse(url)
            filename = unquote(os.path.basename(parsed.path))
            if not filename:
                filename = f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        destination_path = os.path.join(destination_dir, filename)

        # Create download record
        download = Download(
            type=DownloadType.MODEL,
            url=url,
            destination_path=destination_path,
            filename=filename,
            status=DownloadStatus.PENDING,
            tags=tags or [],
            checksum_sha256=checksum_sha256,
            provider_id=provider_id,
            created_by=created_by
        )

        created_download = self.repo.create(download)

        # Queue for processing. Self-healing: a worker whose persistent loop
        # died gets replaced with a fresh one here rather than silently
        # enqueuing into a dead consumer.
        await self._ensure_worker_ready()
        await self._call_on_worker(self.worker.enqueue(created_download.id))

        # Get queue position
        position = self.worker.get_queue_position() if self.worker else 0

        # Notify via WebSocket
        await self.conn.send_download_queued(
            created_download.id,
            filename,
            position
        )

        # Execute after_queue hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_queue,
            {
                "download_id": created_download.id,
                "url": url,
                "type": "model",
                "filename": filename
            }
        )

        logger.info(f"Queued model download: {filename}")
        return created_download

    async def queue_media_download(
        self,
        url: str,
        destination_dir: Optional[str] = None,
        filename: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Download:
        """Queue a media file for download.

        Executes hooks:
        - download.before_queue: Can modify/validate data or block
        - download.after_queue: Notification of successful queue

        Args:
            url: URL to download from
            destination_dir: Optional destination directory
            filename: Optional filename override
            created_by: Optional user ID

        Returns:
            Created Download object

        Raises:
            DownloadQueueException: If queueing fails or is blocked
        """
        # Execute before_queue hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_queue,
            {
                "url": url,
                "type": "media",
                "destination_dir": destination_dir,
                "filename": filename,
                "created_by": created_by
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Download queue blocked")
            logger.warning(f"Media download queue blocked by plugin: {reason}")
            raise DownloadQueueException(reason)

        # Allow hooks to modify data
        url = hook_data.get("url", url)
        destination_dir = hook_data.get("destination_dir", destination_dir)
        filename = hook_data.get("filename", filename)

        # Determine destination directory (untrusted input contained inside
        # the configured media depot - see `_resolve_contained_dir`)
        destination_dir = str(self._resolve_contained_dir(
            self.settings.default_media_directory,
            requested=destination_dir,
        ))

        # Extract filename from URL if not provided
        if not filename:
            parsed = urlparse(url)
            filename = unquote(os.path.basename(parsed.path))
            if not filename:
                filename = f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        destination_path = os.path.join(destination_dir, filename)

        # Create download record
        download = Download(
            type=DownloadType.MEDIA,
            url=url,
            destination_path=destination_path,
            filename=filename,
            status=DownloadStatus.PENDING,
            created_by=created_by
        )

        created_download = self.repo.create(download)

        # Queue for processing (self-healing, see queue_model_download)
        await self._ensure_worker_ready()
        await self._call_on_worker(self.worker.enqueue(created_download.id))

        # Get queue position
        position = self.worker.get_queue_position() if self.worker else 0

        # Notify via WebSocket
        await self.conn.send_download_queued(
            created_download.id,
            filename,
            position
        )

        # Execute after_queue hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_queue,
            {
                "download_id": created_download.id,
                "url": url,
                "type": "media",
                "filename": filename
            }
        )

        logger.info(f"Queued media download: {filename}")
        return created_download

    async def queue_batch_downloads(
        self,
        urls: List[str],
        destination_dir: Optional[str] = None,
        download_type: str = "media",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Queue multiple files for download.

        Args:
            urls: List of URLs to download
            destination_dir: Optional destination directory
            download_type: Type of downloads ("model" or "media")
            user_id: Optional user ID

        Returns:
            Dict with queued downloads and errors
        """
        downloads = []
        errors = []

        for url in urls:
            try:
                if download_type == "model":
                    download = await self.queue_model_download(
                        url=url,
                        destination_dir=destination_dir,
                        created_by=user_id
                    )
                else:
                    download = await self.queue_media_download(
                        url=url,
                        destination_dir=destination_dir,
                        created_by=user_id
                    )
                downloads.append(download.to_dict())
            except Exception as e:
                errors.append({'url': url, 'error': str(e)})

        return {
            'queued': downloads,
            'errors': errors,
            'total_queued': len(downloads),
            'total_errors': len(errors)
        }

    # ========== Hugging Face repo jobs ==========

    def _hf_token(self) -> Optional[str]:
        """A Hugging Face token, if a provider claiming huggingface.co
        downloads is installed and configured. Resolved through the provider
        seam - core names no plugin."""
        try:
            from src.features.providers.registry import get_provider_registry

            provider = get_provider_registry().find_provider_for_url(_HF_BASE_URL + "/")
        except Exception as e:
            logger.debug(f"No provider available for Hugging Face token lookup: {e}")
            return None
        if provider is None:
            return None
        auth = provider.get_download_headers().get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):] or None
        return None

    def _enumerate_hf_repo(
        self,
        repo_id: str,
        revision: Optional[str],
        allow_patterns: Optional[List[str]],
    ) -> List[Tuple[str, Optional[int], str]]:
        """List a repo's files as (path_in_repo, size_or_None, resolve_url).

        Talks to the Hub API directly rather than through `huggingface_hub`:
        the native engine defaults `HF_HUB_OFFLINE=1` process-wide (see
        `src/platform/runtime/native/text_encoders/tokenization.py`), which
        makes every `HfApi` call in this process refuse - and downloading is
        the one job that must reach the network regardless.

        Public repos need no token; a configured Hugging Face provider's
        credentials apply automatically for gated/private repos.
        """
        import requests
        from urllib.parse import quote

        rev = quote(revision, safe="") if revision else None
        url = f"{_HF_BASE_URL}/api/models/{repo_id}"
        if rev:
            url += f"/revision/{rev}"

        headers = {}
        token = self._hf_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = requests.get(url, params={"blobs": "true"}, headers=headers, timeout=30)
        response.raise_for_status()
        siblings = response.json().get("siblings") or []

        files: List[Tuple[str, Optional[int], str]] = []
        for sibling in siblings:
            rfilename = sibling.get("rfilename")
            if not rfilename:
                continue
            if allow_patterns and not any(
                fnmatch.fnmatch(rfilename, pattern) for pattern in allow_patterns
            ):
                continue
            resolve_url = (
                f"{_HF_BASE_URL}/{repo_id}/resolve/{rev or 'main'}/{quote(rfilename)}"
            )
            files.append((rfilename, sibling.get("size"), resolve_url))
        return files

    async def queue_hf_repo_download(
        self,
        repo_id: str,
        destination_dir: Optional[str] = None,
        revision: Optional[str] = None,
        allow_patterns: Optional[List[str]] = None,
        created_by: Optional[str] = None,
        *,
        trusted_destination_dir: Optional[str] = None,
    ) -> Download:
        """Queue a whole Hugging Face repo as one logical grouped download.

        Enumerates the repo's files via HfApi, creates one parent history row
        (type `hf_repo`) plus one byte-progress child per file, and enqueues
        the children on the regular worker. The parent's progress/status are
        aggregates of its children.

        Args:
            repo_id: e.g. "org/model-name"
            destination_dir: Where the repo's file tree lands, as a subdir of
                the model depot; defaults to `<org--model-name>`. Untrusted -
                always joined onto the depot root, never used as a standalone
                path (see `_resolve_contained_dir`).
            revision: Optional git revision to pin
            allow_patterns: Optional fnmatch patterns limiting which files download
            created_by: Optional user ID
            trusted_destination_dir: A destination this process already resolved
                against the depot, used as given rather than joined onto the
                root again. For internal callers only - `ensure_local_hf_repo`
                and, through it, the `AssetFetcher` methods, which hand their
                caller back the very directory they passed and so need the
                bytes to land exactly there. Still verified against the depot
                (`_verify_contained_dir`), so a caller that computes one wrongly
                is refused rather than trusted blindly. Not reachable from the
                HTTP route, which has no such field.

        Returns:
            The parent Download (the group's history row)

        Raises:
            DownloadQueueException: If enumeration fails, matches nothing,
                or a plugin blocks queueing
        """
        repo_url = f"{_HF_BASE_URL}/{repo_id}"

        trusted = trusted_destination_dir is not None
        requested_dir = trusted_destination_dir if trusted else destination_dir

        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_queue,
            {
                "url": repo_url,
                "type": "hf_repo",
                "destination_dir": requested_dir,
                "filename": repo_id,
                "created_by": created_by,
            }
        )
        if blocked:
            reason = hook_data.get("block_reason", "Download queue blocked")
            logger.warning(f"HF repo download queue blocked by plugin: {reason}")
            raise DownloadQueueException(reason)

        destination_dir = hook_data.get("destination_dir", requested_dir)
        if destination_dir != requested_dir:
            # A plugin rewrote the destination: untrusted from here on, however
            # the caller supplied it. A hook cannot promote itself by rewriting
            # a trusted path.
            trusted = False

        if not destination_dir:
            # Bare, so the join below roots it in the depot - pre-joining the
            # root here would double the prefix.
            destination_dir = repo_id.replace("/", "--")
            trusted = False

        # Contained inside the configured model depot before anything is
        # enumerated or written to history, same contract as
        # queue_model_download / queue_media_download. An untrusted
        # destination (request body, or a plugin's before_queue hook) is
        # joined onto the depot root first; an already-resolved one is only
        # verified, because re-rooting it would double the depot prefix.
        root = self.settings.default_model_directory
        destination_dir = str(
            self._verify_contained_dir(root, Path(destination_dir))
            if trusted
            else self._resolve_contained_dir(root, requested=destination_dir)
        )

        try:
            files = await asyncio.to_thread(
                self._enumerate_hf_repo, repo_id, revision, allow_patterns
            )
        except Exception as e:
            raise DownloadQueueException(f"Could not enumerate Hugging Face repo '{repo_id}': {e}")

        if not files:
            raise DownloadQueueException(
                f"Hugging Face repo '{repo_id}' has no files"
                + (" matching the given patterns" if allow_patterns else "")
            )

        sizes = [size for _, size, _ in files]
        total_bytes = sum(sizes) if all(size for size in sizes) else None

        parent = self.repo.create(Download(
            type=DownloadType.HF_REPO,
            url=repo_url,
            destination_path=destination_dir,
            filename=repo_id,
            status=DownloadStatus.PENDING,
            total_bytes=total_bytes,
            repo_id=repo_id,
            revision=revision,
            created_by=created_by,
        ))

        children = []
        for rfilename, size, url in files:
            children.append(self.repo.create(Download(
                type=DownloadType.MODEL,
                url=url,
                destination_path=os.path.join(destination_dir, rfilename),
                filename=rfilename,
                status=DownloadStatus.PENDING,
                total_bytes=size,
                group_id=parent.id,
                created_by=created_by,
            )))

        await self._ensure_worker_ready()
        for child in children:
            await self._call_on_worker(self.worker.enqueue(child.id))

        position = self.worker.get_queue_position() if self.worker else 0
        await self.conn.send_download_queued(parent.id, parent.filename, position)

        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_queue,
            {
                "download_id": parent.id,
                "url": repo_url,
                "type": "hf_repo",
                "filename": repo_id,
            }
        )

        logger.info(f"Queued HF repo download: {repo_id} ({len(children)} files)")
        return parent

    def ensure_local_hf_repo(
        self,
        repo_id: str,
        target_dir: str,
        revision: Optional[str] = None,
        allow_patterns: Optional[List[str]] = None,
        poll_interval: float = 1.0,
        timeout: Optional[float] = None,
    ) -> Path:
        """Fetch a Hugging Face repo into `target_dir`, waiting until done.

        The synchronous wrapper the lazy first-use model loaders call in
        place of a direct `snapshot_download`, so every fetch lands in the
        download history with live progress.

        `target_dir` is used as given and returned as given - it is where the
        bytes actually land, which is what lets a caller pass it straight to
        `from_pretrained`. It is a complete directory, NOT a subdir joined onto
        the depot, so callers pass a depot-rooted path (`<models_dir>/clip/...`)
        rather than a bare subdir. It must still resolve inside the configured
        model depot; one that escapes is refused, not re-rooted.

        Sync-context design: the callers are synchronous (often running
        inside `asyncio.to_thread`, sometimes on a thread with no event loop
        at all). This method therefore never awaits on the caller's context:
        it schedules `queue_hf_repo_download` onto the manager's own
        persistent background loop with `run_coroutine_threadsafe` and then
        plain-polls the repository with `time.sleep` until the grouped
        parent reaches a terminal status. Safe from any thread; MUST NOT be
        called from a coroutine running on the persistent loop itself (the
        blocking wait would deadlock the queue consumer - no core caller
        does).

        Args:
            repo_id: e.g. "org/model-name"
            target_dir: Directory the repo's file tree lands in, as a complete
                depot-rooted path (see above)
            revision: Optional git revision to pin
            allow_patterns: Optional fnmatch patterns limiting which files download
            poll_interval: Seconds between completion polls
            timeout: Optional overall wait bound in seconds

        Returns:
            Path(target_dir) once the grouped download completed

        Raises:
            DownloadQueueException: If the job could not be queued
            DownloadOperationException: If the download failed, was
                cancelled, or the timeout elapsed
        """
        loop = self._persistent_loop.ensure_running()
        future = asyncio.run_coroutine_threadsafe(
            self.queue_hf_repo_download(
                repo_id,
                revision=revision,
                allow_patterns=allow_patterns,
                # `target_dir` is a complete destination, not a subdir to root:
                # this method returns it to the caller as where the bytes are,
                # so the bytes must land exactly there. Passing it as the
                # untrusted `destination_dir` would join it onto the depot root
                # and, for a target already inside the depot, silently double
                # the prefix - the download would report success from a
                # directory the caller never looks in.
                trusted_destination_dir=str(target_dir),
            ),
            loop,
        )
        parent = future.result()

        deadline = time.monotonic() + timeout if timeout else None
        while True:
            current = self.repo.get_by_id(parent.id)
            if current is None:
                raise DownloadOperationException(
                    f"Download record for '{repo_id}' disappeared while waiting"
                )
            if current.status == DownloadStatus.COMPLETED:
                return Path(target_dir)
            if current.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                raise DownloadOperationException(
                    f"Download of '{repo_id}' {current.status.value}"
                    + (f": {current.error_message}" if current.error_message else "")
                )
            if deadline and time.monotonic() > deadline:
                raise DownloadOperationException(
                    f"Timed out waiting for download of '{repo_id}'"
                )
            time.sleep(poll_interval)

    # ========== Asset fetching (the platform `AssetFetcher` port) ==========

    def _asset_lock(self, key: str) -> threading.Lock:
        with self._asset_locks_guard:
            lock = self._asset_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._asset_locks[key] = lock
            return lock

    def _asset_dir(self, subdir: str) -> Path:
        """`subdir` resolved inside the model depot, or `AssetFetchError`.

        Translates the containment refusal into the port's error type so a
        pipe - which cannot import this package's exceptions - can catch it.
        """
        try:
            return self._resolve_contained_dir(
                self.settings.default_model_directory, requested=subdir
            )
        except DownloadException as e:
            raise AssetFetchError(str(e)) from e

    def _await_download(
        self,
        download_id: str,
        label: str,
        poll_interval: float,
        timeout: Optional[float],
    ) -> None:
        """Block until `download_id` reaches a terminal status.

        Plain `time.sleep` polling rather than an await: see
        `ensure_local_hf_repo` for why the synchronous callers cannot await on
        their own context. Same constraint applies - MUST NOT be called from a
        coroutine running on the persistent loop.
        """
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            current = self.repo.get_by_id(download_id)
            if current is None:
                raise AssetFetchError(
                    f"Download record for '{label}' disappeared while waiting"
                )
            if current.status == DownloadStatus.COMPLETED:
                return
            if current.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                raise AssetFetchError(
                    f"Download of '{label}' {current.status.value}"
                    + (f": {current.error_message}" if current.error_message else "")
                )
            if deadline and time.monotonic() > deadline:
                raise AssetFetchError(f"Timed out waiting for download of '{label}'")
            time.sleep(poll_interval)

    def ensure_asset_file(
        self,
        url: str,
        *,
        subdir: str,
        filename: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 1.0,
    ) -> Path:
        """`AssetFetcher.ensure_asset_file` - see `src/platform/assets/fetcher.py`."""
        target_dir = self._asset_dir(subdir)
        name = filename or unquote(os.path.basename(urlparse(url).path))
        if not name:
            raise AssetFetchError(
                f"No filename could be derived from '{url}'; pass filename= explicitly"
            )
        target = target_dir / name

        with self._asset_lock(str(target)):
            if target.exists():
                return target

            loop = self._persistent_loop.ensure_running()
            try:
                download = asyncio.run_coroutine_threadsafe(
                    self.queue_model_download(
                        url, destination_dir=subdir, filename=name
                    ),
                    loop,
                ).result()
            except DownloadException as e:
                raise AssetFetchError(f"Could not queue '{url}': {e}") from e

            self._await_download(download.id, name, poll_interval, timeout)
            # The record's own destination, not the precomputed `target`: a
            # `download.before_queue` hook may have rewritten it (still
            # depot-contained), and the caller needs where the bytes landed.
            return Path(download.destination_path)

    def ensure_asset_repo(
        self,
        repo_id: str,
        *,
        subdir: str,
        files: Optional[Sequence[str]] = None,
        revision: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 1.0,
    ) -> Path:
        """`AssetFetcher.ensure_asset_repo` - see `src/platform/assets/fetcher.py`."""
        target_dir = self._asset_dir(subdir)

        with self._asset_lock(str(target_dir)):
            if self._asset_repo_present(target_dir, files):
                return target_dir

            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.ensure_local_hf_repo(
                    repo_id,
                    str(target_dir),
                    revision=revision,
                    allow_patterns=list(files) if files else None,
                    poll_interval=poll_interval,
                    timeout=timeout,
                )
            except DownloadException as e:
                raise AssetFetchError(
                    f"Could not fetch Hugging Face repo '{repo_id}': {e}"
                ) from e
            return target_dir

    @staticmethod
    def _asset_repo_present(target_dir: Path, files: Optional[Sequence[str]]) -> bool:
        if files:
            return all((target_dir / name).exists() for name in files)
        return target_dir.is_dir() and any(target_dir.iterdir())

    # ========== Control Operations ==========

    def _group_children(self, download: Download) -> Optional[List[Download]]:
        """The children of a grouped parent, or None for plain downloads."""
        if download.type != DownloadType.HF_REPO:
            return None
        return self.repo.get_children(download.id)

    async def pause_download(self, download_id: str) -> Download:
        """Pause an active download (all active children, for a grouped one).

        Executes hooks:
        - download.before_pause: Can block
        - download.after_pause: Notification

        Args:
            download_id: ID of download to pause

        Returns:
            Updated Download object

        Raises:
            DownloadNotFoundException: If download not found
            DownloadOperationException: If pause fails or is blocked
        """
        download = self.get_download(download_id)

        # Execute before_pause hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_pause,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Pause blocked")
            logger.warning(f"Download pause blocked by plugin: {reason}")
            raise DownloadOperationException(reason)

        if not self.worker:
            raise DownloadOperationException("Download worker not running")

        children = self._group_children(download)
        if children is not None:
            paused_any = False
            for child in children:
                if await self._call_on_worker(self.worker.pause(child.id)):
                    paused_any = True
            if not paused_any:
                raise DownloadOperationException("Download cannot be paused (not active)")
            self.repo.refresh_group(download.id)
        else:
            success = await self._call_on_worker(self.worker.pause(download_id))
            if not success:
                raise DownloadOperationException("Download cannot be paused (not active)")

        # Execute after_pause hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_pause,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        return self.get_download(download_id)

    async def resume_download(self, download_id: str) -> Download:
        """Resume a paused download (all paused/failed children, for a grouped one).

        Executes hooks:
        - download.before_resume: Can block
        - download.after_resume: Notification

        Args:
            download_id: ID of download to resume

        Returns:
            Updated Download object

        Raises:
            DownloadNotFoundException: If download not found
            DownloadOperationException: If resume fails or is blocked
        """
        download = self.get_download(download_id)

        # Execute before_resume hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_resume,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Resume blocked")
            logger.warning(f"Download resume blocked by plugin: {reason}")
            raise DownloadOperationException(reason)

        if not self.worker:
            raise DownloadOperationException("Download worker not running")

        children = self._group_children(download)
        if children is not None:
            resumed_any = False
            for child in children:
                if await self._call_on_worker(self.worker.resume(child.id)):
                    resumed_any = True
            if not resumed_any:
                raise DownloadOperationException("Download cannot be resumed (not paused/failed)")
            self.repo.refresh_group(download.id)
        else:
            success = await self._call_on_worker(self.worker.resume(download_id))
            if not success:
                raise DownloadOperationException("Download cannot be resumed (not paused/failed)")

        # Execute after_resume hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_resume,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        return self.get_download(download_id)

    async def cancel_download(self, download_id: str) -> Download:
        """Cancel an active or pending download (all children, for a grouped one).

        Executes hooks:
        - download.before_cancel: Can block
        - download.after_cancel: Notification

        Args:
            download_id: ID of download to cancel

        Returns:
            Updated Download object

        Raises:
            DownloadNotFoundException: If download not found
            DownloadOperationException: If cancel fails or is blocked
        """
        download = self.get_download(download_id)

        # Execute before_cancel hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_cancel,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Cancel blocked")
            logger.warning(f"Download cancel blocked by plugin: {reason}")
            raise DownloadOperationException(reason)

        if not self.worker:
            raise DownloadOperationException("Download worker not running")

        children = self._group_children(download)
        if children is not None:
            cancelled_any = False
            for child in children:
                if await self._call_on_worker(self.worker.cancel(child.id)):
                    cancelled_any = True
            if not cancelled_any:
                raise DownloadOperationException("Download cannot be cancelled (already completed/cancelled)")
            self.repo.refresh_group(download.id)
        else:
            success = await self._call_on_worker(self.worker.cancel(download_id))
            if not success:
                raise DownloadOperationException("Download cannot be cancelled (already completed/cancelled)")

        # Execute after_cancel hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_cancel,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        return self.get_download(download_id)

    async def retry_download(self, download_id: str) -> Download:
        """Retry a failed download (all failed children, for a grouped one).

        Args:
            download_id: ID of download to retry

        Returns:
            Updated Download object

        Raises:
            DownloadNotFoundException: If download not found
            DownloadOperationException: If retry fails
        """
        download = self.get_download(download_id)

        if not self.worker:
            raise DownloadOperationException("Download worker not running")

        children = self._group_children(download)
        if children is not None:
            retried_any = False
            for child in children:
                if await self._call_on_worker(self.worker.retry(child.id)):
                    retried_any = True
            if not retried_any:
                raise DownloadOperationException("Download cannot be retried (not failed)")
            self.repo.refresh_group(download.id)
        else:
            success = await self._call_on_worker(self.worker.retry(download_id))
            if not success:
                raise DownloadOperationException("Download cannot be retried (not failed)")

        return self.get_download(download_id)

    # ========== Delete Operations ==========

    async def delete_download(self, download_id: str) -> None:
        """Delete a download record (with its grouped children).

        Executes hooks:
        - download.before_delete: Can block
        - download.after_delete: Notification

        Args:
            download_id: ID of download to delete

        Raises:
            DownloadNotFoundException: If download not found
            DownloadOperationException: If delete is blocked
        """
        download = self.get_download(download_id)

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_delete,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Delete blocked")
            logger.warning(f"Download delete blocked by plugin: {reason}")
            raise DownloadOperationException(reason)

        # Cancel if active
        if self.worker:
            children = self._group_children(download)
            if children is not None:
                for child in children:
                    if child.status == DownloadStatus.DOWNLOADING:
                        await self._call_on_worker(self.worker.cancel(child.id))
            elif download.status == DownloadStatus.DOWNLOADING:
                await self._call_on_worker(self.worker.cancel(download_id))

        # Delete record (repo cascades to grouped children)
        self.repo.delete(download_id)

        # Execute after_delete hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_delete,
            {
                "download_id": download_id,
                "filename": download.filename
            }
        )

        logger.info(f"Deleted download: {download.filename}")

    def clear_completed(self) -> int:
        """Clear all completed downloads from history.

        Executes hooks:
        - download.before_clear: Can block
        - download.after_clear: Notification

        Returns:
            Number of downloads cleared

        Raises:
            DownloadOperationException: If clear is blocked
        """
        # Execute before_clear hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_clear,
            {
                "clear_type": "completed"
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Clear blocked")
            logger.warning(f"Clear completed blocked by plugin: {reason}")
            raise DownloadOperationException(reason)

        count = self.repo.delete_completed()

        # Execute after_clear hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_clear,
            {
                "clear_type": "completed",
                "count": count
            }
        )

        logger.info(f"Cleared {count} completed downloads")
        return count

    def clear_cancelled(self) -> int:
        """Clear all cancelled downloads from history.

        Executes hooks:
        - download.before_clear: Can block
        - download.after_clear: Notification

        Returns:
            Number of downloads cleared

        Raises:
            DownloadOperationException: If clear is blocked
        """
        # Execute before_clear hook
        hook_data, blocked = execute_hook(self.plugins,
            DOWNLOAD_HOOKS.before_clear,
            {
                "clear_type": "cancelled"
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Clear blocked")
            logger.warning(f"Clear cancelled blocked by plugin: {reason}")
            raise DownloadOperationException(reason)

        count = self.repo.delete_cancelled()

        # Execute after_clear hook
        execute_hook(self.plugins,
            DOWNLOAD_HOOKS.after_clear,
            {
                "clear_type": "cancelled",
                "count": count
            }
        )

        logger.info(f"Cleared {count} cancelled downloads")
        return count

    # ========== Settings Operations ==========

    def get_settings(self) -> DownloadSettings:
        """Get current download settings.

        Returns:
            DownloadSettings object
        """
        return self.settings

    def update_settings(self, new_settings: DownloadSettings) -> DownloadSettings:
        """Update download settings.

        Note: Changes to max_concurrent_downloads require restart to take effect.

        Args:
            new_settings: New settings to apply

        Returns:
            Updated DownloadSettings object
        """
        self.settings = new_settings
        return self.settings
