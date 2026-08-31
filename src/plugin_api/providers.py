"""Talking to a model marketplace.

A provider plugin subclasses `MarketplaceProviderBase` to teach the application
about one marketplace: how to look a model up, search it, and download from it
with the plugin's own credentials. Core ships no providers - CivitAI and
HuggingFace are plugins like any other.

Declare what you support with `ProviderCapability`; return `ProviderModelInfo` /
`ProviderSearchResult` from the lookups; raise the `Provider*Error` types so the
caller can tell "not found" from "rate limited" from "the site is down".

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
from src.features.providers.registry import get_provider_registry
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
    "get_provider_registry",
]
