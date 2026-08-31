"""
Base classes and interfaces for marketplace providers.

This module defines the abstract interface that all marketplace providers must implement,
along with data classes for provider metadata, model information, and search results.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional, TypedDict


class ProviderCapability(Enum):
    """Capabilities that a marketplace provider can support."""

    HASH_LOOKUP = auto()       # Can lookup models by SHA256 hash
    SEARCH = auto()            # Can search for models by query
    DOWNLOAD_URL = auto()      # Can provide download URLs
    MODEL_INFO = auto()        # Can provide detailed model information
    MEDIA_DOWNLOAD = auto()    # Can download preview media (images/videos)
    PROMPT_FETCH = auto()      # Can import browse-only prompt examples
    API_KEY_REQUIRED = auto()  # Requires API key for authentication
    REMOTE_DOWNLOAD = auto()   # Can resolve a credential-free URL a remote worker can fetch
    REMOTE_DOWNLOAD_BY_HASH = auto()  # Can resolve that URL from a SHA256 alone, no provider link needed


class ProviderError(Exception):
    """Base exception for provider errors."""
    pass


class ProviderConnectionError(ProviderError):
    """Raised when connection to provider fails."""
    pass


class ProviderRateLimitError(ProviderError):
    """Raised when provider rate limit is exceeded."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderNotFoundError(ProviderError):
    """Raised when requested resource is not found on provider."""
    pass


@dataclass
class ProviderMetadata:
    """Metadata describing a marketplace provider."""

    id: str                          # Unique provider identifier (e.g., "civitai")
    name: str                        # Display name (e.g., "CivitAI")
    description: str                 # Brief description of the provider
    website: str                     # Provider website URL
    capabilities: List[ProviderCapability] = field(default_factory=list)
    icon: Optional[str] = None       # Base64 encoded icon or icon URL
    version: str = "1.0.0"           # Provider implementation version

    def has_capability(self, capability: ProviderCapability) -> bool:
        """Check if provider has a specific capability."""
        return capability in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'website': self.website,
            'capabilities': [cap.name for cap in self.capabilities],
            'icon': self.icon,
            'version': self.version,
        }


@dataclass
class ProviderModelInfo:
    """Model information returned by a provider."""

    provider_id: str                      # Provider that returned this info
    provider_model_id: str                # Model ID on the provider's platform
    provider_version_id: Optional[str] = None  # Version ID if applicable
    name: str = ""                        # Model name
    description: Optional[str] = None     # Model description
    tags: List[str] = field(default_factory=list)  # Tags/keywords
    nsfw: bool = False                    # NSFW flag
    download_url: Optional[str] = None    # Primary download URL
    media_urls: List[str] = field(default_factory=list)  # Preview image/video URLs
    model_type: Optional[str] = None      # Type (checkpoint, lora, embedding, etc.)
    base_model: Optional[str] = None      # Base model (SD 1.5, SDXL, etc.)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)  # Provider-specific data

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            'provider_id': self.provider_id,
            'provider_model_id': self.provider_model_id,
            'provider_version_id': self.provider_version_id,
            'name': self.name,
            'description': self.description,
            'tags': self.tags,
            'nsfw': self.nsfw,
            'download_url': self.download_url,
            'media_urls': self.media_urls,
            'model_type': self.model_type,
            'base_model': self.base_model,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'extra_data': self.extra_data,
        }


@dataclass
class ProviderSearchResult:
    """Search result from a provider."""

    provider_id: str                      # Provider that returned this result
    provider_model_id: str                # Model ID on the provider's platform
    name: str                             # Model name
    thumbnail_url: Optional[str] = None   # Thumbnail image URL
    download_url: Optional[str] = None    # Download URL if available
    model_type: Optional[str] = None      # Type (checkpoint, lora, etc.)
    rating: Optional[float] = None        # Rating/score if available
    downloads: Optional[int] = None       # Download count if available
    nsfw: bool = False                    # NSFW flag

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            'provider_id': self.provider_id,
            'provider_model_id': self.provider_model_id,
            'name': self.name,
            'thumbnail_url': self.thumbnail_url,
            'download_url': self.download_url,
            'model_type': self.model_type,
            'rating': self.rating,
            'downloads': self.downloads,
            'nsfw': self.nsfw,
        }


