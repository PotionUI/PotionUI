"""Tests for the ProviderRegistry"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from src.features.providers import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderMetadata,
    ProviderModelInfo,
)


class MockProvider(MarketplaceProviderBase):
    """Mock provider for testing"""

    def __init__(self, provider_id="mock", name="Mock Provider"):
        self._id = provider_id
        self._name = name
        self._initialized = False

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            id=self._id,
            name=self._name,
            description="A mock provider for testing",
            website="https://example.com",
            capabilities=[ProviderCapability.HASH_LOOKUP, ProviderCapability.SEARCH]
        )

    async def initialize(self, settings):
        self._initialized = True
        return True

    async def shutdown(self):
        self._initialized = False

    async def get_model_by_hash(self, sha256):
        if sha256 == "known_hash":
            return ProviderModelInfo(
                provider_id=self._id,
                provider_model_id="12345",
                name="Mock Model"
            )
        return None

    def get_settings_schema(self):
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "API Key",
                    "format": "password"
                }
            },
            "required": []
        }


class TestProviderRegistry(unittest.TestCase):
    """Test ProviderRegistry"""

    def setUp(self):
        """Set up test fixtures"""
        # Create a mock registry
        self.mock_registry = MagicMock()
        self.mock_registry.execute_hook.return_value = (
            MagicMock(data={'providers': {'mock': MockProvider}}),
            True
        )

    @patch('src.features.providers.registry.PluginRepository')
    def test_service_creation(self, mock_plugin_repo):
        """Test service can be created"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        self.assertIsNotNone(service)
        self.assertEqual(service._registry, self.mock_registry)

    @patch('src.features.providers.registry.PluginRepository')
    def test_discover_providers(self, mock_plugin_repo):
        """Test discovering providers via hook"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        async def run_test():
            return await service.discover_providers()

        result = asyncio.run(run_test())

        # Verify hook was called
        self.mock_registry.execute_hook.assert_called_once()

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_all_provider_metadata(self, mock_plugin_repo):
        """Test getting all provider metadata"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        # Manually add a provider
        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        metadata_list = service.get_all_provider_metadata()

        self.assertEqual(len(metadata_list), 1)
        self.assertEqual(metadata_list[0].id, 'mock')
        self.assertEqual(metadata_list[0].name, 'Mock Provider')

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider_metadata(self, mock_plugin_repo):
        """Test getting specific provider metadata"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        # Manually add a provider
        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        metadata = service.get_provider_metadata('mock')

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.id, 'mock')

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider_metadata_not_found(self, mock_plugin_repo):
        """Test getting metadata for non-existent provider"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        metadata = service.get_provider_metadata('nonexistent')

        self.assertIsNone(metadata)

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider(self, mock_plugin_repo):
        """Test getting provider instance"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        provider = service.get_provider('mock')

        self.assertEqual(provider, mock_provider)

    @patch('src.features.providers.registry.PluginRepository')
    def test_is_provider_initialized(self, mock_plugin_repo):
        """Test checking if provider is initialized"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        self.assertFalse(service.is_provider_initialized('mock'))

        service._initialized_providers.add('mock')

        self.assertTrue(service.is_provider_initialized('mock'))

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_providers_with_capability(self, mock_plugin_repo):
        """Test getting providers with specific capability"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        providers = service.get_providers_with_capability(ProviderCapability.HASH_LOOKUP)

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0], mock_provider)

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_model_by_hash_provider_not_found(self, mock_plugin_repo):
        """Test get_model_by_hash when provider doesn't exist"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        async def run_test():
            return await service.get_model_by_hash('nonexistent', 'some_hash')

        result = asyncio.run(run_test())

        self.assertIsNone(result)

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_model_by_hash_provider_not_initialized(self, mock_plugin_repo):
        """Test get_model_by_hash when provider is not initialized"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider
        # Don't add to initialized_providers

        async def run_test():
            return await service.get_model_by_hash('mock', 'some_hash')

        result = asyncio.run(run_test())

        self.assertIsNone(result)

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_model_by_hash_success(self, mock_plugin_repo):
        """Test successful model lookup by hash"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider
        service._initialized_providers.add('mock')

        async def run_test():
            return await service.get_model_by_hash('mock', 'known_hash')

        result = asyncio.run(run_test())

        self.assertIsNotNone(result)
        self.assertEqual(result.provider_model_id, '12345')
        self.assertEqual(result.name, 'Mock Model')

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_model_by_hash_not_found(self, mock_plugin_repo):
        """Test model lookup when model is not found"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider
        service._initialized_providers.add('mock')

        async def run_test():
            return await service.get_model_by_hash('mock', 'unknown_hash')

        result = asyncio.run(run_test())

        self.assertIsNone(result)

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider_settings_schema(self, mock_plugin_repo):
        """Test getting provider settings schema"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        schema = service.get_provider_settings_schema('mock')

        self.assertIsNotNone(schema)
        self.assertEqual(schema['type'], 'object')
        self.assertIn('api_key', schema['properties'])

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider_settings_schema_not_found(self, mock_plugin_repo):
        """Test getting settings schema for non-existent provider"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        schema = service.get_provider_settings_schema('nonexistent')

        self.assertIsNone(schema)

    @patch('src.features.providers.registry.PluginRepository')
    def test_initialize_providers(self, mock_plugin_repo):
        """Test initializing providers"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        # Mock settings retrieval
        mock_plugin_repo.return_value.get_plugin_settings.return_value = []

        async def run_test():
            return await service.initialize_providers()

        results = asyncio.run(run_test())

        self.assertIn('mock', results)
        self.assertTrue(results['mock'])
        self.assertIn('mock', service._initialized_providers)

    @patch('src.features.providers.registry.PluginRepository')
    def test_shutdown_providers(self, mock_plugin_repo):
        """Test shutting down providers"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        mock_provider._initialized = True
        service._providers['mock'] = mock_provider
        service._initialized_providers.add('mock')

        async def run_test():
            await service.shutdown_providers()

        asyncio.run(run_test())

        self.assertNotIn('mock', service._initialized_providers)


class TestGetProviderService(unittest.TestCase):
    """Test the get_provider_registry function"""

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider_registry_singleton(self, mock_plugin_repo):
        """Test that get_provider_registry returns same instance"""
        from src.features.providers.registry import get_provider_registry

        # Reset global state
        import src.features.providers.registry as module
        module._provider_registry = None

        service1 = get_provider_registry()
        service2 = get_provider_registry()

        self.assertEqual(service1, service2)


if __name__ == '__main__':
    unittest.main()
