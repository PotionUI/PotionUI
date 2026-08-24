"""Tests for the provider base classes and interfaces"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from src.features.providers import (
    MarketplaceProviderBase,
    ProviderCapability,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderSearchResult,
    ProviderError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderNotFoundError,
)


class TestProviderCapability(unittest.TestCase):
    """Test ProviderCapability enum"""

    def test_capability_values(self):
        """Test that all expected capabilities exist"""
        expected_capabilities = [
            'HASH_LOOKUP',
            'SEARCH',
            'DOWNLOAD_URL',
            'MODEL_INFO',
            'MEDIA_DOWNLOAD',
            'PROMPT_FETCH',
            'API_KEY_REQUIRED',
        ]

        actual_capabilities = [cap.name for cap in ProviderCapability]

        for expected in expected_capabilities:
            self.assertIn(expected, actual_capabilities)


class TestProviderMetadata(unittest.TestCase):
    """Test ProviderMetadata dataclass"""

    def test_create_metadata(self):
        """Test creating provider metadata"""
        metadata = ProviderMetadata(
            id="test-provider",
            name="Test Provider",
            description="A test provider",
            website="https://example.com",
            capabilities=[ProviderCapability.HASH_LOOKUP, ProviderCapability.SEARCH],
            icon=None,
            version="1.0.0"
        )

        self.assertEqual(metadata.id, "test-provider")
        self.assertEqual(metadata.name, "Test Provider")
        self.assertEqual(metadata.description, "A test provider")
        self.assertEqual(metadata.website, "https://example.com")
        self.assertEqual(len(metadata.capabilities), 2)
        self.assertEqual(metadata.version, "1.0.0")

    def test_has_capability(self):
        """Test has_capability method"""
        metadata = ProviderMetadata(
            id="test",
            name="Test",
            description="Test",
            website="https://example.com",
            capabilities=[ProviderCapability.HASH_LOOKUP]
        )

        self.assertTrue(metadata.has_capability(ProviderCapability.HASH_LOOKUP))
        self.assertFalse(metadata.has_capability(ProviderCapability.SEARCH))

    def test_to_dict(self):
        """Test to_dict method"""
        metadata = ProviderMetadata(
            id="test",
            name="Test",
            description="Test",
            website="https://example.com",
            capabilities=[ProviderCapability.HASH_LOOKUP, ProviderCapability.PROMPT_FETCH],
            version="1.0.0"
        )

        result = metadata.to_dict()

        self.assertEqual(result['id'], "test")
        self.assertEqual(result['name'], "Test")
        self.assertEqual(result['capabilities'], ['HASH_LOOKUP', 'PROMPT_FETCH'])


class TestProviderModelInfo(unittest.TestCase):
    """Test ProviderModelInfo dataclass"""

    def test_create_model_info(self):
        """Test creating model info"""
        info = ProviderModelInfo(
            provider_id="civitai",
            provider_model_id="12345",
            provider_version_id="67890",
            name="Test Model",
            description="A test model",
            tags=["tag1", "tag2"],
            nsfw=False,
            download_url="https://example.com/download",
            media_urls=["https://example.com/image1.jpg"]
        )

        self.assertEqual(info.provider_id, "civitai")
        self.assertEqual(info.provider_model_id, "12345")
        self.assertEqual(info.name, "Test Model")
        self.assertEqual(len(info.tags), 2)
        self.assertFalse(info.nsfw)

    def test_to_dict(self):
        """Test to_dict method"""
        info = ProviderModelInfo(
            provider_id="civitai",
            provider_model_id="12345",
            name="Test Model",
            tags=["tag1"]
        )

        result = info.to_dict()

        self.assertEqual(result['provider_id'], "civitai")
        self.assertEqual(result['provider_model_id'], "12345")
        self.assertEqual(result['name'], "Test Model")


class TestProviderSearchResult(unittest.TestCase):
    """Test ProviderSearchResult dataclass"""

    def test_create_search_result(self):
        """Test creating search result"""
        result = ProviderSearchResult(
            provider_id="civitai",
            provider_model_id="12345",
            name="Test Model",
            thumbnail_url="https://example.com/thumb.jpg",
            model_type="checkpoint",
            rating=4.5,
            downloads=1000
        )

        self.assertEqual(result.provider_id, "civitai")
        self.assertEqual(result.name, "Test Model")
        self.assertEqual(result.rating, 4.5)
        self.assertEqual(result.downloads, 1000)

    def test_to_dict(self):
        """Test to_dict method"""
        result = ProviderSearchResult(
            provider_id="civitai",
            provider_model_id="12345",
            name="Test Model"
        )

        dict_result = result.to_dict()

        self.assertEqual(dict_result['provider_id'], "civitai")
        self.assertEqual(dict_result['name'], "Test Model")


class TestProviderExceptions(unittest.TestCase):
    """Test provider exception classes"""

    def test_provider_error(self):
        """Test base ProviderError"""
        error = ProviderError("Test error")
        self.assertEqual(str(error), "Test error")

    def test_provider_connection_error(self):
        """Test ProviderConnectionError"""
        error = ProviderConnectionError("Connection failed")
        self.assertIsInstance(error, ProviderError)
        self.assertEqual(str(error), "Connection failed")

    def test_provider_rate_limit_error(self):
        """Test ProviderRateLimitError"""
        error = ProviderRateLimitError("Rate limit exceeded", retry_after=60.0)
        self.assertIsInstance(error, ProviderError)
        self.assertEqual(error.retry_after, 60.0)

    def test_provider_not_found_error(self):
        """Test ProviderNotFoundError"""
        error = ProviderNotFoundError("Model not found")
        self.assertIsInstance(error, ProviderError)


class ConcreteTestProvider(MarketplaceProviderBase):
    """Concrete implementation for testing abstract base class"""

    def __init__(self):
        self._initialized = False
        self._api_key = None

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            id="test-provider",
            name="Test Provider",
            description="A test provider",
            website="https://example.com",
            capabilities=[ProviderCapability.HASH_LOOKUP, ProviderCapability.SEARCH]
        )

    async def initialize(self, settings):
        self._api_key = settings.get('api_key')
        self._initialized = True
        return True

    async def shutdown(self):
        self._initialized = False

    async def get_model_by_hash(self, sha256):
        if sha256 == "known_hash":
            return ProviderModelInfo(
                provider_id="test-provider",
                provider_model_id="12345",
                name="Test Model"
            )
        return None


class TestMarketplaceProviderBase(unittest.TestCase):
    """Test MarketplaceProviderBase abstract class"""

    def test_concrete_implementation(self):
        """Test that concrete implementation works"""
        provider = ConcreteTestProvider()
        metadata = provider.get_metadata()

        self.assertEqual(metadata.id, "test-provider")
        self.assertEqual(metadata.name, "Test Provider")

    def test_provider_id_property(self):
        """Test provider_id property"""
        provider = ConcreteTestProvider()
        self.assertEqual(provider.provider_id, "test-provider")

    def test_provider_name_property(self):
        """Test provider_name property"""
        provider = ConcreteTestProvider()
        self.assertEqual(provider.provider_name, "Test Provider")

    def test_initialize(self):
        """Test initialize method"""
        provider = ConcreteTestProvider()

        async def run_test():
            result = await provider.initialize({'api_key': 'test_key'})
            return result

        result = asyncio.run(run_test())

        self.assertTrue(result)
        self.assertTrue(provider._initialized)
        self.assertEqual(provider._api_key, 'test_key')

    def test_shutdown(self):
        """Test shutdown method"""
        provider = ConcreteTestProvider()

        async def run_test():
            await provider.initialize({})
            await provider.shutdown()
            return provider._initialized

        result = asyncio.run(run_test())

        self.assertFalse(result)

    def test_get_model_by_hash_found(self):
        """Test get_model_by_hash when model is found"""
        provider = ConcreteTestProvider()

        async def run_test():
            return await provider.get_model_by_hash("known_hash")

        result = asyncio.run(run_test())

        self.assertIsNotNone(result)
        self.assertEqual(result.provider_model_id, "12345")
        self.assertEqual(result.name, "Test Model")

    def test_get_model_by_hash_not_found(self):
        """Test get_model_by_hash when model is not found"""
        provider = ConcreteTestProvider()

        async def run_test():
            return await provider.get_model_by_hash("unknown_hash")

        result = asyncio.run(run_test())

        self.assertIsNone(result)

    def test_search_models_not_implemented(self):
        """Test that search_models raises NotImplementedError by default"""
        provider = ConcreteTestProvider()

        async def run_test():
            return await provider.search_models("test query")

        with self.assertRaises(NotImplementedError):
            asyncio.run(run_test())

    def test_get_download_url_not_implemented(self):
        """Test that get_download_url raises NotImplementedError by default"""
        provider = ConcreteTestProvider()

        async def run_test():
            return await provider.get_download_url("12345")

        with self.assertRaises(NotImplementedError):
            asyncio.run(run_test())

    def test_fetch_image_prompts_not_implemented(self):
        """Test that prompt imports require an advertised provider implementation."""
        provider = ConcreteTestProvider()

        async def run_test():
            return await provider.fetch_image_prompts(limit=20)

        with self.assertRaises(NotImplementedError):
            asyncio.run(run_test())

    def test_get_settings_schema_default(self):
        """Test default settings schema"""
        provider = ConcreteTestProvider()
        schema = provider.get_settings_schema()

        self.assertEqual(schema['type'], 'object')
        self.assertEqual(schema['properties'], {})
        self.assertEqual(schema['required'], [])

    def test_test_connection_default(self):
        """Test default test_connection returns True"""
        provider = ConcreteTestProvider()

        async def run_test():
            return await provider.test_connection()

        result = asyncio.run(run_test())

        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
