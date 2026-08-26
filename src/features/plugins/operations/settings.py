"""
Read/write a plugin's settings, and the credential-flag promotion pass.

The manifest is the only authority on what is a secret: core does not guess
from key names and knows no plugin by name.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from src.features.plugins.dto import PluginSettingResponse
from src.features.plugins.mappers import setting_to_response
from src.features.plugins.repository import PluginRepository
from src.platform.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


class PluginManifestUnavailableError(RuntimeError):
    """The registry holds no manifest for a plugin whose settings are being written.

    Not a ValueError: the plugin exists, so this is not "plugin not found" and
    must not be answered with a 404. It is a transient inability to tell a
    credential from an ordinary setting.
    """


def _secret_setting_keys(registry: PluginRegistry, plugin_id: str) -> Optional[set]:
    """Setting names the plugin's manifest marks `is_secret`.

    Returns None - distinct from an empty set - when the registry holds no
    manifest. "This plugin declares no secrets" and "we cannot find out
    what this plugin's secrets are" are different answers, and collapsing
    them is what let a credential be stored in the clear.
    """
    manifest = registry.get_plugin(plugin_id)
    if not manifest:
        return None
    if not manifest.settings:
        return set()
    return {
        spec.name for spec in manifest.settings
        if getattr(spec, 'is_secret', False)
    }


def update_plugin_settings(
    repo: PluginRepository,
    registry: PluginRegistry,
    plugin_id: str,
    settings: Dict[str, Any],
    user_id: Optional[str] = None,
    *,
    actor_user_id: Optional[str] = None,
    actor_username: Optional[str] = None,
) -> List[PluginSettingResponse]:
    """
    Update plugin settings (batch update).

    Args:
        repo: PluginRepository
        registry: PluginRegistry
        plugin_id: Plugin identifier
        settings: Dictionary of key-value pairs to update
        user_id: Optional user ID for user-specific settings
        actor_user_id: Who is making the change, for the audit trail
        actor_username: Their username, denormalized so the trail survives
            a user deletion

    Raises:
        ValueError: If plugin not found
        PluginManifestUnavailableError: If the plugin's manifest cannot be
            read, so core cannot tell which keys are credentials

    Returns:
        List of updated PluginSettingResponse DTOs
    """
    plugin = repo.get_plugin_by_id(plugin_id)
    if not plugin:
        raise ValueError(f"Plugin '{plugin_id}' not found")

    secret_keys = _secret_setting_keys(registry, plugin_id)
    if secret_keys is None:
        # Without the manifest every key looks ordinary, so a credential
        # typed now would be stored in the clear AND unflagged - unflagged
        # meaning it is also handed back unmasked, and that later passes
        # such as encrypt_declared_secrets have no way to find it. Refuse
        # the whole batch rather than write a plaintext secret: the operator
        # can reload the plugin and save again, but cannot un-leak a key.
        raise PluginManifestUnavailableError(
            f"Plugin '{plugin_id}' has no readable manifest, so its credential "
            f"settings cannot be identified. Settings were not saved. Reload "
            f"the plugin and try again."
        )
    updated_settings = []

    # Update each setting
    for key, value in settings.items():
        # Serialize value for storage
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value)
        else:
            value_str = str(value)

        is_secret = key in secret_keys
        setting = repo.set_plugin_setting(
            plugin_id=plugin_id,
            setting_key=key,
            setting_value=value_str,
            user_id=user_id,
            is_secret=is_secret
        )
        repo.record_setting_change(
            plugin_id=plugin_id,
            setting_key=key,
            action='set',
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            scope_user_id=user_id,
            is_secret=is_secret,
        )
        updated_settings.append(setting_to_response(setting))

    logger.info(f"Updated {len(updated_settings)} settings for plugin {plugin_id}")

    return updated_settings


def encrypt_declared_secrets(repo: PluginRepository, registry: PluginRegistry) -> int:
    """Flag and encrypt settings their manifest calls secrets but the row does not.

    Every save used to force ``is_secret=False``, so a credential a manifest
    declared could be sitting unflagged - and therefore unmasked and
    unencrypted. Migration 111 cannot find these: it keys off the flag, and
    the manifests it would need are only loaded once the registry exists.

    Returns the number of settings promoted.
    """
    promoted = 0
    for plugin in repo.get_all_plugins():
        # None (no manifest) and an empty set are both "nothing to promote"
        # here - this pass only ever adds the flag a manifest asks for.
        secret_keys = _secret_setting_keys(registry, plugin.id)
        if not secret_keys:
            continue
        for setting in repo.get_plugin_settings(plugin.id):
            if setting.is_secret or setting.setting_key not in secret_keys:
                continue
            if not setting.setting_value:
                continue
            repo.set_plugin_setting(
                plugin_id=plugin.id,
                setting_key=setting.setting_key,
                setting_value=setting.setting_value,
                user_id=setting.user_id,
                is_secret=True,
            )
            promoted += 1
    return promoted
