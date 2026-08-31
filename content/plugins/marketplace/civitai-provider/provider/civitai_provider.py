"""
CivitAI Marketplace Provider Implementation.

This module implements the MarketplaceProviderBase interface for CivitAI,
providing model metadata lookup, search, and download URL functionality.
"""

import asyncio
import aiohttp
import logging
import time
from typing import Any, Dict, List, Optional

from src.plugin_api import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderSearchResult,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderNotFoundError,
    RemoteDownloadRef,
)

logger = logging.getLogger(__name__)


class CivitaiProvider(MarketplaceProviderBase):
    """
    CivitAI marketplace provider.

    Provides integration with CivitAI's API for:
    - Model lookup by SHA256 hash
    - Model search
    - Download URL retrieval
    - Preview media information
    """

    BASE_URL = "https://civitai.com/api/v1"

    def __init__(self):
        self._api_key: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_delay: float = 1.0
        self._last_request_time: float = 0
        self._download_media: bool = True
        self._max_media_files: int = 10
        self._headers: Dict[str, str] = {}
        self._initialized: bool = False

    def get_metadata(self) -> ProviderMetadata:
        """Get CivitAI provider metadata."""
        return ProviderMetadata(
            id="civitai",
            name="CivitAI",
            description="The largest AI model sharing platform. Browse and download Stable Diffusion models, LoRAs, embeddings, and more.",
            website="https://civitai.com",
            capabilities=[
                ProviderCapability.HASH_LOOKUP,
                ProviderCapability.SEARCH,
                ProviderCapability.DOWNLOAD_URL,
                ProviderCapability.MODEL_INFO,
                ProviderCapability.MEDIA_DOWNLOAD,
                ProviderCapability.PROMPT_FETCH,
                ProviderCapability.REMOTE_DOWNLOAD,
                ProviderCapability.REMOTE_DOWNLOAD_BY_HASH,
            ],
            icon=None,  # Could be base64 encoded icon
            version="1.0.0"
        )

    async def initialize(self, settings: Dict[str, Any]) -> bool:
        """
        Initialize the CivitAI provider with settings.

        Args:
            settings: Provider settings including api_key, rate_limit_delay, etc.

        Returns:
            True if initialization successful
        """
        self._api_key = settings.get('api_key')
        self._rate_limit_delay = float(settings.get('rate_limit_delay', 1.0))
        self._download_media = settings.get('download_media', 'true') == 'true' or settings.get('download_media') is True
        self._max_media_files = int(settings.get('max_media_files', 10))

        # Store headers for lazy session creation
        self._headers = {
            'User-Agent': 'PotionUI/1.0',
            'Content-Type': 'application/json'
        }

        if self._api_key:
            self._headers['Authorization'] = f'Bearer {self._api_key}'

        # Don't create session here - it will be created lazily on first request
        # to ensure it's in the correct async context
        self._initialized = True

        logger.info(f"CivitAI provider initialized (API key: {'configured' if self._api_key else 'not configured'})")
        return True

    async def shutdown(self) -> None:
        """Cleanup provider resources."""
        if self._session:
            await self._session.close()
            self._session = None
        self._initialized = False
        logger.info("CivitAI provider shutdown")

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create the HTTP session.

        Creates the session lazily on first use to ensure it's in the correct
        async context, avoiding "Timeout context manager should be used inside a task" errors.
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self._session

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        current_time = time.monotonic()
        time_since_last = current_time - self._last_request_time

        if time_since_last < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - time_since_last)

        self._last_request_time = time.monotonic()

    async def get_model_by_hash(self, sha256: str) -> Optional[ProviderModelInfo]:
        """
        Look up model information by SHA256 hash.

        Args:
            sha256: SHA256 hash of the model file

        Returns:
            ProviderModelInfo if found, None if not found
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        await self._rate_limit()

        url = f"{self.BASE_URL}/model-versions/by-hash/{sha256}"
        session = await self._get_session()

        try:
            logger.debug(f"Fetching CivitAI data for hash: {sha256}")

            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_model_version_response(data)

                elif response.status == 404:
                    logger.debug(f"Model not found on CivitAI for hash: {sha256}")
                    return None

                elif response.status == 429:
                    retry_after = response.headers.get('Retry-After', '60')
                    raise ProviderRateLimitError(
                        f"CivitAI rate limit exceeded",
                        retry_after=float(retry_after)
                    )

                else:
                    error_text = await response.text()
                    logger.warning(f"CivitAI API error {response.status}: {error_text}")
                    return None

        except asyncio.TimeoutError:
            raise ProviderConnectionError(f"Timeout fetching CivitAI data for hash: {sha256}")
        except aiohttp.ClientError as e:
            raise ProviderConnectionError(f"Connection error: {e}")

    async def search_models(
        self,
        query: str,
        model_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        **kwargs
    ) -> List[ProviderSearchResult]:
        """
        Search for models on CivitAI.

        Args:
            query: Search query string
            model_type: Optional filter by model type (Checkpoint, LORA, etc.)
            limit: Maximum results to return
            offset: Offset for pagination
            **kwargs: Additional parameters (nsfw, sort, period, etc.)

        Returns:
            List of search results
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        await self._rate_limit()

        # Build query parameters
        params = {
            'query': query,
            'limit': min(limit, 100),  # CivitAI max is 100
        }

        if offset:
            params['page'] = (offset // limit) + 1

        if model_type:
            # Map common types to CivitAI types
            type_map = {
                'checkpoint': 'Checkpoint',
                'lora': 'LORA',
                'embedding': 'TextualInversion',
                'vae': 'VAE',
                'controlnet': 'Controlnet',
                'upscaler': 'Upscaler',
            }
            params['types'] = type_map.get(model_type.lower(), model_type)

        # Handle NSFW filter
        if 'nsfw' in kwargs:
            params['nsfw'] = str(kwargs['nsfw']).lower()

        # Handle sort
        if 'sort' in kwargs:
            params['sort'] = kwargs['sort']  # Most Downloaded, Highest Rated, Newest

        url = f"{self.BASE_URL}/models"
        session = await self._get_session()

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_search_response(data)

                elif response.status == 429:
                    retry_after = response.headers.get('Retry-After', '60')
                    raise ProviderRateLimitError(
                        "CivitAI rate limit exceeded",
                        retry_after=float(retry_after)
                    )

                else:
                    logger.warning(f"CivitAI search error {response.status}")
                    return []

        except asyncio.TimeoutError:
            raise ProviderConnectionError("Timeout during CivitAI search")
        except aiohttp.ClientError as e:
            raise ProviderConnectionError(f"Connection error: {e}")

    async def get_download_url(
        self,
        provider_model_id: str,
        provider_version_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Get download URL for a model.

        Args:
            provider_model_id: CivitAI model ID
            provider_version_id: Optional version ID

        Returns:
            Download URL if available
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        await self._rate_limit()

        # If we have a version ID, we can construct the URL directly
        if provider_version_id:
            return f"https://civitai.com/api/download/models/{provider_version_id}"

        # Otherwise, fetch model info to get the latest version's download URL
        url = f"{self.BASE_URL}/models/{provider_model_id}"
        session = await self._get_session()

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    versions = data.get('modelVersions', [])
                    if versions:
                        # Get latest version's primary file download URL
                        latest = versions[0]
                        for file in latest.get('files', []):
                            if file.get('primary', False):
                                return file.get('downloadUrl')
                    return None

                elif response.status == 404:
                    raise ProviderNotFoundError(f"Model {provider_model_id} not found on CivitAI")

                else:
                    return None

        except asyncio.TimeoutError:
            raise ProviderConnectionError("Timeout getting download URL from CivitAI")
        except aiohttp.ClientError as e:
            raise ProviderConnectionError(f"Connection error: {e}")

    async def resolve_remote_download(
        self,
        provider_model_id: str,
        provider_version_id: Optional[str] = None
    ) -> RemoteDownloadRef:
        """
        Resolve a CivitAI model to the pre-signed CDN URL a remote worker
        can fetch without our API key.

        Reuses prepare_download's redirect dance. If it doesn't land off
        civitai.com (no API key, or the token-in-URL fallback), that's a
        resolution failure - never hand an API key to a remote worker.
        """
        url = await self.get_download_url(provider_model_id, provider_version_id)
        if not url:
            raise ProviderNotFoundError(f"No download URL for CivitAI model {provider_model_id}")

        session = await self._get_session()
        headers: Dict[str, str] = {}
        resolved_url = await self.prepare_download(session, url, headers)

        if 'civitai.com/api/download' in resolved_url:
            raise ProviderConnectionError(
                "CivitAI did not return a pre-signed CDN URL; check the API key"
            )

        return RemoteDownloadRef(url=resolved_url, headers={})

    async def resolve_remote_download_by_hash(self, sha256: str) -> RemoteDownloadRef:
        """
        Resolve a model to a remote-worker-fetchable URL by SHA256 alone.

        CivitAI's by-hash endpoint has been seen returning HTTP 200 with the
        wrong model-version record, so the returned record's own file list is
        searched for the file whose hash actually matches - never trust the
        first (or "primary") file. A version can list a file under an
        unrelated hash-lookup match while another file in the same list is
        the real one, so a lookup that returned no matching file is treated
        as a miss, not as "use whatever's there".
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        await self._rate_limit()

        url = f"{self.BASE_URL}/model-versions/by-hash/{sha256}"
        session = await self._get_session()

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 404:
                    raise ProviderNotFoundError(f"No CivitAI model found for hash {sha256}")
                if response.status == 429:
                    retry_after = response.headers.get('Retry-After', '60')
                    raise ProviderRateLimitError(
                        "CivitAI rate limit exceeded", retry_after=float(retry_after)
                    )
                if response.status != 200:
                    raise ProviderConnectionError(f"CivitAI by-hash lookup returned HTTP {response.status}")
                data = await response.json()
        except asyncio.TimeoutError:
            raise ProviderConnectionError(f"Timeout during CivitAI by-hash lookup for {sha256}")
        except aiohttp.ClientError as e:
            raise ProviderConnectionError(f"Connection error: {e}")

        file_info = self._matching_file(data.get('files', []), sha256)
        if file_info is None or not file_info.get('downloadUrl'):
            raise ProviderNotFoundError(f"CivitAI by-hash response for {sha256} has no matching file")

        session = await self._get_session()
        headers: Dict[str, str] = {}
        resolved_url = await self.prepare_download(session, file_info['downloadUrl'], headers)

        if 'civitai.com/api/download' in resolved_url:
            raise ProviderConnectionError(
                "CivitAI did not return a pre-signed CDN URL for a by-hash lookup; check the API key"
            )

        size_kb = file_info.get('sizeKB')
        size_hint = round(size_kb * 1024) if isinstance(size_kb, (int, float)) else None

        return RemoteDownloadRef(url=resolved_url, headers={}, size_hint=size_hint)

    @staticmethod
    def _matching_file(files: List[Dict[str, Any]], sha256: str) -> Optional[Dict[str, Any]]:
        target = sha256.lower()
        for file_info in files:
            file_hash = (file_info.get('hashes') or {}).get('SHA256')
            if file_hash and file_hash.lower() == target:
                return file_info
        return None

    def get_settings_schema(self) -> Dict[str, Any]:
        """Get JSON schema for CivitAI provider settings."""
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "API Key",
                    "format": "password",
                    "description": "Your CivitAI API key (optional but recommended for higher rate limits)"
                },
                "rate_limit_delay": {
                    "type": "number",
                    "title": "Rate Limit Delay (seconds)",
                    "description": "Delay between API requests to avoid rate limiting",
                    "default": 1.0,
                    "minimum": 0.5,
                    "maximum": 10.0
                },
                "download_media": {
                    "type": "boolean",
                    "title": "Download Preview Media",
                    "description": "Download preview images and videos when fetching model info",
                    "default": True
                },
                "max_media_files": {
                    "type": "integer",
                    "title": "Max Media Files",
                    "description": "Maximum number of preview media files to download per model",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50
                }
            },
            "required": []
        }

    async def _validate_model_id(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Validate that a model ID exists on CivitAI.

        Returns model data dict if valid, None if not found.
        """
        url = f"{self.BASE_URL}/models/{model_id}"
        session = await self._get_session()
        await self._rate_limit()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    return None
                else:
                    logger.warning(f"CivitAI model validation returned {response.status}")
                    return None
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"CivitAI model validation failed: {e}")
            return None

    async def _validate_model_version_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        """Validate that a model version ID exists on CivitAI.

        Returns version data dict if valid, None if not found.
        """
        url = f"{self.BASE_URL}/model-versions/{version_id}"
        session = await self._get_session()
        await self._rate_limit()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    return None
                else:
                    logger.warning(f"CivitAI version validation returned {response.status}")
                    return None
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"CivitAI version validation failed: {e}")
            return None

    async def fetch_image_prompts(
        self,
        model_id: Optional[str] = None,
        model_version_id: Optional[str] = None,
        sort: str = "Most Reactions",
        period: str = "AllTime",
        limit: int = 20,
        nsfw: bool = False,
        fetch_all: bool = False,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Fetch image generation prompts from CivitAI's /api/v1/images endpoint.

        Args:
            model_id: CivitAI model ID to scope results.
            model_version_id: CivitAI model version ID.
            sort: Sort order (Most Reactions, Most Comments, Newest).
            period: Time period (AllTime, Year, Month, Week, Day).
            limit: When fetch_all=False, max items per page. When fetch_all=True,
                max total items to collect.
            nsfw: Include NSFW images.
            fetch_all: If True, paginate through pages up to limit total items.

        Returns:
            List of dicts with prompt metadata ready for PromptDatabaseManager.
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        # Validate model_id exists on CivitAI before bulk-fetching.
        # CivitAI silently ignores invalid modelId and returns unfiltered results.
        if model_id and not model_version_id:
            logger.info(f"CivitAI: validating model_id={model_id} on CivitAI API")
            model_data = await self._validate_model_id(model_id)
            if model_data is None:
                # Try as version ID — users often confuse model vs version IDs
                logger.info(f"CivitAI: model_id={model_id} not found as model, trying as version ID")
                version_data = await self._validate_model_version_id(model_id)
                if version_data is not None:
                    logger.info(
                        f"CivitAI: {model_id} is a version ID, not model ID — adjusting"
                    )
                    model_version_id = model_id
                    model_id = None
                else:
                    logger.error(
                        f"CivitAI: model_id={model_id} not found as model or version on CivitAI"
                    )
                    raise ProviderNotFoundError(
                        f"Model ID {model_id} not found on CivitAI "
                        "(neither as model nor version)"
                    )
            else:
                logger.info(
                    f"CivitAI: model_id={model_id} validated, "
                    f"name={model_data.get('name', '?')}"
                )

        # When fetch_all=True, use max page size for efficiency and limit becomes max_total
        if fetch_all:
            page_size = 200
            max_total = limit
        else:
            page_size = limit
            max_total = limit

        all_items = await self._fetch_images_paginated(
            model_id=model_id,
            model_version_id=model_version_id,
            sort=sort,
            period=period,
            limit=page_size,
            nsfw=nsfw,
            fetch_all=fetch_all,
            max_total=max_total,
        )

        logger.info(f"CivitAI: fetched {len(all_items)} image prompts")
        return all_items

    async def _fetch_images_paginated(
        self,
        model_id: Optional[str] = None,
        model_version_id: Optional[str] = None,
        sort: str = "Most Reactions",
        period: str = "AllTime",
        limit: int = 20,
        nsfw: bool = False,
        fetch_all: bool = False,
        max_total: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Fetch images from CivitAI with pagination support.

        Args:
            max_total: Hard cap on total items to collect across all pages.

        Returns list of prompt dicts extracted from CivitAI image metadata.
        """
        params: Dict[str, Any] = {
            "limit": min(limit, 200),
            "sort": sort,
            "period": period,
        }
        if model_id:
            params["modelId"] = model_id
        if model_version_id:
            params["modelVersionId"] = model_version_id
        if nsfw:
            # browsingLevel is a bitmask: 1=SFW, 2=Soft, 4=Mature, 8=X — 31 includes all
            params["browsingLevel"] = 31
        else:
            params["nsfw"] = "None"

        url = f"{self.BASE_URL}/images"
        session = await self._get_session()
        all_items: List[Dict[str, Any]] = []
        logger.info(
            f"CivitAI _fetch_images_paginated: url={url}, params={params}, "
            f"fetch_all={fetch_all}, max_total={max_total}"
        )

        page_num = 0
        while url:
            page_num += 1
            await self._rate_limit()
            try:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    actual_url = str(response.url)
                    logger.info(f"CivitAI page {page_num}: GET {actual_url} -> status={response.status}")
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "60")
                        raise ProviderRateLimitError(
                            "CivitAI rate limit exceeded",
                            retry_after=float(retry_after),
                        )
                    if response.status != 200:
                        logger.warning(
                            f"CivitAI images endpoint returned {response.status}"
                        )
                        break

                    data = await response.json()
            except asyncio.TimeoutError:
                raise ProviderConnectionError("Timeout fetching CivitAI images")
            except aiohttp.ClientError as e:
                raise ProviderConnectionError(f"Connection error: {e}")

            items = data.get("items", [])
            skipped_no_meta = 0
            skipped_no_prompt = 0
            logger.info(f"CivitAI page {page_num}: received {len(items)} items")

            for item in items:
                outer_meta = item.get("meta") or {}
                if not item.get("meta"):
                    skipped_no_meta += 1
                    continue
                meta = outer_meta.get("meta") or outer_meta
                prompt_text = meta.get("prompt", "")
                if not prompt_text:
                    skipped_no_prompt += 1
                    meta_keys = list(outer_meta.keys())[:10]
                    logger.debug(
                        f"CivitAI: item {item.get('id')} has meta but no prompt. "
                        f"Meta keys: {meta_keys}"
                    )
                    continue

                stats = item.get("stats") or {}
                all_items.append({
                    "source_id": str(item.get("id", "")),
                    "prompt": prompt_text,
                    "negative_prompt": meta.get("negativePrompt"),
                    "model_name": meta.get("Model"),
                    "base_model": item.get("baseModel"),
                    "cfg_scale": meta.get("cfgScale"),
                    "steps": meta.get("steps"),
                    "sampler": meta.get("sampler"),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "stats": {
                        "heart_count": stats.get("heartCount", 0),
                        "like_count": stats.get("likeCount", 0),
                        "laugh_count": stats.get("laughCount", 0),
                        "cry_count": stats.get("cryCount", 0),
                        "comment_count": stats.get("commentCount", 0),
                    },
                    "tags": [],
                    "nsfw": item.get("nsfw", False) if isinstance(item.get("nsfw"), bool) else item.get("nsfwLevel", "None") != "None",
                    "source_url": f"https://civitai.com/images/{item.get('id', '')}",
                })

            logger.info(
                f"CivitAI page {page_num} summary: {len(items)} items, "
                f"{skipped_no_meta} skipped (no meta), "
                f"{skipped_no_prompt} skipped (no prompt in meta), "
                f"{len(items) - skipped_no_meta - skipped_no_prompt} usable. "
                f"Running total: {len(all_items)}"
            )

            # Check max_total cap
            if len(all_items) >= max_total:
                all_items = all_items[:max_total]
                logger.info(
                    f"CivitAI: reached max_total cap of {max_total} items, "
                    "stopping pagination"
                )
                break

            # Pagination
            next_cursor = data.get("metadata", {}).get("nextCursor")
            if fetch_all and next_cursor:
                params["cursor"] = next_cursor
            else:
                break

        return all_items

    async def test_connection(self) -> bool:
        """Test if the CivitAI connection is working."""
        if not self._initialized:
            return False

        try:
            # Try to fetch a simple endpoint
            url = f"{self.BASE_URL}/models"
            params = {'limit': 1}
            session = await self._get_session()

            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                return response.status == 200

        except Exception as e:
            logger.error(f"CivitAI connection test failed: {e}")
            return False

    def get_download_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers for downloading files from CivitAI.

        CivitAI download flow:
        1. Initial request to /api/download/models/{id} with Authorization header
        2. Server returns redirect to pre-signed URL
        3. Follow redirect (no auth needed, URL is pre-signed)
        """
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        if self._api_key:
            headers['Authorization'] = f'Bearer {self._api_key}'

        return headers

    def get_authenticated_download_url(self, url: str) -> str:
        """
        Get an authenticated download URL for CivitAI.

        CivitAI uses redirect-based authentication - the initial request needs
        Authorization header, then redirects to a pre-signed URL.
        No URL modification needed.
        """
        # CivitAI doesn't need token in URL - it uses header auth + redirect
        return url

    def matches_download_url(self, url: str) -> bool:
        """CivitAI owns civitai.com downloads."""
        return 'civitai.com' in url

    async def prepare_download(self, session, url: str, headers: Dict[str, str]) -> str:
        """
        Resolve a CivitAI download to the pre-signed CDN URL the worker
        should stream from.

        Flow: request /api/download/... with the Authorization header and no
        auto-redirect. A valid redirect points at the pre-signed CDN URL
        (followed WITHOUT the auth header). A redirect back to /login means
        header auth was rejected - retry with the token as a query parameter
        (some CivitAI edges only accept that), again resolving the redirect.
        """
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        headers.update(self.get_download_headers())

        if 'civitai.com/api/download' not in url or not self._api_key:
            if 'civitai.com/api/download' in url and not self._api_key:
                logger.warning("CivitAI download attempted without API key configured")
            return url

        api_token = self._api_key
        download_url = url

        logger.info("Downloading from CivitAI with authentication")

        def is_valid_cdn_redirect(redirect_url: str) -> bool:
            """Check if redirect URL is a valid CDN URL."""
            if not redirect_url:
                return False
            if '/login' in redirect_url or 'reason=download-auth' in redirect_url:
                return False
            return True

        def make_absolute_url(redirect_url: str, base_url: str) -> str:
            if redirect_url.startswith('/'):
                parsed = urlparse(base_url)
                return f"{parsed.scheme}://{parsed.netloc}{redirect_url}"
            return redirect_url

        # First attempt: Authorization header with redirect handling
        async with session.get(
            download_url,
            headers=headers,
            allow_redirects=False
        ) as initial_response:
            if initial_response.status in (301, 302, 303, 307, 308):
                redirect_url = initial_response.headers.get('Location', '')

                if is_valid_cdn_redirect(redirect_url):
                    # Valid redirect to CDN - follow it without auth header
                    download_url = make_absolute_url(redirect_url, download_url)
                    headers.pop('Authorization', None)
                    logger.debug("Following CivitAI CDN redirect")
                else:
                    # Auth failed - try token in URL as fallback
                    logger.debug("Trying CivitAI token-in-URL fallback")

                    parsed = urlparse(download_url)
                    query_params = parse_qs(parsed.query)
                    query_params['token'] = [api_token]
                    new_query = urlencode(query_params, doseq=True)
                    token_url = urlunparse((
                        parsed.scheme, parsed.netloc, parsed.path,
                        parsed.params, new_query, parsed.fragment
                    ))
                    headers.pop('Authorization', None)

                    # Make request with token in URL and handle redirect
                    async with session.get(
                        token_url,
                        headers=headers,
                        allow_redirects=False
                    ) as token_response:
                        if token_response.status in (301, 302, 303, 307, 308):
                            token_redirect = token_response.headers.get('Location', '')

                            if is_valid_cdn_redirect(token_redirect):
                                download_url = make_absolute_url(token_redirect, token_url)
                                logger.debug("Following CivitAI CDN redirect from token-in-URL")
                            else:
                                raise Exception(
                                    "CivitAI authentication failed. Both Authorization header and token query parameter "
                                    "were rejected. Please verify your API key is valid at https://civitai.com/user/account"
                                )
                        elif token_response.status == 200:
                            content_type = token_response.headers.get('Content-Type', '')
                            if 'text/html' in content_type:
                                raise Exception(
                                    "CivitAI returned login page instead of model file. "
                                    "API key may be invalid or this model requires additional permissions."
                                )
                            download_url = token_url
                        else:
                            raise Exception(f"CivitAI token-in-URL error: HTTP {token_response.status}")

            elif initial_response.status == 200:
                content_type = initial_response.headers.get('Content-Type', '')
                if 'text/html' in content_type:
                    raise Exception(
                        "CivitAI returned login page instead of model file. "
                        "API key may be invalid or this model requires additional permissions."
                    )
            elif initial_response.status == 401:
                raise Exception("CivitAI API key is invalid or expired")
            else:
                raise Exception(f"CivitAI error: HTTP {initial_response.status}")

        return download_url

    def _parse_model_version_response(self, data: Dict[str, Any]) -> ProviderModelInfo:
        """
        Parse CivitAI model version API response into ProviderModelInfo.

        Args:
            data: CivitAI API response for model-versions endpoint

        Returns:
            ProviderModelInfo with parsed data
        """
        # Model info is nested
        model_info = data.get('model', {})

        # Extract media URLs (limited to max_media_files)
        media_urls = []
        for image in data.get('images', [])[:self._max_media_files]:
            if image.get('url'):
                media_urls.append(image['url'])
            if image.get('videoUrl'):
                media_urls.append(image['videoUrl'])
            if image.get('meta') and image['meta'].get('video'):
                media_urls.append(image['meta']['video'])

        # Extract tags from trainedWords
        tags = data.get('trainedWords', [])

        # Extract download URL from primary file
        download_url = None
        for file_info in data.get('files', []):
            if file_info.get('primary', False):
                download_url = file_info.get('downloadUrl')
                break

        # Get description (version level or model level)
        description = data.get('description') or model_info.get('description')

        # Determine model type
        model_type = model_info.get('type', '').lower()

        # Determine base model
        base_model = data.get('baseModel')

        return ProviderModelInfo(
            provider_id="civitai",
            provider_model_id=str(model_info.get('id', data.get('modelId', ''))),
            provider_version_id=str(data.get('id', '')),
            name=model_info.get('name', ''),
            description=description,
            tags=tags,
            nsfw=model_info.get('nsfw', False),
            download_url=download_url,
            media_urls=media_urls,
            model_type=model_type,
            base_model=base_model,
            extra_data={
                'civitai_model_url': f"https://civitai.com/models/{model_info.get('id')}",
                'civitai_version_url': f"https://civitai.com/models/{model_info.get('id')}?modelVersionId={data.get('id')}",
            }
        )

    def _parse_search_response(self, data: Dict[str, Any]) -> List[ProviderSearchResult]:
        """
        Parse CivitAI search API response into list of ProviderSearchResult.

        Args:
            data: CivitAI API response for models endpoint

        Returns:
            List of search results
        """
        results = []

        for item in data.get('items', []):
            # Get first version for download URL
            versions = item.get('modelVersions', [])
            download_url = None
            if versions:
                for file in versions[0].get('files', []):
                    if file.get('primary', False):
                        download_url = file.get('downloadUrl')
                        break

            # Get thumbnail from first version's images
            thumbnail_url = None
            if versions:
                images = versions[0].get('images', [])
                if images:
                    thumbnail_url = images[0].get('url')

            results.append(ProviderSearchResult(
                provider_id="civitai",
                provider_model_id=str(item.get('id', '')),
                name=item.get('name', ''),
                thumbnail_url=thumbnail_url,
                download_url=download_url,
                model_type=item.get('type', '').lower(),
                rating=item.get('stats', {}).get('rating'),
                downloads=item.get('stats', {}).get('downloadCount'),
                nsfw=item.get('nsfw', False),
            ))

        return results

    # === Backward Compatibility Methods ===
    # These methods maintain compatibility with the existing CivitaiService

    def parse_civitai_response(self, data: Dict[str, Any]) -> tuple:
        """
        Parse CivitAI API response (backward compatibility).

        Returns:
            Tuple of (model_info_dict, media_urls)
        """
        provider_info = self._parse_model_version_response(data)

        # Convert to the format expected by existing code
        from src.plugin_api import ModelInfo

        model_info = ModelInfo(
            provider='civitai',
            provider_model_id=provider_info.provider_model_id,
            provider_version_id=provider_info.provider_version_id,
            name=provider_info.name,
            description=provider_info.description,
            tags=provider_info.tags,
            nsfw=provider_info.nsfw,
            download_url=provider_info.download_url,
        )

        return model_info, provider_info.media_urls
