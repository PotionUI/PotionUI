"""
HuggingFace Hub Marketplace Provider Implementation.

This module implements the MarketplaceProviderBase interface for the HuggingFace
Hub, providing model search, download URL resolution, and authenticated
download support for gated/private repos.

HuggingFace has no hash-reverse-index API (unlike CivitAI's
`/model-versions/by-hash/{sha256}`), so `get_model_by_hash` always returns
None and HASH_LOOKUP is intentionally not advertised in capabilities.

Ref shape
---------
HuggingFace models are repos containing many files, not a single
"model version" the way CivitAI models are. To fit the
`get_download_url(provider_model_id, provider_version_id)` two-string
interface, this provider uses:

  provider_model_id   = "{org}/{repo}"          e.g. "black-forest-labs/FLUX.1-dev"
  provider_version_id = "{revision}@{filepath}" e.g. "main@flux1-dev.safetensors"

If `provider_version_id` has no "@", the whole string is treated as the
filepath and the revision defaults to "main". A HuggingFace repo has no
notion of a single "primary" file, so `provider_version_id` being omitted
entirely is not resolvable deterministically - `get_download_url` raises
`ProviderNotFoundError` naming the repo rather than guessing one.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp

from src.plugin_api import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderSearchResult,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderNotFoundError,
)

logger = logging.getLogger(__name__)

# Model-type -> HuggingFace Hub API `filter` tag. Best-effort mapping; HF's
# tagging is looser than CivitAI's fixed type enum, so unmapped types are
# passed straight through to `filter` unchanged.
_TYPE_FILTER_MAP = {
    'checkpoint': 'diffusers',
    'lora': 'lora',
    'embedding': 'textual_inversion',
    'vae': 'diffusers',
    'controlnet': 'controlnet',
}


class HuggingFaceProvider(MarketplaceProviderBase):
    """
    HuggingFace Hub marketplace provider.

    Provides integration with the HuggingFace Hub API for:
    - Model search
    - Download URL resolution (public, gated, and private repos)
    - Authenticated downloads via Bearer token

    Not supported (HuggingFace has no equivalent API):
    - Hash-based model lookup (`get_model_by_hash` always returns None)
    """

    BASE_URL = "https://huggingface.co"
    API_URL = "https://huggingface.co/api"

    def __init__(self):
        self._api_key: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_delay: float = 0.5
        self._last_request_time: float = 0
        self._headers: Dict[str, str] = {}
        self._initialized: bool = False

    def get_metadata(self) -> ProviderMetadata:
        """Get HuggingFace provider metadata."""
        return ProviderMetadata(
            id="huggingface",
            name="HuggingFace",
            description="The largest open platform for AI models. Browse and download "
                        "diffusion checkpoints, LoRAs, VAEs, and more.",
            website="https://huggingface.co",
            capabilities=[
                ProviderCapability.SEARCH,
                ProviderCapability.DOWNLOAD_URL,
                ProviderCapability.MODEL_INFO,
            ],
            icon=None,
            version="1.0.0"
        )

    async def initialize(self, settings: Dict[str, Any]) -> bool:
        """
        Initialize the HuggingFace provider with settings.

        Args:
            settings: Provider settings including api_key, rate_limit_delay.

        Returns:
            True if initialization successful
        """
        self._api_key = settings.get('api_key') or None
        try:
            self._rate_limit_delay = float(settings.get('rate_limit_delay', 0.5))
        except (TypeError, ValueError):
            self._rate_limit_delay = 0.5

        self._headers = {
            'User-Agent': 'PotionUI/1.0',
        }
        if self._api_key:
            self._headers['Authorization'] = f'Bearer {self._api_key}'

        # Don't create session here - it will be created lazily on first request
        # to ensure it's in the correct async context.
        self._initialized = True

        logger.info(f"HuggingFace provider initialized (token: {'configured' if self._api_key else 'not configured'})")
        return True

    async def shutdown(self) -> None:
        """Cleanup provider resources."""
        if self._session:
            await self._session.close()
            self._session = None
        self._initialized = False
        logger.info("HuggingFace provider shutdown")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the HTTP session lazily, in the correct async context."""
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

    # === Ref parsing ===

    @staticmethod
    def _parse_version_id(provider_version_id: str) -> tuple:
        """Split a `provider_version_id` into (revision, filepath).

        Format is "{revision}@{filepath}"; if there's no "@", the whole
        string is the filepath and revision defaults to "main".
        """
        if "@" in provider_version_id:
            revision, _, filepath = provider_version_id.partition("@")
            return (revision or "main", filepath)
        return ("main", provider_version_id)

    @staticmethod
    def _build_resolve_url(repo: str, revision: str, filepath: str) -> str:
        """Build a huggingface.co/{repo}/resolve/{revision}/{file} download URL."""
        # Each path segment is quoted independently so "/" in the filepath
        # (subdirectories inside the repo) is preserved.
        quoted_file = "/".join(quote(part) for part in filepath.split("/"))
        return f"{HuggingFaceProvider.BASE_URL}/{repo}/resolve/{revision}/{quoted_file}"

    def matches_url(self, url: str) -> bool:
        """Check whether a URL belongs to this provider (huggingface.co)."""
        return "huggingface.co" in url

    # === Capability: HASH_LOOKUP (unsupported) ===

    async def get_model_by_hash(self, sha256: str) -> Optional[ProviderModelInfo]:
        """
        HuggingFace has no hash-reverse-index API. Always returns None.

        Args:
            sha256: SHA256 hash of the model file

        Returns:
            Always None - not supported by the HuggingFace Hub API.
        """
        logger.debug(
            f"HuggingFace provider does not support hash lookup (requested sha256={sha256[:8]}...)"
        )
        return None

    # === Capability: SEARCH ===

    async def search_models(
        self,
        query: str,
        model_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        **kwargs
    ) -> List[ProviderSearchResult]:
        """
        Search for models on the HuggingFace Hub.

        Args:
            query: Search query string
            model_type: Optional filter, mapped to HF's `filter` tag param
            limit: Maximum results to return
            offset: Offset for pagination (best-effort - the HF models API has
                no native page/offset param, so `limit + offset` results are
                fetched and sliced client-side)
            **kwargs: Additional parameters (sort, direction)

        Returns:
            List of search results
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        await self._rate_limit()

        params: Dict[str, Any] = {
            'search': query,
            'limit': min(offset + limit, 1000),
            'full': 'true',
        }

        if model_type:
            params['filter'] = _TYPE_FILTER_MAP.get(model_type.lower(), model_type)

        if 'sort' in kwargs:
            params['sort'] = kwargs['sort']
        if 'direction' in kwargs:
            params['direction'] = kwargs['direction']

        url = f"{self.API_URL}/models"
        session = await self._get_session()

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    results = self._parse_search_response(data)
                    return results[offset:offset + limit]

                elif response.status == 429:
                    retry_after = response.headers.get('Retry-After', '60')
                    raise ProviderRateLimitError(
                        "HuggingFace rate limit exceeded",
                        retry_after=float(retry_after)
                    )

                else:
                    logger.warning(f"HuggingFace search error {response.status}")
                    return []

        except asyncio.TimeoutError:
            raise ProviderConnectionError("Timeout during HuggingFace search")
        except aiohttp.ClientError as e:
            raise ProviderConnectionError(f"Connection error: {e}")

    # === Capability: DOWNLOAD_URL ===

    async def get_download_url(
        self,
        provider_model_id: str,
        provider_version_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Get download URL for a file in a HuggingFace repo.

        Args:
            provider_model_id: HuggingFace repo id ("org/repo")
            provider_version_id: "{revision}@{filepath}" (see module docstring).
                Required - a repo has no single "primary" file to fall back to.

        Returns:
            Download URL if resolvable.

        Raises:
            ProviderNotFoundError: If no file was specified, or the repo (or
                resolved file) doesn't exist.
        """
        if not self._initialized:
            raise ProviderConnectionError("Provider not initialized")

        if not provider_version_id:
            raise ProviderNotFoundError(
                f"No file specified for HuggingFace repo {provider_model_id} - "
                "a repo has no single primary file, so one must be named explicitly"
            )

        revision, filepath = self._parse_version_id(provider_version_id)
        if not filepath:
            raise ProviderNotFoundError(
                f"No file specified in provider_version_id for {provider_model_id}"
            )
        return self._build_resolve_url(provider_model_id, revision, filepath)

    def get_settings_schema(self) -> Dict[str, Any]:
        """Get JSON schema for HuggingFace provider settings."""
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "Access Token",
                    "format": "password",
                    "description": "Your HuggingFace access token (optional - only needed for "
                                    "gated or private repos)"
                },
                "rate_limit_delay": {
                    "type": "number",
                    "title": "Rate Limit Delay (seconds)",
                    "description": "Delay between API requests to avoid rate limiting",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 10.0
                },
            },
            "required": []
        }

    async def test_connection(self) -> bool:
        """Test if the HuggingFace connection (and token, if configured) is working."""
        if not self._initialized:
            return False

        session = await self._get_session()
        try:
            if self._api_key:
                url = f"{self.API_URL}/whoami-v2"
            else:
                url = f"{self.API_URL}/models"

            params = {} if self._api_key else {'limit': 1}

            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                return response.status == 200

        except Exception as e:
            logger.error(f"HuggingFace connection test failed: {e}")
            return False

    def get_download_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers for downloading files from HuggingFace.

        Unlike CivitAI, HuggingFace's resolve URLs accept the Authorization
        header directly (no redirect-based pre-signed URL dance) for both
        gated and private repos.
        """
        headers = {
            'User-Agent': 'PotionUI/1.0',
        }

        if self._api_key:
            headers['Authorization'] = f'Bearer {self._api_key}'

        return headers

    def get_authenticated_download_url(self, url: str) -> str:
        """
        Get an authenticated download URL for HuggingFace.

        HuggingFace uses header-based auth only - no URL modification needed.
        """
        return url

    def matches_download_url(self, url: str) -> bool:
        """HuggingFace owns huggingface.co downloads (incl. its CDN resolve URLs)."""
        return 'huggingface.co' in url

    def _parse_search_response(self, data: List[Dict[str, Any]]) -> List[ProviderSearchResult]:
        """
        Parse HuggingFace Hub `/api/models` search response into search results.

        Args:
            data: List of model dicts from the HF Hub API

        Returns:
            List of search results
        """
        results = []

        for item in data:
            repo_id = item.get('id') or item.get('modelId', '')
            if not repo_id:
                continue

            thumbnail_url = None
            for sibling in item.get('siblings', []) or []:
                filename = sibling.get('rfilename', '')
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    thumbnail_url = f"{self.BASE_URL}/{repo_id}/resolve/main/{quote(filename)}"
                    break

            tags = item.get('tags', []) or []
            model_type = None
            for candidate in ('lora', 'controlnet', 'textual_inversion', 'diffusers'):
                if candidate in tags:
                    model_type = candidate
                    break

            results.append(ProviderSearchResult(
                provider_id="huggingface",
                provider_model_id=repo_id,
                name=repo_id,
                thumbnail_url=thumbnail_url,
                download_url=None,  # resolved on demand via get_download_url (file not chosen yet)
                model_type=model_type,
                rating=None,
                downloads=item.get('downloads'),
                nsfw=False,
            ))

        return results
