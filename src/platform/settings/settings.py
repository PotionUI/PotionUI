from typing import Dict, Any, Optional

from src.platform.settings.repository import SettingRepository


class SettingsManager:
    """Simple database-backed settings manager without caching"""

    def __init__(self, setting_repository: SettingRepository):
        self.setting_repository = setting_repository

    def get_setting(self, key: str, default: Any = None, user_id: Optional[str] = None) -> Any:
        """Get a setting value directly from database"""
        if user_id:
            # For user-specific requests
            value = self.setting_repository.get_user_setting_by_key(user_id, key)
            if value is not None:
                return value
            
            # Fall back to system default
            setting = self.setting_repository.get_setting_by_key(key)
            return setting.get_typed_value() if setting else default
        
        # For system-wide requests
        settings = self.setting_repository.get_effective_settings(user_id)
        return settings.get(key, default)

    def set_setting(self, key: str, value: Any, user_id: Optional[str] = None) -> bool:
        """Set a setting value directly in database"""
        if user_id:
            # Set user-specific override
            return self.setting_repository.set_user_setting_by_key(user_id, key, value)
        else:
            # Update system setting
            setting = self.setting_repository.get_setting_by_key(key)
            if not setting:
                return False
            
            from src.platform.settings.records import Setting
            str_value = Setting.serialize_value(value, setting.value_type)
            return self.setting_repository.update_setting_value(setting.id, str_value)

    def get_all_settings(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get all effective settings for a user or system-wide"""
        return self.setting_repository.get_effective_settings(user_id)

    def delete_user_setting(self, key: str, user_id: str) -> bool:
        """Delete a user's override for a setting, reverting to system default"""
        setting = self.setting_repository.get_setting_by_key(key)
        if not setting:
            return False
        
        return self.setting_repository.delete_user_setting(user_id, setting.id)

    # Convenience methods for common settings
    def get_models_dir(self, user_id: Optional[str] = None) -> str:
        return self.get_setting('models_dir', 'models', user_id)

    # device / dtype / gpu_max_vram are NOT settings: they configure the native
    # engine and live on NativeBackendConfig. See docs/backends.md.

    def is_nsfw_enabled(self, user_id: Optional[str] = None) -> bool:
        return self.get_setting('nsfw', False, user_id)

    def get_model_cache_scope(self, user_id: Optional[str] = None) -> str:
        """How the native model RAM cache is scoped across preset switches:
        ``'preset'`` (default) evicts a previous preset's cached models when you
        switch presets, so host RAM holds only the active preset; ``'global'``
        keeps every preset's models until RAM pressure forces LRU eviction (the
        pre-preset-scoping behaviour). Read by ModelLifecycleManager."""
        scope = self.get_setting('model_cache_scope', 'preset', user_id)
        return scope if scope in ('preset', 'global') else 'preset'

    # Marketplace API keys are NOT settings: each provider owns its own credentials,
    # declared in its plugin manifest. See docs/providers.md.

    def get_file_storage_directory(self, user_id: Optional[str] = None) -> str:
        """Get the base file storage directory"""
        return self.get_setting('file_storage_directory', 'storage', user_id)

    def get_generations_directory(self, user_id: Optional[str] = None) -> str:
        """Get the generations directory path"""
        base_dir = self.get_file_storage_directory(user_id)
        return f"{base_dir}/generations"

    def get_tmp_directory(self, user_id: Optional[str] = None) -> str:
        """Get the temporary files directory path"""
        base_dir = self.get_file_storage_directory(user_id)
        return f"{base_dir}/tmp"

    def get_models_media_directory(self, user_id: Optional[str] = None) -> str:
        """Get the models media directory path"""
        base_dir = self.get_file_storage_directory(user_id)
        return f"{base_dir}/models"