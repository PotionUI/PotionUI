"""Tests for the ProviderRegistry"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from types import SimpleNamespace

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
        self.last_settings = None

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
        self.last_settings = settings
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


class TestProviderRegistry(unittest.TestCase):
    """Test ProviderRegistry"""

    def setUp(self):
        """Set up test fixtures"""
        # Create a mock registry
        self.mock_registry = MagicMock()
        self.mock_registry.get_plugin.side_effect = lambda plugin_id: (
            SimpleNamespace(settings=[
                {"name": "api_key", "type": "string", "label": "API Key", "is_secret": True},
                {"name": "verify", "type": "boolean", "default": True},
            ])
            if plugin_id == "mock-provider" else None
        )
        self.mock_registry.execute_hook.return_value = (
            MagicMock(data={'providers': {'mock': MockProvider}}),
            True
        )

    @patch('src.features.providers.registry.PluginRepository')
    def test_service_creation(self, mock_plugin_repo):
        """Test service can be created"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

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
    def test_a_hash_mismatch_is_swallowed_unless_the_caller_asks_for_errors(self, mock_plugin_repo):
        from src.features.providers import ProviderHashMismatchError
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)
        mock_provider = MockProvider()
        mock_provider.get_model_by_hash = AsyncMock(side_effect=ProviderHashMismatchError("different file"))
        service._providers['mock'] = mock_provider
        service._initialized_providers.add('mock')

        async def lookups():
            quiet = await service.get_model_by_hash('mock', 'h')
            try:
                await service.get_model_by_hash('mock', 'h', raise_errors=True)
            except ProviderHashMismatchError as exc:
                return quiet, exc
            return quiet, None

        quiet, raised = asyncio.run(lookups())

        self.assertIsNone(quiet)
        self.assertIsInstance(raised, ProviderHashMismatchError)

    @patch('src.features.providers.registry.PluginRepository')
    def test_get_provider_settings_schema(self, mock_plugin_repo):
        """Test getting provider settings schema"""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider

        schema = service.get_provider_settings_schema('mock')

        self.assertEqual(schema['type'], 'object')
        self.assertIn('api_key', schema['properties'])

    @patch('src.features.providers.registry.PluginRepository')
    def test_provider_secret_keys_come_from_the_manifest(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)
        service._providers['mock'] = MockProvider()

        self.assertEqual(service.get_provider_secret_keys('mock'), ['api_key'])
        self.assertIsNone(service.get_provider_secret_keys('unmanifested'))
        schema = service.get_provider_settings_schema('mock')
        self.assertEqual(schema['properties']['api_key']['format'], 'password')
        self.assertNotIn('format', schema['properties']['verify'])

    @patch('src.features.providers.registry.PluginRepository')
    def test_update_provider_settings_encrypts_what_the_manifest_marks_secret(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)
        service._providers['mock'] = MockProvider()
        mock_plugin_repo.return_value.get_plugin_settings.return_value = []

        asyncio.run(service.update_provider_settings('mock', {'api_key': 'sk-1', 'verify': True}))

        flags = {
            call.kwargs['setting_key']: call.kwargs['is_secret']
            for call in mock_plugin_repo.return_value.set_plugin_setting.call_args_list
        }
        self.assertEqual(flags, {'api_key': True, 'verify': False})

    @patch('src.features.providers.registry.PluginRepository')
    def test_update_provider_settings_refuses_to_store_without_a_manifest(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        self.mock_registry.get_plugin.side_effect = None
        self.mock_registry.get_plugin.return_value = None
        service = ProviderRegistry(self.mock_registry)
        service._providers['mock'] = MockProvider()

        self.assertFalse(asyncio.run(service.update_provider_settings('mock', {'api_key': 'sk-1'})))
        mock_plugin_repo.return_value.set_plugin_setting.assert_not_called()

    @patch('src.features.providers.registry.PluginRepository')
    def test_current_settings_mask_manifest_secrets(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)
        service._providers['mock'] = MockProvider()
        mock_plugin_repo.return_value.get_plugin_settings.return_value = [
            SimpleNamespace(setting_key='api_key', setting_value='sk-1', is_secret=False),
            SimpleNamespace(setting_key='verify', setting_value='true', is_secret=False),
        ]

        self.assertEqual(
            service.get_provider_current_settings('mock'), {'api_key': '***', 'verify': 'true'}
        )

    @patch('src.features.providers.registry.PluginRepository')
    def test_current_settings_mask_a_row_flagged_secret_outside_the_manifest(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)
        service._providers['mock'] = MockProvider()
        mock_plugin_repo.return_value.get_plugin_settings.return_value = [
            SimpleNamespace(setting_key='legacy_token', setting_value='x', is_secret=True),
        ]

        self.assertEqual(service.get_provider_current_settings('mock'), {'legacy_token': '***'})

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

    @patch('src.features.providers.registry.PluginRepository')
    def test_reinitialize_provider_reads_fresh_settings(self, mock_plugin_repo):
        """reinitialize_provider re-reads settings, not the ones it started with."""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider
        service._initialized_providers.add('mock')
        # Simulate the provider having been initialized earlier with a stale
        # (e.g. empty) key, before the admin saved a new one.
        mock_provider.last_settings = {'api_key': 'stale'}

        fresh_setting = MagicMock(setting_key='api_key', setting_value='fresh')
        mock_plugin_repo.return_value.get_plugin_settings.return_value = [fresh_setting]

        async def run_test():
            return await service.reinitialize_provider('mock')

        success = asyncio.run(run_test())

        self.assertTrue(success)
        self.assertIn('mock', service._initialized_providers)
        self.assertEqual(mock_provider.last_settings, {'api_key': 'fresh'})

    @patch('src.features.providers.registry.PluginRepository')
    def test_reinitialize_provider_initializes_never_initialized(self, mock_plugin_repo):
        """A provider that was never initialized is initialized too."""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider()
        service._providers['mock'] = mock_provider
        mock_plugin_repo.return_value.get_plugin_settings.return_value = []

        async def run_test():
            return await service.reinitialize_provider('mock')

        success = asyncio.run(run_test())

        self.assertTrue(success)
        self.assertIn('mock', service._initialized_providers)

    @patch('src.features.providers.registry.PluginRepository')
    def test_update_provider_settings_stores_booleans_as_json_literals(self, mock_plugin_repo):
        """A boolean is stored as true/false, not Python's True/False; other values with str(), None as ""."""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)
        service._providers['mock'] = MockProvider()
        mock_plugin_repo.return_value.get_plugin_settings.return_value = []

        async def run_test():
            return await service.update_provider_settings(
                'mock', {'download_media': False, 'verify': True, 'max_media_files': 5, 'api_key': None}, user_id='u1'
            )

        self.assertTrue(asyncio.run(run_test()))
        stored = {
            call.kwargs['setting_key']: call.kwargs['setting_value']
            for call in mock_plugin_repo.return_value.set_plugin_setting.call_args_list
        }
        self.assertEqual(stored, {'download_media': 'false', 'verify': 'true', 'max_media_files': '5', 'api_key': ''})

    @patch('src.features.providers.registry.PluginRepository')
    def test_reinitialize_provider_unknown_id(self, mock_plugin_repo):
        """An unknown provider id reinitializes to False."""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        async def run_test():
            return await service.reinitialize_provider('nonexistent')

        self.assertFalse(asyncio.run(run_test()))

    @patch('src.features.providers.registry.PluginRepository')
    def test_provider_id_for_plugin_derives_from_registered_providers(self, mock_plugin_repo):
        """provider_id_for_plugin maps a plugin id back to its provider without hardcoding names."""
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(self.mock_registry)

        mock_provider = MockProvider(provider_id='acme')
        service._providers['acme'] = mock_provider

        self.assertEqual(service.provider_id_for_plugin('acme-provider'), 'acme')
        self.assertIsNone(service.provider_id_for_plugin('unrelated-plugin'))


