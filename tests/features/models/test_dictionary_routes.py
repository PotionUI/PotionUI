import pytest
import sys
import os
from unittest.mock import Mock, patch

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.features.models.dictionary_routes import DictionaryController
from src.platform.http.base_controller import APIResponse
from src.features.models.indexer import ModelScanner
from src.platform.security.user import User, AccountType


class TestDictionaryController:
    """Tests for DictionaryController"""

    @pytest.fixture
    def admin_user(self):
        """Sample admin user data"""
        return User(
            id="admin-user-123",
            username="admin",
            email="admin@example.com",
            password_hash="$2b$12$admin.hash",
            account_type=AccountType.ADMIN
        )

    @pytest.fixture
    def dictionary_controller(self):
        """DictionaryController instance"""
        return DictionaryController()

    @pytest.mark.asyncio
    async def test_get_models_dictionary_success(self, dictionary_controller, admin_user):
        """Test successful retrieval of models dictionary"""
        response = await dictionary_controller.router.routes[0].endpoint(current_user=admin_user)

        assert response.success is True
        assert 'models' in response.data
        assert isinstance(response.data['models'], list)
        assert len(response.data['models']) > 0

    @pytest.mark.asyncio
    async def test_get_models_dictionary_contains_expected_types(self, dictionary_controller, admin_user):
        """Test models dictionary contains expected model types"""
        response = await dictionary_controller.router.routes[0].endpoint(current_user=admin_user)

        models = response.data['models']

        # Check that common model types are present
        assert 'checkpoint' in models
        assert 'lora' in models
        assert 'embedding' in models
        assert 'vae' in models
        assert 'controlnet' in models

    @pytest.mark.asyncio
    async def test_get_models_dictionary_returns_all_types(self, dictionary_controller, admin_user):
        """Test models dictionary returns all model types from the scanner"""
        expected_types = list(ModelScanner.MODEL_TYPE_MAPPING.values())

        response = await dictionary_controller.router.routes[0].endpoint(current_user=admin_user)

        models = response.data['models']

        # Verify all expected types are present
        assert set(models) == set(expected_types)

    def test_controller_initialization(self, dictionary_controller):
        """Test controller initializes correctly"""
        assert dictionary_controller is not None
        assert dictionary_controller.router is not None
        assert len(dictionary_controller.router.routes) == 1

        # Check route details
        route = dictionary_controller.router.routes[0]
        assert route.path == "/models"
        assert "GET" in route.methods
