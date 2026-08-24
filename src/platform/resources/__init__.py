"""@resource system: providers, registry, and data models."""

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
)
from src.platform.resources.registry import DuplicateResourceNamespaceError, ResourceRegistry

__all__ = [
    "BaseResourceProvider",
    "ResolvedResource",
    "ResourceContext",
    "ResourceSuggestion",
    "ResourceRegistry",
    "DuplicateResourceNamespaceError",
]
