"""Long-running model jobs: downloading a model file and generating video thumbnails.

`run_download_and_index` fetches an arbitrary provider-supplied URL server-side
(through the core download queue), so it guards that URL against SSRF before
queueing the request (see `is_safe_download_url`).
"""

import asyncio
import ipaddress
import logging
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple
from urllib.parse import urlparse

from src.features.models.exceptions import ModelDownloadException
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.indexer import ModelScanner
from src.features.models.repository import ModelRepository
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY
from src.platform.plugins import PluginRegistry

if TYPE_CHECKING:
    from src.features.downloads import DownloadManager

logger = logging.getLogger(__name__)


# Type directory mapping for downloads. A download for a type missing here
# falls back to 'checkpoints' - where the scanner indexes it under the wrong
# type and every picker filtering on the right one comes up empty (this
# happened for detection_segm until 2026-07-27) - so this is the canonical
# type->directory mapping, not a subset of it.
TYPE_DIR_MAP = MODEL_TYPE_TO_DIRECTORY

# How often to poll the download queue for a queued fetch's completion.
DOWNLOAD_POLL_INTERVAL_SECONDS = 2.0


def is_safe_download_url(url: str) -> Tuple[bool, str]:
    """Whether `url` is safe to fetch server-side, and why not if it isn't.

    A download URL arrives from a marketplace provider and is fetched by the
    server, so it is an SSRF vector: without checks it could point the server at
    its own loopback interface, a private-network host, or the cloud metadata
    endpoint. This requires an http(s) scheme and rejects any URL whose host
    resolves to a non-public address (loopback, private, link-local - which
    covers 169.254.169.254 - reserved, multicast or unspecified).
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False, f"unsupported URL scheme '{parsed.scheme}' (only http/https allowed)"

    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"could not resolve host '{host}': {exc}"

    for info in addr_infos:
        raw_addr = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"host '{host}' resolves to a non-public address ({raw_addr})"

    return True, ""


class ModelJobs:
    """Background jobs that produce or fetch model files and their thumbnails."""

    def __init__(
        self,
        model_repository: ModelRepository,
        plugin_registry: PluginRegistry,
        scanner: ModelScanner,
        download_manager: "DownloadManager",
    ):
        self.model_repo = model_repository
        self.plugins = plugin_registry
        self.scanner = scanner
        self.downloads = download_manager

    def start_thumbnail_generation(
        self,
        model_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Announce a background video-thumbnail generation run."""
        models_count = len(model_ids) if model_ids else "all"
        return {
            "message": f"Started video thumbnail generation for {models_count} models",
            "status": "started"
        }

    async def run_thumbnail_generation(
        self,
        model_ids: Optional[List[str]] = None
    ) -> None:
        """Generate thumbnails from videos for models missing images (background task)."""
        try:
            logger.info(f"Starting video thumbnail generation for models: {model_ids if model_ids else 'all'}")

            from src.features.models.images import generate_missing_thumbnails_from_videos
            result = await generate_missing_thumbnails_from_videos(model_ids=model_ids)

            logger.info(f"Video thumbnail generation completed: {result}")

        except Exception as e:
            logger.error(f"Error in video thumbnail generation background task: {e}")

    def start_download_and_index(
        self,
        name: str,
        link: str,
        size: str,
        sha256: str,
        model_type: str = 'checkpoint'
    ) -> Dict[str, Any]:
        """Announce a background download+index, letting a plugin veto it first.

        Fires model_index.before_download (can block). Raises ModelDownloadException
        if a plugin blocks; the caller schedules `run_download_and_index` on success.
        """
        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_download,
            {
                "name": name,
                "link": link,
                "size": size,
                "sha256": sha256,
                "model_type": model_type
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Download blocked by plugin")
            raise ModelDownloadException(reason)

        logger.info(f"Starting download and indexing for model: {name}")
        return {
            "message": f"Download and indexing started for {name}",
            "status": "started",
            "model_name": name
        }

    async def run_download_and_index(
        self,
        name: str,
        link: str,
        sha256: str,
        model_type: str = 'checkpoint'
    ) -> None:
        """Fetch a model file through the download queue, then index it
        (background task).

        Refuses SSRF-unsafe URLs before queueing anything; the queue's worker
        handles the byte download and SHA256 verification, so the fetch shows
        up in the admin download history like every other model fetch. Fires
        model_index.after_download once the file is indexed.
        """
        try:
            # The URL is provider-supplied and fetched server-side; refuse anything
            # that could redirect the request at our own network before touching it.
            safe, reason = is_safe_download_url(link)
            if not safe:
                logger.error(f"Refusing to download {name}: {reason}")
                return

            # Get model directory from settings
            from src.platform.settings.repository import SettingRepository
            setting_repo = SettingRepository()
            model_dir_setting = setting_repo.get_setting_by_key('models_dir')
            models_dir = Path(model_dir_setting.get_typed_value() if model_dir_setting else "models")

            # Determine target directory based on model type
            target_dir = models_dir / TYPE_DIR_MAP.get(model_type, 'checkpoints')

            # Extract filename from URL or use name
            parsed_url = urlparse(link)
            filename = Path(parsed_url.path).name or f"{name}.safetensors"

            logger.debug(f"Downloading {name} to {target_dir / filename}")

            download = await self.downloads.queue_model_download(
                url=link,
                destination_dir=str(target_dir),
                filename=filename,
                checksum_sha256=sha256 or None,
            )

            from src.features.downloads.models import DownloadStatus

            while True:
                await asyncio.sleep(DOWNLOAD_POLL_INTERVAL_SECONDS)
                current = self.downloads.get_download(download.id)
                if current.status == DownloadStatus.COMPLETED:
                    break
                if current.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                    logger.error(
                        f"Failed to download {name}: {current.error_message or current.status.value}"
                    )
                    return

            # The worker may have discovered the real filename from the
            # provider-resolved URL - index whatever actually landed.
            file_path = Path(current.destination_path)
            logger.debug(f"Downloaded {name} ({file_path.stat().st_size} bytes)")

            # Index the downloaded model
            logger.debug(f"Indexing {name}")
            file_size = file_path.stat().st_size
            model = self.scanner.index_single_model(str(file_path), model_type, file_size)

            if model:
                logger.info(f"Successfully downloaded and indexed {name}")
                execute_hook(
                    self.plugins,
                    MODEL_INDEX_HOOKS.after_download,
                    {
                        "name": name,
                        "model_id": model.id,
                        "file_path": str(file_path)
                    }
                )
            else:
                logger.error(f"Failed to index {name}")

        except Exception as e:
            logger.error(f"Error in download and index background task: {e}", exc_info=True)
