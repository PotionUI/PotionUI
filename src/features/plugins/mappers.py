"""
Response mappers for the plugins feature.

Plain functions that turn Plugin/PluginHook/PluginSetting records into their
API response DTOs. No class, no state - a mapping that needs a collaborator
the manager/controller holds (e.g. the plugin registry, for runtime state
enrichment) takes it as an explicit argument.
"""
from typing import Optional

from src.features.plugins.dto import (
    PluginResponse,
    PluginHookResponse,
    PluginSettingResponse,
)
from src.features.plugins.records import Plugin, PluginSetting, PluginHook
from src.platform.plugins.registry import PluginRegistry


def plugin_to_response(plugin: Plugin, registry: PluginRegistry) -> PluginResponse:
    """
    Convert a Plugin model to PluginResponse, enriched with registry state.

    Args:
        plugin: Plugin database model
        registry: PluginRegistry to read runtime state/manifest from

    Returns:
        PluginResponse DTO with runtime state info
    """
    state = registry.get_plugin_state(plugin.id)
    error = registry.get_plugin_error(plugin.id)

    manifest = registry.get_plugin(plugin.id)
    if manifest:
        category = manifest.category
        tags = manifest.tags
        capabilities = manifest.capabilities
        source = manifest.source
        homepage = manifest.homepage
        repository = manifest.repository
        hook_count = len(manifest.hooks) + len(manifest.frontend_hooks)
        settings_count = len(manifest.settings)
    else:
        category = "other"
        tags = []
        capabilities = []
        source = "local"
        homepage = None
        repository = None
        hook_count = 0
        settings_count = 0

    return PluginResponse(
        id=plugin.id,
        name=plugin.name,
        version=plugin.version,
        type=plugin.type,
        enabled=plugin.enabled,
        manifest_path=plugin.manifest_path,
        description=plugin.description,
        author=plugin.author,
        installed_at=plugin.installed_at,
        updated_at=plugin.updated_at,
        state=state.value if state else None,
        error=error,
        category=category,
        tags=tags,
        capabilities=capabilities,
        source=source,
        homepage=homepage,
        repository=repository,
        hook_count=hook_count,
        settings_count=settings_count,
    )


def hook_to_response(hook: PluginHook, plugin: Optional[Plugin] = None) -> PluginHookResponse:
    """
    Convert a PluginHook model to PluginHookResponse.

    Args:
        hook: PluginHook database model
        plugin: Optional Plugin for enrichment

    Returns:
        PluginHookResponse DTO
    """
    return PluginHookResponse(
        id=hook.id,
        plugin_id=hook.plugin_id,
        hook_name=hook.hook_name,
        hook_type=hook.hook_type,
        handler_path=hook.handler_path,
        component_path=hook.component_path,
        position=hook.position,
        sort_order=hook.sort_order,
        plugin_name=plugin.name if plugin else None,
        plugin_version=plugin.version if plugin else None
    )


def setting_to_response(setting: PluginSetting) -> PluginSettingResponse:
    """
    Convert a PluginSetting model to PluginSettingResponse.

    Args:
        setting: PluginSetting database model

    Returns:
        PluginSettingResponse DTO
    """
    return PluginSettingResponse(
        id=setting.id,
        plugin_id=setting.plugin_id,
        setting_key=setting.setting_key,
        setting_value='***' if setting.is_secret else setting.setting_value,
        user_id=setting.user_id,
        is_secret=setting.is_secret
    )
