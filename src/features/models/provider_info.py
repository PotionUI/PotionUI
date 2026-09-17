"""Fetches marketplace metadata for indexed models via the provider registry."""

import logging
import uuid
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.features.models.exceptions import ProviderFetchException
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.metadata_editor import ModelMetadataEditor
from src.features.models.records import ModelInfo
from src.features.models.repository import ModelRepository
from src.platform.filesystem.storage_driver import FileStorageDriver, LocalFileStorageDriver
from src.platform.plugins import PluginRegistry

logger = logging.getLogger(__name__)

MAX_PROVIDER_PREVIEW_MEDIA = 10

_PREVIEW_MEDIA_TIMEOUT_SECONDS = 30
_PREVIEW_MEDIA_MAX_BYTES = 25 * 1024 * 1024

_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp'}
_VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.m4v'}
_DEFAULT_EXTENSION = {'image': '.jpg', 'video': '.mp4', 'audio': '.mp3'}


def _infer_preview_type(url: str, content_type: Optional[str]) -> Optional[str]:
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return 'image'
    if suffix in _VIDEO_EXTENSIONS:
        return 'video'
    if content_type:
        head = content_type.split('/', 1)[0].strip().lower()
        if head in ('image', 'video', 'audio'):
            return head
    return None


class ProviderInfoFetcher:
    """Enriches models with provider metadata, matched by SHA256 through the registry.

    A provider (e.g. civitai) is a plugin; this class asks the provider registry
    to resolve each model's hash to marketplace metadata and stores the result.
    """

    def __init__(
        self,
        model_repository: ModelRepository,
        plugin_registry: PluginRegistry,
        metadata_editor: Optional[ModelMetadataEditor] = None,
        storage_driver: Optional[FileStorageDriver] = None,
    ):
        self.model_repo = model_repository
        self.plugins = plugin_registry
        self.metadata_editor = metadata_editor
        self.storage_driver = storage_driver

    def fetch_provider_info(
        self,
        provider: str,
        model_ids: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Validate the provider and announce a background fetch.

        Fires model_index.before_fetch_info (can block). Raises
        ProviderFetchException if the provider is missing, not initialised, or vetoed.
        """
        from src.features.providers.registry import get_provider_registry

        provider_registry = get_provider_registry()
        provider_instance = provider_registry.get_provider(provider)

        if not provider_instance:
            raise ProviderFetchException(
                f"Provider '{provider}' not found. Please install and configure the provider plugin in Providers settings."
            )

        if not provider_registry.is_provider_initialized(provider):
            raise ProviderFetchException(
                f"Provider '{provider}' is not initialized. Please configure API key and other settings in Providers settings."
            )

        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_fetch_info,
            {
                "provider": provider,
                "model_ids": model_ids,
                "force_refresh": force_refresh
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Provider fetch blocked by plugin")
            raise ProviderFetchException(reason)

        logger.info(f"Using provider plugin for {provider}")

        return {
            "message": f"{provider.title()} info fetch started in background",
            "status": "running",
            "provider": provider,
            "models_to_process": len(model_ids) if model_ids else "all_without_info"
        }

    async def run_provider_fetch(
        self,
        provider: str,
        model_ids: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> Dict[str, int]:
        """Execute the actual provider fetch (background task); fires after_fetch_info."""
        try:
            from src.features.providers.registry import get_provider_registry

            provider_registry = get_provider_registry()

            # Get models to process
            if model_ids:
                models = [self.model_repo.get_by_id(mid, include_providers=False) for mid in model_ids]
                models = [m for m in models if m]
            elif force_refresh:
                models = self.model_repo.get_all(include_providers=False, limit=None)
            else:
                models = self.model_repo.get_models_without_provider_info(provider)

            successful = 0
            failed = 0

            for model in models:
                if getattr(model, 'is_directory', False):
                    # HF-layout checkpoints are local-only for now: their
                    # `sha256` is a directory fingerprint, not a content hash a
                    # marketplace could ever match.
                    logger.debug(f"Model {model.id} is an HF-layout directory, skipping provider fetch")
                    continue

                if not model.sha256:
                    logger.warning(f"Model {model.id} has no SHA256 hash, skipping")
                    failed += 1
                    continue

                try:
                    model_info = await provider_registry.get_model_by_hash(
                        provider, model.sha256
                    )
                    if model_info:
                        # Store provider info in database
                        db_model_info = ModelInfo(
                            model_id=model.id,
                            provider=model_info.provider_id,
                            provider_model_id=model_info.provider_model_id,
                            provider_version_id=model_info.provider_version_id,
                            name=model_info.name,
                            description=model_info.description,
                            tags=model_info.tags,
                            nsfw=model_info.nsfw,
                            download_url=model_info.download_url,
                        )
                        self.model_repo.upsert_provider(model.id, db_model_info)
                        successful += 1
                        if model_info.description and not model.description:
                            self.model_repo.update_description(model.id, model_info.description)
                        if model_info.media_urls:
                            await self._attach_provider_previews(
                                model.id, model_info.media_urls
                            )
                    else:
                        failed += 1
                except Exception as e:
                    logger.error(f"Error fetching info for model {model.id}: {e}")
                    failed += 1

            logger.info(f"Provider fetch completed: {successful} successful, {failed} failed")

            execute_hook(
                self.plugins,
                MODEL_INDEX_HOOKS.after_fetch_info,
                {
                    "provider": provider,
                    "successful": successful,
                    "failed": failed
                }
            )
            return {"successful": successful, "failed": failed}

        except Exception as e:
            logger.error(f"Error during background provider fetch: {e}")
            return {"successful": 0, "failed": len(model_ids or [])}

    def _resolve_storage_driver(self) -> Optional[FileStorageDriver]:
        if self.storage_driver is not None:
            return self.storage_driver
        if self.metadata_editor is not None:
            return LocalFileStorageDriver(self.metadata_editor.settings.get_file_storage_directory())
        return None

    async def _attach_provider_previews(
        self, model_id: str, media_urls: List[str]
    ) -> None:
        if self.metadata_editor is None:
            return
        driver = self._resolve_storage_driver()
        if driver is None:
            return

        if self.metadata_editor.list_model_previews(model_id):
            return

        for url in media_urls[:MAX_PROVIDER_PREVIEW_MEDIA]:
            try:
                await self._store_preview_from_url(model_id, url, driver)
            except Exception as e:
                logger.warning(f"Failed to store preview media for model {model_id} from {url}: {e}")

    async def _store_preview_from_url(self, model_id: str, url: str, driver: FileStorageDriver) -> None:
        fetched = await self._fetch_media_bytes(url)
        if fetched is None:
            return
        data, content_type = fetched

        media_type = _infer_preview_type(url, content_type)
        if media_type is None:
            logger.debug(f"Could not determine media type for preview URL {url}, skipping")
            return

        suffix = PurePosixPath(urlparse(url).path).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS and suffix not in _VIDEO_EXTENSIONS:
            suffix = _DEFAULT_EXTENSION.get(media_type, '')

        key = f"models/previews/{model_id}/{uuid.uuid4().hex}{suffix}"
        driver.put_bytes(key, data)
        self.metadata_editor.add_model_preview(model_id, {'source_path': key, 'type': media_type})

    async def _fetch_media_bytes(self, url: str) -> Optional[Tuple[bytes, Optional[str]]]:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=_PREVIEW_MEDIA_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.debug(f"Preview media fetch got HTTP {response.status} for {url}")
                    return None

                content_length = response.headers.get('Content-Length')
                if content_length and int(content_length) > _PREVIEW_MEDIA_MAX_BYTES:
                    logger.debug(f"Preview media at {url} exceeds size cap, skipping")
                    return None

                chunks = []
                total = 0
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > _PREVIEW_MEDIA_MAX_BYTES:
                        logger.debug(f"Preview media at {url} exceeded size cap mid-stream, skipping")
                        return None
                    chunks.append(chunk)

                return b''.join(chunks), response.headers.get('Content-Type')
