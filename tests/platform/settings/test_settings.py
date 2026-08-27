import pytest
from unittest.mock import Mock, MagicMock
from typing import Dict, Any

from src.platform.settings.settings import Settings
from src.platform.settings.repository import SettingRepository
from src.platform.settings.records import Setting, UserSetting, SettingType, SettingValueType
from datetime import datetime


class TestSettings:
    """Test cases for Settings"""

    @pytest.fixture
    def mock_repository(self):
        """Create a mock setting repository"""
        return Mock(spec=SettingRepository)

    @pytest.fixture
    def settings(self, mock_repository):
        """Create Settings instance with mocked repository"""
        manager = Settings(mock_repository)
        return manager

    @pytest.fixture
    def sample_settings(self):
        """Sample settings for testing"""
        return {
            "models_dir": "models",
            "device": "cuda",
            "precision": "fp16",
            "nsfw": False,
            "hf_api_key": "",
            "civitai_api_key": ""
        }

    def test_get_setting_system_wide(self, settings, mock_repository, sample_settings):
        """Test getting a setting system-wide (no user)"""
        mock_repository.get_effective_settings.return_value = sample_settings

        result = settings.get_setting("models_dir")

        assert result == "models"
        mock_repository.get_effective_settings.assert_called_once_with(None)

    def test_get_setting_with_default(self, settings, mock_repository):
        """Test getting a setting with default value"""
        mock_repository.get_effective_settings.return_value = {}

        result = settings.get_setting("nonexistent", default="default_value")

        assert result == "default_value"

    def test_get_setting_user_specific(self, settings, mock_repository):
        """Test getting a user-specific setting"""
        mock_repository.get_user_setting_by_key.return_value = "user_value"

        result = settings.get_setting("models_dir", user_id="user123")

        assert result == "user_value"
        mock_repository.get_user_setting_by_key.assert_called_once_with("user123", "models_dir")

    def test_get_setting_user_fallback_to_system(self, settings, mock_repository):
        """Test user setting falling back to system default"""
        # No user override
        mock_repository.get_user_setting_by_key.return_value = None

        # Mock system setting
        mock_setting = Mock(spec=Setting)
        mock_setting.get_typed_value.return_value = "system_value"
        mock_repository.get_setting_by_key.return_value = mock_setting

        result = settings.get_setting("models_dir", default="default", user_id="user123")

        assert result == "system_value"
        mock_repository.get_user_setting_by_key.assert_called_once_with("user123", "models_dir")
        mock_repository.get_setting_by_key.assert_called_once_with("models_dir")

    def test_set_setting_system(self, settings, mock_repository):
        """Test setting a system-wide setting"""
        # Mock existing setting
        mock_setting = Mock(spec=Setting)
        mock_setting.id = "setting123"
        mock_setting.value_type = SettingValueType.STRING
        mock_repository.get_setting_by_key.return_value = mock_setting
        mock_repository.update_setting_value.return_value = True

        result = settings.set_setting("models_dir", "new_models")

        assert result is True
        mock_repository.get_setting_by_key.assert_called_once_with("models_dir")
        mock_repository.update_setting_value.assert_called_once_with("setting123", "new_models")

    def test_set_setting_user_specific(self, settings, mock_repository):
        """Test setting a user-specific setting"""
        mock_repository.set_user_setting_by_key.return_value = True

        result = settings.set_setting("models_dir", "user_models", user_id="user123")

        assert result is True
        mock_repository.set_user_setting_by_key.assert_called_once_with("user123", "models_dir", "user_models")

    def test_set_setting_nonexistent(self, settings, mock_repository):
        """Test setting a non-existent setting"""
        mock_repository.get_setting_by_key.return_value = None

        result = settings.set_setting("nonexistent", "value")

        assert result is False

    def test_get_all_settings_system(self, settings, mock_repository, sample_settings):
        """Test getting all settings system-wide"""
        mock_repository.get_effective_settings.return_value = sample_settings

        result = settings.get_all_settings()

        assert result == sample_settings
        mock_repository.get_effective_settings.assert_called_once_with(None)

    def test_get_all_settings_user(self, settings, mock_repository, sample_settings):
        """Test getting all settings for a user"""
        mock_repository.get_effective_settings.return_value = sample_settings

        result = settings.get_all_settings(user_id="user123")

        assert result == sample_settings
        mock_repository.get_effective_settings.assert_called_once_with("user123")

    def test_delete_user_setting(self, settings, mock_repository):
        """Test deleting a user setting override"""
        # Mock setting exists
        mock_setting = Mock(spec=Setting)
        mock_setting.id = "setting123"
        mock_repository.get_setting_by_key.return_value = mock_setting
        mock_repository.delete_user_setting.return_value = True

        result = settings.delete_user_setting("models_dir", "user123")

        assert result is True
        mock_repository.get_setting_by_key.assert_called_once_with("models_dir")
        mock_repository.delete_user_setting.assert_called_once_with("user123", "setting123")

    def test_delete_user_setting_nonexistent(self, settings, mock_repository):
        """Test deleting a non-existent user setting"""
        mock_repository.get_setting_by_key.return_value = None

        result = settings.delete_user_setting("nonexistent", "user123")

        assert result is False

    def test_convenience_methods(self, settings, mock_repository):
        """Test convenience methods for common settings"""
        sample_settings = {
            "models_dir": "test_models",
            "nsfw": True,
        }
        mock_repository.get_effective_settings.return_value = sample_settings

        assert settings.get_models_dir() == "test_models"
        assert settings.is_nsfw_enabled() is True

    def test_convenience_methods_with_user(self, settings, mock_repository):
        """Test convenience methods with user-specific settings"""
        mock_repository.get_user_setting_by_key.return_value = "user_specific_value"

        result = settings.get_models_dir(user_id="user123")

        assert result == "user_specific_value"
        mock_repository.get_user_setting_by_key.assert_called_with("user123", "models_dir")

    def test_convenience_methods_defaults(self, settings, mock_repository):
        """Test convenience methods with default values when setting doesn't exist"""
        mock_repository.get_effective_settings.return_value = {}

        assert settings.get_models_dir() == "models"
        assert settings.is_nsfw_enabled() is False


class TestGpuSettingsMovedToNativeBackend:
    """
    device / dtype / gpu_max_vram are native-engine config (NativeBackendConfig),
    not global settings: only the native engine ever consulted them. Migration 070
    moved them onto the native backend. See docs/backends.md.
    """

    @pytest.fixture
    def mock_repository(self):
        return Mock(spec=SettingRepository)

    @pytest.fixture
    def settings(self, mock_repository):
        return Settings(mock_repository)

    @pytest.mark.parametrize(
        "removed",
        ["get_device", "get_precision", "get_dtype", "get_gpu_max_vram", "get_attention_mechanism"],
    )
    def test_gpu_getters_are_gone(self, settings, removed):
        assert not hasattr(settings, removed)

    @pytest.mark.parametrize("removed", ["get_hf_api_key", "get_civitai_api_key"])
    def test_marketplace_api_key_getters_are_gone(self, settings, removed):
        """Each provider plugin owns its own credentials. See docs/providers.md."""
        assert not hasattr(settings, removed)

    def test_file_storage_directory_is_still_a_setting(self, settings, mock_repository):
        """It is genuinely global - both engines write files to this host."""
        mock_repository.get_effective_settings.return_value = {"file_storage_directory": "/data"}

        assert settings.get_file_storage_directory() == "/data"