class TestRefreshProviderForPlugin(unittest.TestCase):
    """Test the module-level refresh_provider_for_plugin() helper."""

    def setUp(self):
        import src.features.providers.registry as module
        self.module = module
        self._orig_registry = module._provider_registry
        self._orig_discovery_done = module._discovery_done

    def tearDown(self):
        self.module._provider_registry = self._orig_registry
        self.module._discovery_done = self._orig_discovery_done

    def test_noop_when_registry_not_created(self):
        self.module._provider_registry = None
        self.module._discovery_done = False

        result = asyncio.run(self.module.refresh_provider_for_plugin('acme-provider'))

        self.assertFalse(result)

    @patch('src.features.providers.registry.PluginRepository')
    def test_noop_when_discovery_not_done(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(MagicMock())
        service._providers['acme'] = MockProvider(provider_id='acme')
        self.module._provider_registry = service
        self.module._discovery_done = False

        result = asyncio.run(self.module.refresh_provider_for_plugin('acme-provider'))

        self.assertFalse(result)

    @patch('src.features.providers.registry.PluginRepository')
    def test_reinitializes_matching_provider(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(MagicMock())
        mock_provider = MockProvider(provider_id='acme')
        service._providers['acme'] = mock_provider
        mock_plugin_repo.return_value.get_plugin_settings.return_value = []
        self.module._provider_registry = service
        self.module._discovery_done = True

        result = asyncio.run(self.module.refresh_provider_for_plugin('acme-provider'))

        self.assertTrue(result)
        self.assertIn('acme', service._initialized_providers)

    @patch('src.features.providers.registry.PluginRepository')
    def test_noop_for_unrelated_plugin_id(self, mock_plugin_repo):
        from src.features.providers.registry import ProviderRegistry

        service = ProviderRegistry(MagicMock())
        service._providers['acme'] = MockProvider(provider_id='acme')
        self.module._provider_registry = service
        self.module._discovery_done = True

        result = asyncio.run(self.module.refresh_provider_for_plugin('some-other-plugin'))

        self.assertFalse(result)


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
