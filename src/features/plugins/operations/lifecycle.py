"""
Enable/disable/delete a plugin.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for "not
found"/"failed" (the controller converts that to an HTTP response).
"""
import logging
from typing import Any, Optional

from src.features.plugins.dto import PluginResponse
from src.features.plugins.mappers import plugin_to_response
from src.features.plugins.repository import PluginRepository
from src.platform.plugins.registry import PluginRegistry
from src.platform.plugins.hooks import hooks_registry
from src.platform.plugins.lifecycle_hooks import PLUGIN_LIFECYCLE_HOOKS

logger = logging.getLogger(__name__)


def _notify_plugin_lifecycle(level: str, title: str, message: str) -> None:
    """
    Broadcast a system notification for a plugin lifecycle event.

    Uses `get_global_notification_manager()` rather than a passed-in
    collaborator - this can run during startup, before the notification
    manager exists. Swallows RuntimeError (not yet initialized) and any other
    failure - a notification must never break plugin enable/disable.
    """
    try:
        from src.platform.plugins.runtime_registries import get_global_notification_manager
        get_global_notification_manager().notify(
            level=level,
            title=title,
            message=message,
            category='system',
            user_id=None,
            source='core',
            type='system.plugins',
        )
    except RuntimeError:
        logger.debug("NotificationManager not initialized yet; skipping plugin lifecycle notification")
    except Exception as e:
        logger.error(f"Failed to send plugin lifecycle notification: {e}")


def _rescan_presets_and_pipes(
    preset_loader: Optional[Any],
    pipe_catalog: Optional[Any],
    recipe_catalog: Optional[Any],
    reason: str,
) -> None:
    """Refresh presets + pipes + recipes after a plugin enable/disable/delete.

    Reads the CURRENT enabled set from the registry (already updated by the
    caller before this runs), so this is correct regardless of call order.
    Best-effort: a rescan failure is logged, never raised - a plugin toggle
    must not be reported as failed just because the preset/pipe/recipe rescan
    that follows it hit a snag; the registry state change itself already
    succeeded and is the thing the caller's contract is about. Any
    collaborator that is ``None`` (e.g. a test harness that doesn't need this)
    is silently skipped.
    """
    if preset_loader is not None:
        try:
            preset_loader.reload()
        except Exception:
            logger.exception(f"Preset rescan failed after {reason}")
    if pipe_catalog is not None:
        try:
            pipe_catalog.rescan_plugin_pipes()
        except Exception:
            logger.exception(f"Pipe rescan failed after {reason}")
    if recipe_catalog is not None:
        try:
            recipe_catalog.reload()
        except Exception:
            logger.exception(f"Recipe rescan failed after {reason}")


def enable_plugin(
    repo: PluginRepository,
    registry: PluginRegistry,
    plugin_id: str,
    *,
    preset_loader: Optional[Any] = None,
    pipe_catalog: Optional[Any] = None,
    recipe_catalog: Optional[Any] = None,
) -> PluginResponse:
    """
    Enable a plugin and register its hooks.

    Args:
        repo: PluginRepository
        registry: PluginRegistry
        plugin_id: Plugin identifier
        preset_loader: Optional collaborator to reload after enabling (live
            preset rescan). Duck-typed to `.reload()`.
        pipe_catalog: Optional collaborator to rescan after enabling.
            Duck-typed to `.rescan_plugin_pipes()`.
        recipe_catalog: Optional collaborator to reload after enabling.
            Duck-typed to `.reload()`.

    Raises:
        ValueError: If plugin not found or enable fails

    Returns:
        Updated PluginResponse
    """
    plugin = repo.get_plugin_by_id(plugin_id)
    if not plugin:
        raise ValueError(f"Plugin '{plugin_id}' not found")

    # Enable in database
    if not repo.enable_plugin(plugin_id):
        _notify_plugin_lifecycle('error', f"Failed to enable plugin '{plugin_id}'", "Failed to enable plugin in database")
        raise ValueError("Failed to enable plugin in database")

    # Enable in registry (loads and registers hooks)
    success = registry.enable_plugin(plugin_id)
    if not success:
        # Rollback database change
        repo.disable_plugin(plugin_id)

        # Get error from registry
        error = registry.get_plugin_error(plugin_id)
        _notify_plugin_lifecycle(
            'error', f"Failed to enable plugin '{plugin_id}'", error or 'Unknown error'
        )
        raise ValueError(f"Failed to enable plugin in registry: {error or 'Unknown error'}")

    logger.info(f"Enabled plugin: {plugin_id}")
    _notify_plugin_lifecycle('success', f"Plugin '{plugin_id}' enabled", "")

    # Declare hook points this plugin provides for others to hook into
    manifest = registry.get_plugin(plugin_id)
    provides_hooks = getattr(manifest, "provides_hooks", None) if manifest else None
    if isinstance(provides_hooks, (list, tuple)):
        for entry in provides_hooks:
            if isinstance(entry, str):
                hooks_registry.declare_one(
                    entry, "backend", description=f"Provided by plugin '{plugin_id}'"
                )
            else:
                hooks_registry.declare_one(
                    entry["name"],
                    "backend",
                    description=entry.get("description") or f"Provided by plugin '{plugin_id}'",
                    payload=entry.get("payload"),
                    mutable=entry.get("mutable"),
                    use_when=entry.get("use_when"),
                    example=entry.get("example", ""),
                )

    # Execute lifecycle enable hooks
    registry.hook_chain.execute(
        PLUGIN_LIFECYCLE_HOOKS.enable,
        initial_data={"plugin_id": plugin_id}
    )

    # A plugin enabled mid-process still needs its per-process init: the
    # boot hook the startup resync fires for plugins already enabled in the
    # database. Order matters - `enable` is the transition, `boot` follows it.
    registry.run_boot_hook(plugin_id)

    _rescan_presets_and_pipes(preset_loader, pipe_catalog, recipe_catalog, f"enabling plugin '{plugin_id}'")

    # Refresh plugin from DB and return
    updated_plugin = repo.get_plugin_by_id(plugin_id)
    return plugin_to_response(updated_plugin, registry)


