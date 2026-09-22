"""Talking to a model marketplace.

A provider plugin subclasses `MarketplaceProviderBase` to teach the application
about one marketplace: how to look a model up, search it, and download from it
with the plugin's own credentials. Core ships no providers - CivitAI and
HuggingFace are plugins like any other.

Declare what you support with `ProviderCapability`; return `ProviderModelInfo` /
`ProviderSearchResult` from the lookups; raise the `Provider*Error` types so the
caller can tell "not found" from "rate limited" from "the site is down".

`get_provider_registry()` is a synchronous, best-effort accessor; from an
async route handler that needs a provider ready *now* (not merely kicked off
in the background), `await ensure_providers_discovered()` instead.

See docs/providers.md.
"""

from src.features.providers import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderConnectionError,
    ProviderError,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderNotFoundError,
    ProviderPromptItem,
    ProviderRateLimitError,
    ProviderSearchResult,
    RemoteDownloadRef,
)
from src.features.providers.registry import ensure_providers_discovered, get_provider_registry
from src.features.models.records import ModelInfo

__all__ = [
    "MarketplaceProviderBase",
    "ModelInfo",
    "ProviderCapability",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderMetadata",
    "ProviderModelInfo",
    "ProviderNotFoundError",
    "ProviderPromptItem",
    "ProviderRateLimitError",
    "ProviderSearchResult",
    "RemoteDownloadRef",
    "ensure_providers_discovered",
    "get_provider_registry",
]