class ProviderPromptItem(TypedDict, total=False):
    """Provider-neutral browsing record consumed by the Prompt library.

    Positive and negative text share source metadata at the provider boundary,
    but the manager persists them as independent one-segment Prompt aggregates.
    """

    source_id: str
    prompt: str
    negative_prompt: str
    model_name: str
    base_model: str
    cfg_scale: float
    steps: int
    sampler: str
    width: int
    height: int
    stats: Dict[str, int]
    tags: List[str]
    nsfw: bool
    source_url: str
    metadata: Dict[str, Any]


@dataclass
class RemoteDownloadRef:
    """A credential-free URL a remote worker can fetch a model from.

    Headers are empty for a truly pre-signed URL; a provider that must attach
    a header-based token instead should treat that as a resolution failure
    rather than hand the token to an untrusted remote worker.
    """

    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    expires_hint: Optional[str] = None
    size_hint: Optional[int] = None  # Provider-reported byte size, when known, for a pre-download sanity check


class MarketplaceProviderBase(ABC):
    """
    Abstract base class for marketplace providers.

    All marketplace provider plugins must implement this interface to integrate
    with the PotionUI model management system.

    Lifecycle:
    1. Provider class is registered via provider.register hook
    2. initialize() is called with settings when provider is enabled
    3. Provider methods are called as needed
    4. shutdown() is called when provider is disabled or application closes

    Example implementation:

    ```python
    class MyProvider(MarketplaceProviderBase):
        def get_metadata(self) -> ProviderMetadata:
            return ProviderMetadata(
                id="my-provider",
                name="My Provider",
                description="Custom model marketplace",
                website="https://example.com",
                capabilities=[
                    ProviderCapability.HASH_LOOKUP,
                    ProviderCapability.SEARCH,
                ]
            )

        async def initialize(self, settings: Dict[str, Any]) -> bool:
            self.api_key = settings.get('api_key')
            return self.api_key is not None

        async def get_model_by_hash(self, sha256: str) -> Optional[ProviderModelInfo]:
            # Implementation...
            pass
    ```
    """

    @abstractmethod
    def get_metadata(self) -> ProviderMetadata:
        """
        Get provider metadata including capabilities.

        This method should return static information about the provider
        and is called during provider registration.

        Returns:
            ProviderMetadata with provider information and capabilities
        """
        pass

    @abstractmethod
    async def initialize(self, settings: Dict[str, Any]) -> bool:
        """
        Initialize the provider with settings.

        Called when the provider is enabled. Should set up any required
        connections, validate API keys, etc.

        Args:
            settings: Dictionary of provider settings (api_key, etc.)

        Returns:
            True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Cleanup provider resources.

        Called when the provider is disabled or the application is closing.
        Should close any open connections and release resources.
        """
        pass

    @abstractmethod
    async def get_model_by_hash(self, sha256: str) -> Optional[ProviderModelInfo]:
        """
        Look up model information by SHA256 hash.

        This is the primary lookup method used when matching local model
        files to provider metadata.

        Args:
            sha256: SHA256 hash of the model file

        Returns:
            ProviderModelInfo if found, None if not found

        Raises:
            ProviderConnectionError: If connection to provider fails
            ProviderRateLimitError: If rate limit is exceeded
        """
        pass

    async def search_models(
        self,
        query: str,
        model_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        **kwargs
    ) -> List[ProviderSearchResult]:
        """
        Search for models on the provider.

        Optional method - only required if provider has SEARCH capability.

        Args:
            query: Search query string
            model_type: Optional filter by model type
            limit: Maximum results to return (default 20)
            offset: Offset for pagination (default 0)
            **kwargs: Provider-specific search parameters

        Returns:
            List of search results

        Raises:
            NotImplementedError: If provider doesn't support search
        """
        raise NotImplementedError("This provider does not support search")

    async def get_download_url(
        self,
        provider_model_id: str,
        provider_version_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Get download URL for a model.

        Optional method - only required if provider has DOWNLOAD_URL capability.

        Args:
            provider_model_id: Model ID on the provider
            provider_version_id: Optional version ID

        Returns:
            Download URL if available, None otherwise

        Raises:
            NotImplementedError: If provider doesn't support download URLs
        """
        raise NotImplementedError("This provider does not support download URLs")

    async def resolve_remote_download(
        self,
        provider_model_id: str,
        provider_version_id: Optional[str] = None
    ) -> RemoteDownloadRef:
        """
        Resolve a model to a URL a remote (untrusted) worker can download
        from without ever seeing this provider's credentials.

        Optional method - only required if provider has REMOTE_DOWNLOAD
        capability. Implementations must resolve credentials host-side (e.g.
        a pre-signed, time-limited CDN URL) and return empty headers, or
        raise rather than expose a token.

        Args:
            provider_model_id: Model ID on the provider
            provider_version_id: Optional version ID

        Returns:
            A RemoteDownloadRef safe to hand to a remote worker

        Raises:
            NotImplementedError: If provider doesn't support remote download resolution
        """
        raise NotImplementedError("This provider does not support remote download resolution")

    async def resolve_remote_download_by_hash(self, sha256: str) -> RemoteDownloadRef:
        """
        Resolve a model to a remote-worker-fetchable URL from its SHA256
        alone, for models with no recorded provider link.

        Optional method - only required if provider has
        REMOTE_DOWNLOAD_BY_HASH capability. Same credential-free contract as
        `resolve_remote_download`.

        Args:
            sha256: SHA256 hash of the model file

        Returns:
            A RemoteDownloadRef safe to hand to a remote worker

        Raises:
            NotImplementedError: If provider doesn't support by-hash remote download resolution
        """
        raise NotImplementedError("This provider does not support remote download resolution by hash")

    async def fetch_image_prompts(self, **kwargs) -> List[ProviderPromptItem]:
        """Return prompt browsing records when PROMPT_FETCH is advertised."""
        raise NotImplementedError("This provider does not support prompt imports")

    def get_settings_schema(self) -> Dict[str, Any]:
        """
        Get JSON schema for provider settings.

        Returns a JSON Schema that describes the settings this provider
        accepts. Used to generate settings forms in the UI.

        Default implementation returns empty schema.
        Override to define provider-specific settings.

        Returns:
            JSON Schema dictionary

        Example:
            {
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "title": "API Key",
                        "format": "password",
                        "description": "Your API key from the provider"
                    },
                    "rate_limit": {
                        "type": "number",
                        "title": "Rate Limit (seconds)",
                        "default": 1.0,
                        "minimum": 0.1
                    }
                },
                "required": ["api_key"]
            }
        """
        return {
            "type": "object",
            "properties": {},
            "required": []
        }

    async def test_connection(self) -> bool:
        """
        Test if the provider connection is working.

        Used to verify API keys and connectivity.

        Returns:
            True if connection is working, False otherwise
        """
        # Default implementation - providers should override with actual test
        return True

    def get_download_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers to use when downloading files from this provider.

        Override this method to add authentication headers (API keys, tokens, etc.)
        that are required to download files from the provider.

        Returns:
            Dictionary of HTTP headers to include in download requests
        """
        return {}

    def get_authenticated_download_url(self, url: str) -> str:
        """
        Get an authenticated download URL.

        Some providers require authentication as a query parameter in the URL
        rather than (or in addition to) HTTP headers. Override this method
        to add authentication tokens to download URLs.

        Args:
            url: The original download URL

        Returns:
            The URL with any necessary authentication parameters added
        """
        return url

    def matches_download_url(self, url: str) -> bool:
        """
        Whether this provider recognizes (and can authenticate) downloads
        from the given URL.

        The download worker asks every registered provider when a download
        carries no explicit `provider_id`; the first match owns the request.
        Override to claim your marketplace's download hosts.

        Args:
            url: The download URL being fetched

        Returns:
            True if this provider should handle the URL
        """
        return False

    async def prepare_download(self, session, url: str, headers: Dict[str, str]) -> str:
        """
        Resolve the final URL to fetch for a download, applying whatever
        authentication this provider needs.

        The default merges `get_download_headers()` into `headers` (mutated
        in place) and returns `get_authenticated_download_url(url)`. Override
        for providers whose auth needs live requests (redirect dances,
        pre-signed URL resolution, token fallbacks) - `session` is the
        worker's aiohttp ClientSession.

        Args:
            session: The download worker's aiohttp ClientSession
            url: The original download URL
            headers: Request headers for the byte download (mutable)

        Returns:
            The URL the worker should stream bytes from
        """
        headers.update(self.get_download_headers())
        return self.get_authenticated_download_url(url)

    @property
    def provider_id(self) -> str:
        """Get the provider ID from metadata."""
        return self.get_metadata().id

    @property
    def provider_name(self) -> str:
        """Get the provider display name from metadata."""
        return self.get_metadata().name
