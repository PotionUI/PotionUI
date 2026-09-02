"""
Registry of marketplace providers.

Manages the lifecycle of provider plugins: discovery via the `provider.register`
hook, initialization with each plugin's own settings (including its credentials),
and dispatch of lookups/searches/downloads to the right provider.

Core names no provider. See docs/providers.md.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Type

from src.platform.plugins.hooks import HookContext
from src.platform.plugins.registry import PluginRegistry
from src.features.providers.hooks import PROVIDER_HOOKS
from src.features.providers import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderError,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderSearchResult,
)
from src.features.plugins.repository import PluginRepository

logger = logging.getLogger(__name__)

_warned_provider_messages: set = set()


def _warn_once(message: str) -> None:
    if message not in _warned_provider_messages:
        logger.warning(message)
        _warned_provider_messages.add(message)


class ProviderRegistry:
    """
    Central service for managing marketplace providers.

    This service:
    - Collects providers registered via the provider.register hook
    - Manages provider lifecycle (initialize/shutdown)
    - Dispatches operations to appropriate providers
    - Handles provider settings storage/retrieval

    Usage:
        service = ProviderRegistry(registry)
        await service.initialize_providers()

        # Get model info from a specific provider
        info = await service.get_model_by_hash("provider_id", sha256_hash)

        # Get model info from any available provider
        info = await service.get_model_by_hash_any(sha256_hash)
    """

    def __init__(self, registry: Optional[PluginRegistry] = None):
        """
        Initialize the service.

        Args:
            registry: Plugin registry instance. If None, creates a default one.
        """
        self._registry = registry
        self._providers: Dict[str, MarketplaceProviderBase] = {}
        self._provider_classes: Dict[str, Type[MarketplaceProviderBase]] = {}
        self._initialized_providers: set = set()
        self._plugin_repo = PluginRepository()
        self._lock = asyncio.Lock()

    @property
    def registry(self) -> PluginRegistry:
        """Get the plugin registry (uses the global singleton)."""
        if self._registry is None:
            from src.platform.plugins.runtime_registries import get_global_plugin_registry
            self._registry = get_global_plugin_registry()
        return self._registry

    async def discover_providers(self) -> List[ProviderMetadata]:
        """
        Discover providers by executing the provider.register hook.

        This method triggers the hook which allows plugins to register
        their provider classes.

        Returns:
            List of provider metadata from discovered providers
        """
        logger.info("Discovering marketplace providers...")

        # Debug: Check registry state
        enabled_plugins = self.registry.get_enabled_plugins()
        logger.debug(f"Registry has {len(enabled_plugins)} enabled plugins: {[p.id for p in enabled_plugins]}")

        # Debug: Check hooks registered for provider.register
        plugins_for_hook = self.registry.get_plugins_for_hook(PROVIDER_HOOKS.register)
        logger.debug(f"Plugins registered for provider.register hook: {plugins_for_hook}")

        # Execute the provider.register hook
        context, success = self.registry.execute_hook(
            PROVIDER_HOOKS.register,
            initial_data={'providers': {}}
        )

        logger.debug(f"Hook execution success: {success}")

        if not success:
            logger.warning("Some provider registration hooks failed")

        # Extract registered provider classes from context
        registered = context.data.get('providers', {})
        logger.debug(f"Registered providers from hook: {list(registered.keys())}")

        for provider_id, provider_class in registered.items():
            if not issubclass(provider_class, MarketplaceProviderBase):
                logger.error(
                    f"Provider {provider_id} does not inherit from MarketplaceProviderBase"
                )
                continue

            self._provider_classes[provider_id] = provider_class

            # Create instance to get metadata
            try:
                instance = provider_class()
                metadata = instance.get_metadata()
                self._providers[provider_id] = instance
                logger.info(f"Discovered provider: {metadata.name} ({provider_id})")
            except Exception as e:
                logger.error(f"Failed to instantiate provider {provider_id}: {e}")

        return self.get_all_provider_metadata()

    async def initialize_providers(self) -> Dict[str, bool]:
        """
        Initialize all discovered providers with their settings.

        Returns:
            Dictionary mapping provider_id to initialization success
        """
        results = {}

        for provider_id, provider in self._providers.items():
            try:
                # Get settings for this provider
                settings = self._get_provider_settings(provider_id)

                # Initialize the provider
                success = await provider.initialize(settings)
                results[provider_id] = success

                if success:
                    self._initialized_providers.add(provider_id)
                    logger.info(f"Initialized provider: {provider_id}")
                else:
                    logger.warning(f"Provider {provider_id} initialization returned False")

            except Exception as e:
                logger.error(f"Failed to initialize provider {provider_id}: {e}")
                results[provider_id] = False

        return results

    async def shutdown_providers(self) -> None:
        """Shutdown all initialized providers."""
        for provider_id in list(self._initialized_providers):
            try:
                provider = self._providers.get(provider_id)
                if provider:
                    await provider.shutdown()
                    self._initialized_providers.discard(provider_id)
                    logger.info(f"Shutdown provider: {provider_id}")
            except Exception as e:
                logger.error(f"Error shutting down provider {provider_id}: {e}")

    def get_all_provider_metadata(self) -> List[ProviderMetadata]:
        """Get metadata for all discovered providers."""
        metadata_list = []
        for provider_id, provider in self._providers.items():
            try:
                metadata = provider.get_metadata()
                metadata_list.append(metadata)
            except Exception as e:
                logger.error(f"Failed to get metadata for provider {provider_id}: {e}")
        return metadata_list

    def get_provider_metadata(self, provider_id: str) -> Optional[ProviderMetadata]:
        """Get metadata for a specific provider."""
        provider = self._providers.get(provider_id)
        if provider:
            return provider.get_metadata()
        return None

    def get_provider(self, provider_id: str) -> Optional[MarketplaceProviderBase]:
        """Get a provider instance by ID."""
        return self._providers.get(provider_id)

    def find_provider_for_url(self, url: str) -> Optional[MarketplaceProviderBase]:
        """The provider that claims downloads from `url`, if any.

        Asks every registered provider via `matches_download_url` - core
        names no provider; each plugin describes the hosts it owns.
        """
        for provider in self._providers.values():
            try:
                if provider.matches_download_url(url):
                    return provider
            except Exception as e:
                logger.error(f"Error matching download URL against a provider: {e}")
        return None

    def is_provider_initialized(self, provider_id: str) -> bool:
        """Check if a provider is initialized."""
        return provider_id in self._initialized_providers

    def get_providers_with_capability(
        self,
        capability: ProviderCapability
    ) -> List[MarketplaceProviderBase]:
        """Get all providers that have a specific capability."""
        result = []
        for provider in self._providers.values():
            try:
                metadata = provider.get_metadata()
                if metadata.has_capability(capability):
                    result.append(provider)
            except Exception as e:
                logger.error(f"Error checking capability for provider: {e}")
        return result

    # === Provider Operations ===

    async def get_model_by_hash(
        self,
        provider_id: str,
        sha256: str
    ) -> Optional[ProviderModelInfo]:
        """
        Get model information by hash from a specific provider.

        Args:
            provider_id: ID of the provider to use
            sha256: SHA256 hash of the model file

        Returns:
            ProviderModelInfo if found, None otherwise
        """
        provider = self._providers.get(provider_id)
        if not provider:
            logger.error(f"Provider not found: {provider_id}")
            return None

        if provider_id not in self._initialized_providers:
            _warn_once(f"Provider {provider_id} is not initialized")
            return None

        try:
            return await provider.get_model_by_hash(sha256)
        except ProviderError as e:
            logger.error(f"Provider error from {provider_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error from provider {provider_id}: {e}")
            return None

    async def get_model_by_hash_any(
        self,
        sha256: str,
        preferred_providers: Optional[List[str]] = None
    ) -> Optional[ProviderModelInfo]:
        """
        Get model information by hash from any available provider.

        Tries providers in order of preference, returning the first successful result.

        Args:
            sha256: SHA256 hash of the model file
            preferred_providers: Optional list of provider IDs to try first

        Returns:
            ProviderModelInfo if found, None if not found in any provider
        """
        # Build ordered list of providers to try
        providers_to_try = []

        if preferred_providers:
            for pid in preferred_providers:
                if pid in self._initialized_providers:
                    providers_to_try.append(pid)

        # Add remaining initialized providers with HASH_LOOKUP capability
        for provider in self.get_providers_with_capability(ProviderCapability.HASH_LOOKUP):
            pid = provider.get_metadata().id
            if pid in self._initialized_providers and pid not in providers_to_try:
                providers_to_try.append(pid)

        # Try each provider
        for provider_id in providers_to_try:
            try:
                result = await self.get_model_by_hash(provider_id, sha256)
                if result:
                    logger.debug(f"Found model with hash {sha256[:8]}... on {provider_id}")
                    return result
            except Exception as e:
                logger.debug(f"Provider {provider_id} failed for hash {sha256[:8]}...: {e}")
                continue

        logger.debug(f"No provider found model with hash {sha256[:8]}...")
        return None

    async def search_models(
        self,
        provider_id: str,
        query: str,
        model_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        **kwargs
    ) -> List[ProviderSearchResult]:
        """
        Search for models on a specific provider.

        Args:
            provider_id: ID of the provider to search
            query: Search query string
            model_type: Optional filter by model type
            limit: Maximum results to return
            offset: Offset for pagination
            **kwargs: Additional provider-specific parameters

        Returns:
            List of search results
        """
        provider = self._providers.get(provider_id)
        if not provider:
            logger.error(f"Provider not found: {provider_id}")
            return []

        if provider_id not in self._initialized_providers:
            _warn_once(f"Provider {provider_id} is not initialized")
            return []

        # Check capability
        metadata = provider.get_metadata()
        if not metadata.has_capability(ProviderCapability.SEARCH):
            _warn_once(f"Provider {provider_id} does not support search")
            return []

        try:
            return await provider.search_models(
                query=query,
                model_type=model_type,
                limit=limit,
                offset=offset,
                **kwargs
            )
        except NotImplementedError:
            _warn_once(f"Provider {provider_id} has not implemented search")
            return []
        except Exception as e:
            logger.error(f"Search error from provider {provider_id}: {e}")
            return []

    async def get_download_url(
        self,
        provider_id: str,
        provider_model_id: str,
        provider_version_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Get download URL from a specific provider.

        Args:
            provider_id: ID of the provider
            provider_model_id: Model ID on the provider
            provider_version_id: Optional version ID

        Returns:
            Download URL if available, None otherwise
        """
        provider = self._providers.get(provider_id)
        if not provider:
            logger.error(f"Provider not found: {provider_id}")
            return None

        if provider_id not in self._initialized_providers:
            _warn_once(f"Provider {provider_id} is not initialized")
            return None

        # Check capability
        metadata = provider.get_metadata()
        if not metadata.has_capability(ProviderCapability.DOWNLOAD_URL):
            _warn_once(f"Provider {provider_id} does not support download URLs")
            return None

        try:
            return await provider.get_download_url(provider_model_id, provider_version_id)
        except NotImplementedError:
            _warn_once(f"Provider {provider_id} has not implemented download URL")
            return None
        except Exception as e:
            logger.error(f"Error getting download URL from {provider_id}: {e}")
            return None

    async def test_provider_connection(self, provider_id: str) -> bool:
        """
        Test connection to a specific provider.

        Args:
            provider_id: ID of the provider to test

        Returns:
            True if connection is working, False otherwise
        """
        provider = self._providers.get(provider_id)
        if not provider:
            logger.error(f"Provider not found: {provider_id}")
            return False

        try:
            return await provider.test_connection()
        except Exception as e:
            logger.error(f"Connection test failed for {provider_id}: {e}")
            return False

    # === Settings Management ===

    def _get_provider_settings(self, provider_id: str) -> Dict[str, Any]:
        """
        Get settings for a provider from plugin settings.

        Provider settings are stored with the pattern:
        plugin_id = "{provider_id}-provider" which matches the plugin ID.

        Args:
            provider_id: ID of the provider

        Returns:
            Dictionary of settings
        """
        settings = {}

        try:
            # Get all settings for the provider's plugin
            plugin_id = f"{provider_id}-provider"
            plugin_settings = self._plugin_repo.get_plugin_settings(plugin_id)

            for setting in plugin_settings:
                # Use setting value directly (PluginRepository handles storage)
                settings[setting.setting_key] = setting.setting_value

        except Exception as e:
            logger.error(f"Error loading settings for provider {provider_id}: {e}")

        return settings

    def get_provider_settings_schema(self, provider_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the settings schema for a provider.

        Args:
            provider_id: ID of the provider

        Returns:
            JSON Schema dictionary, or None if provider not found
        """
        provider = self._providers.get(provider_id)
        if not provider:
            return None

        return provider.get_settings_schema()

    async def update_provider_settings(
        self,
        provider_id: str,
        settings: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> bool:
        """
        Update settings for a provider.

        Args:
            provider_id: ID of the provider
            settings: Dictionary of settings to update
            user_id: Optional user ID for user-specific settings

        Returns:
            True if settings updated successfully
        """
        provider = self._providers.get(provider_id)
        if not provider:
            logger.error(f"Provider not found: {provider_id}")
            return False

        try:
            # Get the schema to determine which settings are secrets
            schema = provider.get_settings_schema()
            secret_keys = set()

            if schema and 'properties' in schema:
                for key, prop in schema['properties'].items():
                    if prop.get('format') == 'password':
                        secret_keys.add(key)

            # Save each setting
            plugin_id = f"{provider_id}-provider"

            for key, value in settings.items():
                is_secret = key in secret_keys
                # Convert value to string for storage
                str_value = str(value) if value is not None else ""
                self._plugin_repo.set_plugin_setting(
                    plugin_id=plugin_id,
                    setting_key=key,
                    setting_value=str_value,
                    user_id=user_id,
                    is_secret=is_secret
                )
                self._plugin_repo.record_setting_change(
                    plugin_id=plugin_id,
                    setting_key=key,
                    action='set',
                    actor_user_id=user_id,
                    scope_user_id=user_id,
                    is_secret=is_secret,
                )

            logger.info(f"Updated settings for provider {provider_id}")

            await self.reinitialize_provider(provider_id)

            return True

        except Exception as e:
            logger.error(f"Error updating settings for provider {provider_id}: {e}")
            return False

    async def reinitialize_provider(self, provider_id: str) -> bool:
        """Shut down (if running) and re-initialize a provider with freshly read settings.

        For when a provider's plugin settings changed through a write path other
        than `update_provider_settings` (e.g. the plugin settings API), so the
        live instance picks up new credentials without a process restart.
        """
        provider = self._providers.get(provider_id)
        if not provider:
            logger.error(f"Provider not found: {provider_id}")
            return False

        try:
            if provider_id in self._initialized_providers:
                await provider.shutdown()
                self._initialized_providers.discard(provider_id)

            settings = self._get_provider_settings(provider_id)
            success = await provider.initialize(settings)
            if success:
                self._initialized_providers.add(provider_id)
                logger.info(f"Reinitialized provider: {provider_id}")
            return success

        except Exception as e:
            logger.error(f"Error reinitializing provider {provider_id}: {e}")
            return False

    def provider_id_for_plugin(self, plugin_id: str) -> Optional[str]:
        """The registered provider id whose settings live under `plugin_id`, if any.

        Inverse of the `f"{provider_id}-provider"` plugin_id convention used by
        `_get_provider_settings` - derived from the providers this registry
        actually knows about, never guessed from the plugin id string.
        """
        for provider_id in self._providers:
            if f"{provider_id}-provider" == plugin_id:
                return provider_id
        return None

    def get_provider_current_settings(
        self,
        provider_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get current settings for a provider (masking secrets).

        Args:
            provider_id: ID of the provider
            user_id: Optional user ID for user-specific settings

        Returns:
            Dictionary of settings with secrets masked
        """
        settings = {}

        try:
            # Get schema to know which are secrets
            provider = self._providers.get(provider_id)
            schema = provider.get_settings_schema() if provider else {}
            secret_keys = set()

            if schema and 'properties' in schema:
                for key, prop in schema['properties'].items():
                    if prop.get('format') == 'password':
                        secret_keys.add(key)

            # Get all settings
            plugin_id = f"{provider_id}-provider"
            plugin_settings = self._plugin_repo.get_plugin_settings(plugin_id, user_id)

            for setting in plugin_settings:
                if setting.setting_key in secret_keys:
                    # Mask secrets - just indicate if set or not
                    settings[setting.setting_key] = "***" if setting.setting_value else None
                else:
                    settings[setting.setting_key] = setting.setting_value

        except Exception as e:
            logger.error(f"Error getting settings for provider {provider_id}: {e}")

        return settings


# Global service instance
_provider_registry: Optional[ProviderRegistry] = None
_discovery_done: bool = False


def get_provider_registry() -> ProviderRegistry:
    """Get the global provider service instance, lazily discovering providers."""
    global _provider_registry, _discovery_done
    if _provider_registry is None:
        _provider_registry = ProviderRegistry()

    # Lazily discover providers if not already done
    if not _discovery_done:
        import asyncio
        try:
            # Try to get running loop
            loop = asyncio.get_running_loop()
            # We're in an async context, create a task
            asyncio.create_task(_async_discover_and_init())
        except RuntimeError:
            # No running loop, run synchronously
            asyncio.run(_async_discover_and_init())
        _discovery_done = True

    return _provider_registry


async def _async_discover_and_init():
    """Async helper to discover and initialize providers."""
    global _provider_registry
    if _provider_registry:
        await _provider_registry.discover_providers()
        await _provider_registry.initialize_providers()


async def ensure_providers_discovered() -> ProviderRegistry:
    """
    Ensure providers are discovered and initialized.

    Call this from async contexts to ensure providers are ready.
    """
    global _provider_registry, _discovery_done
    if _provider_registry is None:
        _provider_registry = ProviderRegistry()

    if not _discovery_done:
        await _provider_registry.discover_providers()
        await _provider_registry.initialize_providers()
        _discovery_done = True

    return _provider_registry


async def refresh_provider_for_plugin(plugin_id: str) -> bool:
    """Re-initialize the live provider backed by a plugin's settings, if any.

    No-ops (returns False) when the registry hasn't been created or discovery
    hasn't run yet - the next `ensure_providers_discovered` call reads the
    fresh settings from the DB anyway, so there is nothing live to refresh.
    """
    if _provider_registry is None or not _discovery_done:
        return False

    provider_id = _provider_registry.provider_id_for_plugin(plugin_id)
    if provider_id is None:
        return False

    return await _provider_registry.reinitialize_provider(provider_id)


def reset_provider_registry():
    """
    Reset the provider service, forcing re-discovery on next access.

    Call this when plugins are enabled/disabled to refresh providers.
    """
    global _provider_registry, _discovery_done
    _discovery_done = False
    if _provider_registry:
        # Clear existing providers
        _provider_registry._providers.clear()
        _provider_registry._provider_classes.clear()
        _provider_registry._initialized_providers.clear()
