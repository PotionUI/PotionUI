"""Registry for @resource providers."""

import logging
from typing import Dict, List, Optional, Protocol

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
)

logger = logging.getLogger(__name__)


class ResourceScope(Protocol):
    """A chat mode, seen through the two attributes resource visibility reads."""

    id: str
    resource_namespaces: Optional[List[str]]


class DuplicateResourceNamespaceError(Exception):
    """A provider for the same namespace is already registered."""
    pass


class ResourceRegistry:
    """Registry of resource providers, keyed by namespace.

    Builtin providers are registered at startup; plugins register additional
    providers when enabled and unregister them (by source) on disable.
    Resolution never raises: unknown namespaces/paths resolve to an
    error-shaped :class:`ResolvedResource` so a stale mention cannot fail a
    chat send.
    """

    def __init__(self):
        self._providers: Dict[str, BaseResourceProvider] = {}
        self._sources: Dict[str, str] = {}

    def register(self, provider: BaseResourceProvider, source: str = "builtin") -> None:
        """Register a provider. Raises DuplicateResourceNamespaceError on collision."""
        namespace = provider.namespace
        if namespace in self._providers:
            raise DuplicateResourceNamespaceError(
                f"Resource namespace '{namespace}' is already registered "
                f"(by '{self._sources.get(namespace)}')"
            )
        self._providers[namespace] = provider
        self._sources[namespace] = source
        logger.debug(f"Registered resource provider: {namespace} (source: {source})")

    def unregister(self, namespace: str) -> bool:
        """Unregister a provider by namespace. Returns True if found and removed."""
        if namespace in self._providers:
            del self._providers[namespace]
            self._sources.pop(namespace, None)
            logger.debug(f"Unregistered resource provider: {namespace}")
            return True
        return False

    def unregister_source(self, source: str) -> int:
        """Unregister all providers registered by the given source (plugin id).

        Returns the number of providers removed.
        """
        to_remove = [ns for ns, src in self._sources.items() if src == source]
        for namespace in to_remove:
            del self._providers[namespace]
            del self._sources[namespace]
        if to_remove:
            logger.info(f"Unregistered resource providers {to_remove} for source '{source}'")
        return len(to_remove)

    def get(self, namespace: str) -> Optional[BaseResourceProvider]:
        """Get a provider by namespace."""
        return self._providers.get(namespace)

    def get_all(self) -> List[BaseResourceProvider]:
        """Get all registered providers."""
        return list(self._providers.values())

    def providers_for_mode(self, mode: Optional[ResourceScope]) -> List[BaseResourceProvider]:
        """Get the providers visible in the given mode.

        A provider is visible when its own ``modes`` allows the mode (None =
        all) AND the mode's ``resource_namespaces`` allows the namespace
        (None = all). With no mode, all providers are visible.
        """
        providers = list(self._providers.values())
        if mode is None:
            return providers
        allowed_namespaces = mode.resource_namespaces
        return [
            p for p in providers
            if (p.modes is None or mode.id in p.modes)
            and (allowed_namespaces is None or p.namespace in allowed_namespaces)
        ]

    async def resolve(self, uri: str, ctx: ResourceContext) -> ResolvedResource:
        """Resolve a full ``namespace.path`` URI. Never raises."""
        uri = (uri or "").strip().lstrip('@')
        segments = [s for s in uri.split('.') if s]
        if not segments:
            return self._error_resource(uri)

        namespace, path = segments[0], segments[1:]
        provider = self._providers.get(namespace)
        if provider is None:
            return self._error_resource(uri, namespace)

        try:
            resolved = await provider.resolve(path, ctx)
        except Exception as e:
            logger.error(f"Resource provider '{namespace}' failed to resolve '{uri}': {e}", exc_info=True)
            resolved = None

        return resolved if resolved is not None else self._error_resource(uri, namespace)

    @staticmethod
    def _error_resource(uri: str, namespace: str = "") -> ResolvedResource:
        return ResolvedResource(
            uri=uri,
            namespace=namespace,
            kind="error",
            title=uri,
            content=f"@{uri} could not be resolved (the resource may have been removed).",
        )

    async def suggest(
        self,
        query: str,
        mode: Optional[ResourceScope],
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        """Suggest completions for a partial ``@`` query.

        An empty or dot-free query lists (prefix-matched) namespaces; a dotted
        query delegates to the namespace's provider with the completed
        segments and the trailing partial.
        """
        query = (query or "").strip().lstrip('@')
        providers = {p.namespace: p for p in self.providers_for_mode(mode)}

        if '.' not in query:
            partial = query.lower()
            return [
                ResourceSuggestion(
                    uri=p.namespace,
                    label=p.display_name,
                    kind="namespace",
                    has_children=True,
                    icon=p.icon,
                )
                for ns, p in sorted(providers.items())
                if not partial or ns.lower().startswith(partial)
            ][:limit]

        segments = query.split('.')
        namespace, path, partial = segments[0], segments[1:-1], segments[-1]
        provider = providers.get(namespace)
        if provider is None:
            return []

        try:
            return (await provider.suggest(path, partial, ctx, limit=limit))[:limit]
        except Exception as e:
            logger.error(f"Resource provider '{namespace}' failed to suggest for '{query}': {e}", exc_info=True)
            return []