def disable_plugin(
    repo: PluginRepository,
    registry: PluginRegistry,
    plugin_id: str,
    *,
    preset_loader: Optional[Any] = None,
    pipe_catalog: Optional[Any] = None,
    recipe_catalog: Optional[Any] = None,
) -> PluginResponse:
    """
    Disable a plugin and unregister its hooks.

    Raises:
        ValueError: If plugin not found or disable fails

    Returns:
        Updated PluginResponse
    """
    plugin = repo.get_plugin_by_id(plugin_id)
    if not plugin:
        raise ValueError(f"Plugin '{plugin_id}' not found")

    # Execute lifecycle disable hooks before unregistering
    registry.hook_chain.execute(
        PLUGIN_LIFECYCLE_HOOKS.disable,
        initial_data={"plugin_id": plugin_id}
    )

    # Disable in registry (unregisters hooks)
    success = registry.disable_plugin(plugin_id)
    if not success:
        _notify_plugin_lifecycle(
            'error', f"Failed to disable plugin '{plugin_id}'", "Failed to disable plugin in registry"
        )
        raise ValueError("Failed to disable plugin in registry")

    # Disable in database
    if not repo.disable_plugin(plugin_id):
        _notify_plugin_lifecycle(
            'error', f"Failed to disable plugin '{plugin_id}'", "Failed to disable plugin in database"
        )
        raise ValueError("Failed to disable plugin in database")

    logger.info(f"Disabled plugin: {plugin_id}")
    _rescan_presets_and_pipes(preset_loader, pipe_catalog, recipe_catalog, f"disabling plugin '{plugin_id}'")

    # Refresh plugin from DB and return
    updated_plugin = repo.get_plugin_by_id(plugin_id)
    return plugin_to_response(updated_plugin, registry)


def delete_plugin(
    repo: PluginRepository,
    registry: PluginRegistry,
    plugin_id: str,
    *,
    preset_loader: Optional[Any] = None,
    pipe_catalog: Optional[Any] = None,
    recipe_catalog: Optional[Any] = None,
) -> str:
    """
    Delete a plugin from the database (does not remove files).

    Raises:
        ValueError: If plugin not found or delete fails

    Returns:
        Plugin name for confirmation message
    """
    plugin = repo.get_plugin_by_id(plugin_id)
    if not plugin:
        raise ValueError(f"Plugin '{plugin_id}' not found")

    plugin_name = plugin.name

    # Disable in registry first if enabled
    was_enabled = plugin.enabled
    if was_enabled:
        registry.disable_plugin(plugin_id)

    # Delete from database (cascades to hooks and settings)
    if not repo.delete_plugin(plugin_id):
        raise ValueError("Failed to delete plugin from database")

    if was_enabled:
        _rescan_presets_and_pipes(preset_loader, pipe_catalog, recipe_catalog, f"deleting enabled plugin '{plugin_id}'")

    logger.info(f"Deleted plugin: {plugin_id}")
    return plugin_name
