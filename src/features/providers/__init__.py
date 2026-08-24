"""
Marketplace Provider System for PotionUI.

This module provides the abstraction layer for marketplace providers (CivitAI, HuggingFace, etc.)
that can be added as plugins to provide model metadata lookup capabilities.
"""

from .base_provider import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderSearchResult,
    ProviderPromptItem,
    ProviderError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderNotFoundError,
)

__all__ = [
    'MarketplaceProviderBase',
    'ProviderCapability',
    'ProviderMetadata',
    'ProviderModelInfo',
    'ProviderSearchResult',
    'ProviderPromptItem',
    'ProviderError',
    'ProviderConnectionError',
    'ProviderRateLimitError',
    'ProviderNotFoundError',
]
