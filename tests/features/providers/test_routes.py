"""Tests for the Provider Controller"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.features.providers import (
    ProviderCapability,
    ProviderMetadata,
    ProviderModelInfo,
)


class MockUser:
    """Mock user for testing authentication"""
    id = "test-user-id"
    account_type = "ADMIN"


def get_mock_user():
    """Dependency override for authentication"""
    return MockUser()


class TestProviderController(unittest.TestCase):
    """Test Provider Controller endpoints"""

    def setUp(self):
        """Set up test fixtures"""
        # Create mock provider service
        self.mock_service = MagicMock()

        # Create mock metadata
        self.mock_metadata = ProviderMetadata(
            id="civitai",
            name="CivitAI",
            description="Test provider",
            website="https://civitai.com",
            capabilities=[ProviderCapability.HASH_LOOKUP],
            version="1.0.0"
        )

        # Import the module to ensure it's loaded before patching
        import src.features.providers.routes as provider_controller_module

        # Patch the ensure_providers_discovered function (it's async)
        self.service_patcher = patch.object(
            provider_controller_module,
            'ensure_providers_discovered',
            new_callable=AsyncMock,
            return_value=self.mock_service
        )
        self.mock_get_service = self.service_patcher.start()

        # Create a mock plugin registry
        self.mock_plugin_registry = MagicMock()
        self.mock_plugin_registry.execute_hook.return_value = (MagicMock(), [])

        # Create test app with auth override
        app = FastAPI()

        # Override the authentication dependency
        from src.platform.security.current_user import get_current_active_user
        from src.features.providers.routes import build_router

        app.dependency_overrides[get_current_active_user] = get_mock_user
        app.include_router(build_router(SimpleNamespace(plugin_registry=self.mock_plugin_registry)))

        self.client = TestClient(app)
        self.app = app

    def tearDown(self):
        """Tear down test fixtures"""
        self.service_patcher.stop()
        self.app.dependency_overrides.clear()

    def test_list_providers_empty(self):
        """Test listing providers when none are registered"""
        self.mock_service.get_all_provider_metadata.return_value = []
        self.mock_service.is_provider_initialized.return_value = False

        response = self.client.get("/api/providers/")

        self.assertEqual(response.status_code, 200)
        json_response = response.json()
        self.assertTrue(json_response['success'])
        self.assertEqual(json_response['data'], [])

    def test_list_providers_with_providers(self):
        """Test listing providers with registered providers"""
        self.mock_service.get_all_provider_metadata.return_value = [self.mock_metadata]
        self.mock_service.is_provider_initialized.return_value = True

        response = self.client.get("/api/providers/")

        self.assertEqual(response.status_code, 200)
        json_response = response.json()
        self.assertTrue(json_response['success'])
        data = json_response['data']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['id'], 'civitai')
        self.assertEqual(data[0]['name'], 'CivitAI')
        self.assertTrue(data[0]['initialized'])

    def test_get_provider_found(self):
        """Test getting a specific provider"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = True

        response = self.client.get("/api/providers/civitai")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['id'], 'civitai')
        self.assertEqual(data['name'], 'CivitAI')

    def test_get_provider_not_found(self):
        """Test getting a non-existent provider"""
        self.mock_service.get_provider_metadata.return_value = None

        response = self.client.get("/api/providers/nonexistent")

        self.assertEqual(response.status_code, 404)

    def test_get_settings_schema(self):
        """Test getting provider settings schema"""
        mock_schema = {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "format": "password"}
            },
            "required": []
        }
        self.mock_service.get_provider_settings_schema.return_value = mock_schema

        response = self.client.get("/api/providers/civitai/settings/schema")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['type'], 'object')
        self.assertIn('api_key', data['properties'])

    def test_get_settings_schema_not_found(self):
        """Test getting settings schema for non-existent provider"""
        self.mock_service.get_provider_settings_schema.return_value = None

        response = self.client.get("/api/providers/nonexistent/settings/schema")

        self.assertEqual(response.status_code, 404)

    def test_get_settings(self):
        """Test getting provider settings"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.get_provider_current_settings.return_value = {
            "api_key": "***",
            "rate_limit": "1.0"
        }

        response = self.client.get("/api/providers/civitai/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['api_key'], '***')
        self.assertEqual(data['rate_limit'], '1.0')

    def test_update_settings(self):
        """Test updating provider settings"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata

        # Mock async method
        async_mock = AsyncMock(return_value=True)
        self.mock_service.update_provider_settings = async_mock

        response = self.client.put(
            "/api/providers/civitai/settings",
            json={"settings": {"api_key": "new_key"}}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('message', response.json())

    def test_update_settings_not_found(self):
        """Test updating settings for non-existent provider"""
        self.mock_service.get_provider_metadata.return_value = None

        response = self.client.put(
            "/api/providers/nonexistent/settings",
            json={"settings": {"api_key": "new_key"}}
        )

        self.assertEqual(response.status_code, 404)

    def test_test_connection_success(self):
        """Test successful connection test"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = True

        # Mock async method
        async_mock = AsyncMock(return_value=True)
        self.mock_service.test_provider_connection = async_mock

        response = self.client.post("/api/providers/civitai/test")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

    def test_test_connection_not_initialized(self):
        """Test connection test when provider not initialized"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = False

        response = self.client.post("/api/providers/civitai/test")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('not initialized', data['message'].lower())

    def test_test_connection_failure(self):
        """Test failed connection test"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = True

        # Mock async method
        async_mock = AsyncMock(return_value=False)
        self.mock_service.test_provider_connection = async_mock

        response = self.client.post("/api/providers/civitai/test")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])

    def test_lookup_model_by_hash_success(self):
        """Test successful model lookup by hash"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = True

        mock_model_info = ProviderModelInfo(
            provider_id="civitai",
            provider_model_id="12345",
            name="Test Model"
        )

        # Mock async method
        async_mock = AsyncMock(return_value=mock_model_info)
        self.mock_service.get_model_by_hash = async_mock

        response = self.client.get("/api/providers/civitai/lookup/abc123")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['provider_model_id'], '12345')
        self.assertEqual(data['name'], 'Test Model')

    def test_lookup_model_not_found(self):
        """Test model lookup when not found"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = True

        # Mock async method
        async_mock = AsyncMock(return_value=None)
        self.mock_service.get_model_by_hash = async_mock

        response = self.client.get("/api/providers/civitai/lookup/unknown")

        self.assertEqual(response.status_code, 404)

    def test_lookup_model_provider_not_initialized(self):
        """Test model lookup when provider not initialized"""
        self.mock_service.get_provider_metadata.return_value = self.mock_metadata
        self.mock_service.is_provider_initialized.return_value = False

        response = self.client.get("/api/providers/civitai/lookup/abc123")

        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
