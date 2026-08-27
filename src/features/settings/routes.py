import asyncio
import os
import sys
from typing import Dict, Any, Optional, List, Union, TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.settings.settings import Settings
from src.features.models.directory import ModelDirectories
from src.platform.runtime.gpu import GpuMonitor
from src.features.backends.backend_registry import BackendRegistry
from src.features.settings.dto import (
    SettingsSchema,
    SystemInfo,
    ModelInfo,
    SettingResponse,
    UserSettingResponse,
    SettingUpdateRequest,
    UserSettingUpdateRequest
)
from src.platform.settings.repository import SettingRepository
from src.platform.settings.records import SettingType, SettingValueType
from src.platform.security.redaction import SECRET_MASK, is_secret_key, mask_secret_value
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def _is_stored_secret_mask(key: str, value: Any) -> bool:
    """Whether this write is the mask a read handed out, meaning "unchanged".

    A settings form saved without touching a credential field sends the mask
    straight back. Writing it through would replace the credential with three
    asterisks - which for `auth_secret_key` invalidates every session at once.
    Skipping is not an error: nothing changed.
    """
    return is_secret_key(key) and value == SECRET_MASK


class SettingsController(BaseController):
    def __init__(
        self,
        settings: Settings,
        setting_repository: SettingRepository,
        model_directories: ModelDirectories,
        gpu_monitor: GpuMonitor,
        backend_registry: BackendRegistry
    ):
        super().__init__()
        self.settings = settings
        self.setting_repository = setting_repository
        self.model_directories = model_directories
        self.gpu_monitor = gpu_monitor
        self.backend_registry = backend_registry

    async def get_settings(self, user: Optional[User] = None) -> APIResponse:
        """Get current application settings for a user"""
        try:
            if not user:
                return self.error_response(
                    error="authentication_required",
                    message="Authentication required to access settings"
                )

            # Get all settings
            all_settings = self.setting_repository.get_all_settings()

            result = {}
            for setting in all_settings:
                # Skip SYSTEM settings for non-admin users
                if setting.type == SettingType.SYSTEM and user.account_type != AccountType.ADMIN:
                    continue

                # Get the appropriate value
                if setting.type == SettingType.SYSTEM:
                    value = self.settings.get_setting(setting.key)
                else:  # USER type
                    value = self.settings.get_setting(setting.key, user_id=user.id)
                    if value is None:
                        value = setting.get_typed_value()  # Use default

                result[setting.key] = mask_secret_value(setting.key, value)

            return self.success_response(data=result)

        except Exception as e:
            return self.error_response(
                error="settings_get_failed",
                message=f"Failed to get settings: {str(e)}"
            )

    async def update_settings(self, settings: Dict[str, Any], user: Optional[User] = None) -> APIResponse:
        """Update application settings as one all-or-nothing batch.

        The whole batch is validated first (every key exists, the caller is
        allowed to change it, and the value serializes); only if every key
        passes are the writes applied in a single DB transaction. A rejected or
        failed batch persists nothing - no key is ever half-applied.
        """
        try:
            if not user:
                return self.error_response(
                    error="authentication_required",
                    message="Authentication required to update settings"
                )

            from src.platform.settings.records import Setting

            errors = []
            system_updates = []  # (setting_id, str_value)
            user_updates = []    # (user_id, setting_id, str_value)

            # Validate the entire batch before writing anything.
            for key, value in settings.items():
                setting = self.setting_repository.get_setting_by_key(key)
                if not setting:
                    errors.append(f"Setting '{key}' not found")
                    continue

                if setting.type == SettingType.SYSTEM and user.account_type != AccountType.ADMIN:
                    errors.append(f"Admin privileges required to modify system setting '{key}'")
                    continue

                if _is_stored_secret_mask(key, value):
                    continue

                try:
                    str_value = Setting.serialize_value(value, setting.value_type)
                except Exception as e:
                    errors.append(f"Invalid value for '{key}': {str(e)}")
                    continue

                if setting.type == SettingType.SYSTEM:
                    system_updates.append((setting.id, str_value))
                else:  # USER type
                    user_updates.append((user.id, setting.id, str_value))

            if errors:
                # Nothing has been written yet - reject the batch as a whole so a
                # partial update can never be committed.
                return self.error_response(
                    error="settings_update_rejected",
                    message=f"No settings were updated. Errors: {'; '.join(errors)}"
                )

            self.setting_repository.apply_bulk_updates(system_updates, user_updates)

            return self.success_response(
                message=f"Successfully updated {len(system_updates) + len(user_updates)} settings"
            )

        except Exception as e:
            return self.error_response(
                error="settings_update_failed",
                message=f"Failed to update settings: {str(e)}"
            )

    async def get_all_settings_detailed(self, setting_type: Optional[str] = None, user: Optional[User] = None) -> APIResponse:
        """Get all settings with detailed information"""
        try:
            type_filter = SettingType(setting_type) if setting_type else None
            settings = self.setting_repository.get_all_settings(type_filter)

            settings_data = []
            for setting in settings:
                # Skip SYSTEM settings for non-admin users
                if setting.type == SettingType.SYSTEM and (not user or user.account_type != AccountType.ADMIN):
                    continue

                # Skip USER settings that don't belong to the current user
                if setting.type == SettingType.USER and not user:
                    continue

                settings_data.append(SettingResponse(
                    id=setting.id,
                    key=setting.key,
                    value=mask_secret_value(setting.key, setting.get_typed_value()),
                    value_type=setting.value_type.value,
                    description=setting.description,
                    type=setting.type.value,
                    created_at=setting.created_at,
                    updated_at=setting.updated_at
                ).dict())

            return self.success_response(data=settings_data)

        except Exception as e:
            return self.error_response(
                error="settings_list_failed",
                message=f"Failed to list settings: {str(e)}"
            )

    async def get_setting_by_key(self, key: str, user: Optional[User] = None) -> APIResponse:
        """Get a specific setting by key"""
        try:
            # Get setting details first to determine type
            setting = self.setting_repository.get_setting_by_key(key)
            if not setting:
                return self.error_response(
                    error="setting_not_found",
                    message=f"Setting '{key}' not found"
                )

            # Check authorization based on setting type
            if setting.type == SettingType.SYSTEM:
                # System settings require admin privileges to view
                if not user or user.account_type != AccountType.ADMIN:
                    return self.error_response(
                        error="forbidden",
                        message="Admin privileges required to view system settings",
                        status_code=403
                    )
                value = self.settings.get_setting(key)
            else:  # USER type
                # User settings require the user context
                if not user:
                    return self.error_response(
                        error="authentication_required",
                        message="Authentication required to access user settings"
                    )
                value = self.settings.get_setting(key, user_id=user.id)

            if value is None:
                # For user settings, None might mean no value set yet
                if setting.type == SettingType.USER:
                    value = setting.get_typed_value()  # Return default value

            return self.success_response(data={
                "key": key,
                "value": mask_secret_value(key, value),
                "value_type": setting.value_type.value,
                "description": setting.description,
                "type": setting.type.value
            })

        except Exception as e:
            return self.error_response(
                error="setting_get_failed",
                message=f"Failed to get setting: {str(e)}"
            )

    async def update_setting_by_key(
        self,
        key: str,
        update_data: SettingUpdateRequest,
        user: Optional[User] = None
    ) -> APIResponse:
        """Update a specific setting by key"""
        try:
            # Get setting details first to determine type
            setting = self.setting_repository.get_setting_by_key(key)
            if not setting:
                return self.error_response(
                    error="setting_not_found",
                    message=f"Setting '{key}' not found"
                )

            # Check authorization based on setting type
            if setting.type == SettingType.SYSTEM:
                # System settings require admin privileges
                if not user or user.account_type != AccountType.ADMIN:
                    return self.error_response(
                        error="forbidden",
                        message="Admin privileges required to modify system settings",
                        status_code=403
                    )

                if _is_stored_secret_mask(key, update_data.value):
                    return self.success_response(message=f"Setting '{key}' unchanged")

                # Update system setting
                success = self.settings.set_setting(key, update_data.value)

                # Update description if provided
                if update_data.description is not None:
                    self.setting_repository.update_setting(
                        setting.id,
                        description=update_data.description
                    )
            else:  # USER type
                # User settings require authentication
                if not user:
                    return self.error_response(
                        error="authentication_required",
                        message="Authentication required to modify user settings"
                    )

                if _is_stored_secret_mask(key, update_data.value):
                    return self.success_response(message=f"Setting '{key}' unchanged")

                # Update user setting for the authenticated user
                success = self.settings.set_setting(key, update_data.value, user_id=user.id)

            if not success:
                return self.error_response(
                    error="setting_update_failed",
                    message=f"Failed to update setting '{key}'"
                )

            return self.success_response(message=f"Setting '{key}' updated successfully")

        except Exception as e:
            return self.error_response(
                error="setting_update_failed",
                message=f"Failed to update setting: {str(e)}"
            )

    async def get_user_settings(self, user: User) -> APIResponse:
        """Get all user setting overrides"""
        try:
            user_settings = self.setting_repository.get_user_settings(user.id)

            settings_data = []
            for user_setting in user_settings:
                # Get the base setting for context
                setting = self.setting_repository.get_setting_by_id(user_setting.setting_id)
                if setting:
                    settings_data.append(UserSettingResponse(
                        id=user_setting.id,
                        user_id=user_setting.user_id,
                        setting_id=user_setting.setting_id,
                        setting_key=setting.key,
                        value=user_setting.get_typed_value(setting.value_type),
                        created_at=user_setting.created_at,
                        updated_at=user_setting.updated_at
                    ).dict())

            return self.success_response(data=settings_data)

        except Exception as e:
            return self.error_response(
                error="user_settings_get_failed",
                message=f"Failed to get user settings: {str(e)}"
            )

    async def delete_user_setting(self, key: str, user: User) -> APIResponse:
        """Delete a user setting override, reverting to system default"""
        try:
            success = self.settings.delete_user_setting(key, user.id)

            if not success:
                return self.error_response(
                    error="user_setting_delete_failed",
                    message=f"Failed to delete user setting '{key}' or setting not found"
                )

            return self.success_response(
                message=f"User setting '{key}' deleted, reverted to system default"
            )

        except Exception as e:
            return self.error_response(
                error="user_setting_delete_failed",
                message=f"Failed to delete user setting: {str(e)}"
            )

    async def get_system_info(self) -> APIResponse:
        """Get system information"""
        try:
            # GPU information
            gpu_info = {
                'total_vram': self.gpu_monitor.get_total_vram(),
                'used_vram': self.gpu_monitor.get_used_vram(),
                'available_vram': self.gpu_monitor.get_available_vram(),
                'gpu_name': getattr(self.gpu_monitor, 'gpu_name', 'Unknown'),
                'driver_version': getattr(self.gpu_monitor, 'driver_version', 'Unknown')
            }

            # Memory information (simplified)
            import psutil
            from src.platform.runtime.system_memory import get_system_memory

            sys_mem = get_system_memory()
            used = sys_mem.total - sys_mem.available
            memory_info = {
                'total': sys_mem.total,
                'available': sys_mem.available,
                'used': used,
                'percent': (used / sys_mem.total * 100.0) if sys_mem.total > 0 else 0.0
            }

            # Disk information
            models_dir = Path(self.settings.get_models_dir())
            if models_dir.exists():
                disk_usage = psutil.disk_usage(str(models_dir))
                disk_info = {
                    'total': disk_usage.total,
                    'used': disk_usage.used,
                    'free': disk_usage.free,
                    'path': str(models_dir)
                }
            else:
                disk_info = {'error': 'Models directory not found'}

            # Model counts
            models_count = len(self.model_directories.get_all_models())

            # Preset counts (simplified)
            presets_count = 0
            presets_dir = Path('presets')
            if presets_dir.exists():
                presets_count = len(list(presets_dir.rglob('preset.yml')))

            system_info = SystemInfo(
                gpu_info=gpu_info,
                memory_info=memory_info,
                disk_info=disk_info,
                models_count=models_count,
                presets_count=presets_count
            )

            return self.success_response(data=system_info.dict())

        except Exception as e:
            return self.error_response(
                error="system_info_failed",
                message=f"Failed to get system info: {str(e)}"
            )

    async def list_models(self, model_type: Optional[str] = None) -> APIResponse:
        """List available models"""
        try:
            if model_type:
                models = self.model_directories.get_models_by_type(model_type)
            else:
                models = self.model_directories.get_all_models()

            model_list = []
            for model in models:
                # Check if model file exists
                file_path = model.get('file_path', '')
                available = Path(file_path).exists() if file_path else False

                # Get file size if available
                file_size = None
                if available:
                    try:
                        file_size = Path(file_path).stat().st_size
                    except Exception:
                        pass

                model_info = ModelInfo(
                    id=model.get('id', ''),
                    name=model.get('name', 'Unknown'),
                    type=model.get('type', 'unknown'),
                    file_path=file_path,
                    size=file_size,
                    base=model.get('base'),
                    available=available
                )
                model_list.append(model_info.dict())

            return self.success_response(data=model_list)

        except Exception as e:
            return self.error_response(
                error="models_list_failed",
                message=f"Failed to list models: {str(e)}"
            )

    async def get_model_types(self) -> APIResponse:
        """Get available model types"""
        try:
            model_types = self.model_directories.get_model_types()
            return self.success_response(data=model_types)

        except Exception as e:
            return self.error_response(
                error="model_types_failed",
                message=f"Failed to get model types: {str(e)}"
            )

    async def rescan_models(self) -> APIResponse:
        """Rescan models directory"""
        try:
            # This would trigger a model rescan
            models_found = self.model_directories.scan_models_directory()

            return self.success_response(data={
                'models_found': models_found,
                'message': 'Models directory rescanned successfully'
            })

        except Exception as e:
            return self.error_response(
                error="model_rescan_failed",
                message=f"Failed to rescan models: {str(e)}"
            )

    async def restart_app(self, user: Optional[User] = None) -> APIResponse:
        """Restart the server process in place via os.execv (admin only).

        Responds success first, then swaps the process image after a short
        delay so the HTTP response actually reaches the client before the
        old process is gone. Works for `python api.py` and under a container's
        PID 1 (exec replaces the image rather than forking).
        """
        if not user or user.account_type != AccountType.ADMIN:
            return self.error_response(
                error="admin_required",
                message="Restarting the application requires administrator privileges",
                status_code=403
            )

        from src.features.settings.app_lifecycle import schedule_app_restart
        schedule_app_restart()
        return self.success_response(message="Restarting application...")


