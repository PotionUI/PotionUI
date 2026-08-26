"""
Discover plugins on disk and sync them into the database.
"""
import logging
from datetime import datetime

from src.features.plugins.dto import PluginScanResult
from src.features.plugins.mappers import plugin_to_response
from src.features.plugins.repository import PluginRepository
from src.features.plugins.records import Plugin, PluginHook, PluginPage
from src.platform.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


def _register_plugin_hooks(repo: PluginRepository, manifest) -> None:
    """
    Register hooks and pages for a new plugin.

    Args:
        repo: PluginRepository
        manifest: PluginManifest from registry
    """
    # Register backend hooks
    for hook_name, handler_path in manifest.hooks.items():
        hook = PluginHook(
            id=None,  # Auto-generated
            plugin_id=manifest.id,
            hook_name=hook_name,
            hook_type="backend",
            handler_path=handler_path,
            component_path=None,
            position=None,
            sort_order=0
        )
        repo.register_hook(hook)

    # Register frontend hooks
    for frontend_hook in manifest.frontend_hooks:
        hook = PluginHook(
            id=None,  # Auto-generated
            plugin_id=manifest.id,
            hook_name=frontend_hook['hook_name'],
            hook_type="frontend",
            handler_path=frontend_hook.get('handler_path'),
            component_path=frontend_hook.get('component_path'),
            position=frontend_hook.get('position'),
            sort_order=frontend_hook.get('sort_order', 0)
        )
        repo.register_hook(hook)

    # Register pages from manifest
    for page_def in manifest.pages:
        route = page_def.get('route', '')
        component = page_def.get('component', '')
        label = page_def.get('label', '')

        if not route or not component or not label:
            logger.warning(f"Skipping incomplete page definition in plugin {manifest.id}")
            continue

        # Check sidebar items for matching route to get icon/order
        icon_svg = None
        sidebar_order = 100
        show_in_sidebar = False
        require_role = None

        for sidebar_item in manifest.sidebar_items:
            if sidebar_item.get('route') == route:
                icon_svg = sidebar_item.get('icon')
                sidebar_order = sidebar_item.get('order', 100)
                show_in_sidebar = True
                require_role = sidebar_item.get('require_role')
                break

        page = PluginPage(
            id=None,
            plugin_id=manifest.id,
            route=route,
            component_path=component,
            label=label,
            icon_svg=icon_svg,
            sidebar_order=sidebar_order,
            show_in_sidebar=show_in_sidebar,
            require_role=require_role
        )
        repo.create_plugin_page(page)


def _refresh_plugin_hooks(repo: PluginRepository, manifest) -> None:
    """
    Refresh hooks and pages for an existing plugin.

    Args:
        repo: PluginRepository
        manifest: PluginManifest from registry
    """
    # Clear existing hooks and pages
    repo.clear_plugin_hooks(manifest.id)
    repo.delete_plugin_pages(manifest.id)

    # Re-register all hooks and pages
    _register_plugin_hooks(repo, manifest)


def scan_plugins(repo: PluginRepository, registry: PluginRegistry) -> PluginScanResult:
    """
    Rescan plugin directories to discover new plugins.

    Returns:
        PluginScanResult with new and updated plugins
    """
    # Force plugin discovery in registry
    registry.discover_plugins()

    # Get all discovered plugins from registry
    registry_plugins = registry.get_all_plugins()

    # Get all plugins from database
    db_plugins = {p.id: p for p in repo.get_all_plugins()}

    new_plugins = []
    updated_plugins = []

    for manifest in registry_plugins:
        if manifest.id not in db_plugins:
            # New plugin - create in database
            plugin = Plugin(
                id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                type=manifest.plugin_type,
                enabled=False,  # New plugins start disabled
                manifest_path=str(manifest.manifest_path),
                description=manifest.description,
                author=manifest.author,
                installed_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            created_plugin = repo.create_plugin(plugin)
            new_plugins.append(plugin_to_response(created_plugin, registry))

            # Register backend hooks in database
            _register_plugin_hooks(repo, manifest)

            logger.info(f"Discovered new plugin: {manifest.name} ({manifest.id})")
        else:
            # Existing plugin - check for version updates
            db_plugin = db_plugins[manifest.id]
            version_changed = db_plugin.version != manifest.version

            if version_changed:
                db_plugin.version = manifest.version
                db_plugin.description = manifest.description
                db_plugin.author = manifest.author
                db_plugin.updated_at = datetime.utcnow()

                updated_plugin = repo.update_plugin(manifest.id, db_plugin)
                updated_plugins.append(plugin_to_response(updated_plugin, registry))
                logger.info(f"Updated plugin: {manifest.name} ({manifest.id})")

            # Always refresh hooks for existing plugins
            _refresh_plugin_hooks(repo, manifest)

            logger.info(f"Refreshed hooks for plugin: {manifest.name} ({manifest.id})")

    return PluginScanResult(
        new_plugins=new_plugins,
        updated_plugins=updated_plugins,
        total_discovered=len(registry_plugins)
    )
