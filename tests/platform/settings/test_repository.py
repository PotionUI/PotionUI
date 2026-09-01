import pytest
from datetime import datetime
from typing import Dict, Any
from unittest.mock import patch

from src.platform.settings.repository import SettingRepository
from src.platform.settings.records import Setting, SettingType, SettingValueType


class TestSettingRepository:
    """Test cases for SettingRepository - Core functionality only"""

    @pytest.fixture
    def repository(self, mock_db):
        """Create repository instance with test database"""
        return SettingRepository()

    def test_get_setting_by_key_existing(self, repository):
        """Test retrieving an existing setting by key"""
        # Use a setting we know exists from migration
        setting = repository.get_setting_by_key("models_dir")
        
        assert setting is not None
        assert setting.key == "models_dir"
        assert setting.value == "models"
        assert setting.value_type == SettingValueType.STRING
        assert setting.type == SettingType.SYSTEM

    def test_get_setting_by_key_not_found(self, repository):
        """Test retrieving a non-existent setting"""
        setting = repository.get_setting_by_key("nonexistent_key_12345")
        assert setting is None

    def test_get_all_settings(self, repository):
        """Test retrieving all settings"""
        settings = repository.get_all_settings()
        
        # Should have at least the default settings from migration
        assert len(settings) >= 8
        keys = [s.key for s in settings]
        assert "models_dir" in keys
        assert "nsfw_filter" in keys

    def test_get_all_settings_filtered_by_type(self, repository):
        """Test retrieving settings filtered by type"""
        system_settings = repository.get_all_settings(SettingType.SYSTEM)
        user_settings = repository.get_all_settings(SettingType.USER)
        
        # Should have default system and user settings
        assert len(system_settings) >= 7  # Most defaults are system
        assert len(user_settings) >= 1   # At least nsfw is user
        
        assert all(s.type == SettingType.SYSTEM for s in system_settings)
        assert all(s.type == SettingType.USER for s in user_settings)

    def test_get_effective_settings_no_user(self, repository):
        """Test getting effective settings without user overrides"""
        effective_settings = repository.get_effective_settings()
        
        # Should include default settings
        assert len(effective_settings) >= 8
        assert "models_dir" in effective_settings
        assert "nsfw_filter" in effective_settings
        
        # Values should be properly typed
        assert isinstance(effective_settings["models_dir"], str)
        assert isinstance(effective_settings["nsfw_filter"], bool)

    def test_setting_typed_values(self, repository):
        """Test that settings return properly typed values"""
        # Test with existing settings
        models_dir_setting = repository.get_setting_by_key("models_dir")
        assert models_dir_setting.get_typed_value() == "models"

        nsfw_filter_setting = repository.get_setting_by_key("nsfw_filter")
        assert nsfw_filter_setting.get_typed_value() is False

    def test_setting_serialize_value(self):
        """Test value serialization for different types"""
        # Test string
        assert Setting.serialize_value("hello", SettingValueType.STRING) == "hello"
        
        # Test integer
        assert Setting.serialize_value(123, SettingValueType.INTEGER) == "123"
        
        # Test float
        assert Setting.serialize_value(123.45, SettingValueType.FLOAT) == "123.45"
        
        # Test boolean
        assert Setting.serialize_value(True, SettingValueType.BOOLEAN) == "true"
        assert Setting.serialize_value(False, SettingValueType.BOOLEAN) == "false"
        
        # Test JSON
        obj = {"key": "value", "list": [1, 2, 3]}
        serialized = Setting.serialize_value(obj, SettingValueType.JSON)
        assert serialized == '{"key": "value", "list": [1, 2, 3]}'

    def test_output_directory_setting_exists(self, repository):
        """Test that output_directory setting exists and is properly configured"""
        setting = repository.get_setting_by_key("output_directory")
        
        assert setting is not None
        assert setting.key == "output_directory"
        # The value may be expanded to an absolute path during app initialization
        assert "outputs" in setting.value  # Should contain the base path
        assert setting.value_type == SettingValueType.STRING
        assert setting.type == SettingType.SYSTEM
        assert setting.description == (
            "DEPRECATED: Use file_storage_directory instead. "
            "Directory where generated images and files are stored"
        )

    def test_repository_instantiation(self, repository):
        """Test that repository can be instantiated and is functional"""
        assert repository is not None
        assert hasattr(repository, 'get_setting_by_key')
        assert hasattr(repository, 'get_all_settings')
        assert hasattr(repository, 'get_effective_settings')

    def test_mock_db_actually_isolates_this_repository(self, repository, mock_db):
        # Regression: `settings/repository.py` used to bind `db` at its own
        # top-level `from ... import db` - once anything (a prior test,
        # collection itself) had imported that module once, patching
        # `database.db` never reached it: the module kept its own frozen
        # reference, writes-and-all, to whatever `db` was at that first
        # import - the live `storage/db.sqlite` in a plain `pytest tests/`
        # run. `db` is now imported at call time inside each method, so
        # re-pointing `database.db` mid-test (as `mock_db` already did once,
        # for the whole test) must be picked up on the very next call - not
        # only on the first one after collection.
        from tests.conftest import TestDatabase
        from src.platform.database.migration_runner import MigrationRunner

        second_db = TestDatabase()
        with patch("src.platform.database.database.db", second_db), \
             patch("src.platform.database.migration_runner.db", second_db):
            MigrationRunner().run_migrations()

        with patch("src.platform.database.database.db", second_db):
            setting = repository.create_setting(
                key="mock_db_isolation_probe",
                value="probe",
                value_type=SettingValueType.STRING,
            )

        with mock_db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM settings WHERE id = ?", (setting.id,))
            assert cursor.fetchone() is None, "write leaked into the fixture's own db, not the re-pointed one"

        with second_db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM settings WHERE id = ?", (setting.id,))
            assert cursor.fetchone() is not None, "write did not land on the re-pointed db"