def _build_settings_controller(container: "AppContainer") -> SettingsController:
    return SettingsController(
        container.settings,
        container.setting_repository,
        container.model_directories,
        container.gpu_monitor,
        container.backend_registry,
    )


def build_router(container: "AppContainer") -> APIRouter:
    controller = _build_settings_controller(container)

    router = APIRouter(prefix="/api/settings", tags=["Settings"])

    @router.get("", response_model=APIResponse, summary="Get Application Settings")
    async def get_settings(current_user = Depends(get_current_active_user)):
        """Get current application settings and configuration for the authenticated user."""
        return await controller.get_settings(current_user)

    @router.put("", response_model=APIResponse, summary="Update Application Settings")
    async def update_settings(settings: Dict[str, Any], current_user = Depends(get_current_active_user)):
        """Update application settings with new configuration values."""
        return await controller.update_settings(settings, current_user)

    @router.get("/all", response_model=APIResponse, summary="Get All Settings Detailed")
    async def get_all_settings_detailed(setting_type: Optional[str] = None, current_user = Depends(get_current_active_user)):
        """Get all settings with detailed information, optionally filtered by type (USER/SYSTEM)."""
        return await controller.get_all_settings_detailed(setting_type, current_user)

    @router.get("/user", response_model=APIResponse, summary="Get User Setting Overrides")
    async def get_user_settings(current_user = Depends(get_current_active_user)):
        """Get all user-specific setting overrides for the authenticated user."""
        return await controller.get_user_settings(current_user)

    @router.get("/{key}", response_model=APIResponse, summary="Get Setting by Key")
    async def get_setting_by_key(key: str, current_user = Depends(get_current_active_user)):
        """Get a specific setting value by key for the authenticated user."""
        return await controller.get_setting_by_key(key, current_user)

    @router.put("/{key}", response_model=APIResponse, summary="Update Setting by Key")
    async def update_setting_by_key(
        key: str,
        update_data: SettingUpdateRequest,
        current_user = Depends(get_current_active_user)
    ):
        """Update a specific setting by key. Creates user override if user is authenticated."""
        return await controller.update_setting_by_key(key, update_data, current_user)

    @router.delete("/user/{key}", response_model=APIResponse, summary="Delete User Setting Override")
    async def delete_user_setting(key: str, current_user = Depends(get_current_active_user)):
        """Delete a user setting override, reverting to system default."""
        return await controller.delete_user_setting(key, current_user)

    @router.get("/system/info", response_model=APIResponse, summary="Get System Information")
    async def get_system_info(current_user = Depends(get_current_active_user)):
        """Get detailed system information including hardware specifications."""
        return await controller.get_system_info()

    @router.get("/models/list", response_model=APIResponse, summary="List Available Models")
    async def list_models(model_type: Optional[str] = None, current_user = Depends(get_current_active_user)):
        """List all available models, optionally filtered by model type."""
        return await controller.list_models(model_type)

    @router.get("/models/types", response_model=APIResponse, summary="Get Model Types")
    async def get_model_types(current_user = Depends(get_current_active_user)):
        """Get all available model types (checkpoints, LoRAs, embeddings, etc.)."""
        return await controller.get_model_types()

    @router.post("/models/rescan", response_model=APIResponse, summary="Rescan Models Directory")
    async def rescan_models(current_user = Depends(get_current_admin_user)):
        """Rescan the models directory to detect newly added or removed models."""
        return await controller.rescan_models()

    return router


def build_admin_router(container: "AppContainer") -> APIRouter:
    """App-level admin actions live under /api/admin rather than /api/settings -
    restarting isn't a setting, it's an operation."""
    controller = _build_settings_controller(container)

    admin_router = APIRouter(prefix="/api/admin", tags=["Settings"])

    @admin_router.post("/restart", response_model=APIResponse, summary="Restart Application")
    async def restart_app(current_user = Depends(get_current_active_user)):
        """Restart the server process in place (admin only). No graceful drain - use sparingly."""
        return await controller.restart_app(current_user)

    return admin_router
