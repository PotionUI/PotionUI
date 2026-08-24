"""
Plugin domain manager.

Handles all business logic for plugin management.
Framework-agnostic - uses ValueError for errors (controller converts to HTTP responses).
"""
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime


from src.features.plugins.dto import (
    PluginResponse,
    PluginDetailResponse,
    PluginHookResponse,
    PluginSettingResponse,
    PluginPageResponse,
    PluginScanResult,
)
from src.features.plugins.repository import PluginRepository
from src.features.plugins.records import Plugin, PluginSetting, PluginHook, PluginPage
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.hooks import hooks_registry
from src.platform.plugins.lifecycle_hooks import PLUGIN_LIFECYCLE_HOOKS

logger = logging.getLogger(__name__)


class PluginManifestUnavailableError(RuntimeError):
    """The registry holds no manifest for a plugin whose settings are being written.

    Not a ValueError: the plugin exists, so this is not "plugin not found" and
    must not be answered with a 404. It is a transient inability to tell a
    credential from an ordinary setting.
    """


class PluginManager:
    """
    Coordinates plugin operations.

    Handles CRUD for plugins, settings management,
    scanning, and frontend hooks grouping.
    """

    def __init__(
        self,
        plugin_repository: PluginRepository,
        plugin_registry: PluginRegistry,
        preset_loader: Optional[Any] = None,
        pipe_catalog: Optional[Any] = None,
        recipe_catalog: Optional[Any] = None,
    ):
        self.repo = plugin_repository
        self.registry = plugin_registry
        # Optional: when wired (see src/bootstrap/container.py), an
        # enable/disable/delete rescans all three surfaces so plugin-shipped
        # presets, plugin-contributed modes, plugin-shipped pipes, and
        # plugin-shipped setup recipes appear/disappear live instead of
        # needing a backend restart.
        # Untyped (Any) and duck-typed to their ``reload()``/
        # ``rescan_plugin_pipes()`` methods, rather than importing
        # PresetTemplateLoader/PipeCatalog/RecipeCatalog here, purely to keep
        # this module decoupled from collaborators it otherwise has no reason
        # to know the full interface of.
        self.preset_loader = preset_loader
        self.pipe_catalog = pipe_catalog
        self.recipe_catalog = recipe_catalog

    def _plugin_to_response(self, plugin: Plugin) -> PluginResponse:
        """
        Convert a Plugin model to PluginResponse, enriched with registry state.

        Args:
            plugin: Plugin database model

        Returns:
            PluginResponse DTO with runtime state info
        """
        state = self.registry.get_plugin_state(plugin.id)
        error = self.registry.get_plugin_error(plugin.id)

        manifest = self.registry.get_plugin(plugin.id)
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

    def _hook_to_response(self, hook: PluginHook, plugin: Optional[Plugin] = None) -> PluginHookResponse:
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

    def _setting_to_response(self, setting: PluginSetting) -> PluginSettingResponse:
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

    def _notify_plugin_lifecycle(self, level: str, title: str, message: str) -> None:
        """
        Broadcast a system notification for a plugin lifecycle event.

        Uses `get_global_notification_manager()` rather than an injected
        dependency - PluginManager is constructed very early in
        build_container(), before the notification manager exists, so a
        constructor dependency would create an ordering problem. Swallows
        RuntimeError (not yet initialized) and any other failure - a
        notification must never break plugin enable/disable.
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

    # ========== Plugin Operations ==========

    def list_plugins(self) -> List[PluginResponse]:
        """
        Get all plugins with their runtime state.

        Returns:
            List of PluginResponse DTOs
        """
        plugins = self.repo.get_all_plugins()
        return [self._plugin_to_response(plugin) for plugin in plugins]

    def get_plugin(self, plugin_id: str) -> PluginDetailResponse:
        """
        Get detailed plugin information.

        Args:
            plugin_id: Plugin identifier

        Raises:
            ValueError: If plugin not found

        Returns:
            PluginDetailResponse with hooks and settings
        """
        plugin = self.repo.get_plugin_by_id(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        base = self._plugin_to_response(plugin)

        # Get hooks
        hooks = self.repo.get_plugin_hooks(plugin_id)
        hook_responses = [self._hook_to_response(hook) for hook in hooks]

        # Get settings schema from manifest
        manifest = self.registry.get_plugin(plugin_id)
        settings_schema = manifest.settings if manifest and manifest.settings else []

        # Get current settings values
        settings = self.repo.get_plugin_settings(plugin_id)
        settings_values = {}
        for setting in settings:
            settings_values[setting.setting_key] = (
                '***' if setting.is_secret else setting.setting_value
            )

        return PluginDetailResponse(
            **base.model_dump(),
            hooks=hook_responses,
            settings_schema=settings_schema,
            settings_values=settings_values
        )

    def _rescan_presets_and_pipes(self, reason: str) -> None:
        """Refresh presets + pipes + recipes after a plugin enable/disable/delete.

        Reads the CURRENT enabled set from ``self.registry`` (already updated
        by the caller before this runs), so this is correct regardless of call
        order. Best-effort: a rescan failure is logged, never raised - a
        plugin toggle must not be reported as failed just because the preset/
        pipe/recipe rescan that follows it hit a snag; the registry state
        change itself already succeeded and is the thing the caller's
        contract is about. Any collaborator being unwired (``None``, e.g. a
        test harness that doesn't need this) is silently skipped.
        """
        if self.preset_loader is not None:
            try:
                self.preset_loader.reload()
            except Exception:
                logger.exception(f"Preset rescan failed after {reason}")
        if self.pipe_catalog is not None:
            try:
                self.pipe_catalog.rescan_plugin_pipes()
            except Exception:
                logger.exception(f"Pipe rescan failed after {reason}")
        if self.recipe_catalog is not None:
            try:
                self.recipe_catalog.reload()
            except Exception:
                logger.exception(f"Recipe rescan failed after {reason}")

    def enable_plugin(self, plugin_id: str) -> PluginResponse:
        """
        Enable a plugin and register its hooks.

        Args:
            plugin_id: Plugin identifier

        Raises:
            ValueError: If plugin not found or enable fails

        Returns:
            Updated PluginResponse
        """
        plugin = self.repo.get_plugin_by_id(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        # Enable in database
        if not self.repo.enable_plugin(plugin_id):
            self._notify_plugin_lifecycle('error', f"Failed to enable plugin '{plugin_id}'", "Failed to enable plugin in database")
            raise ValueError("Failed to enable plugin in database")

        # Enable in registry (loads and registers hooks)
        success = self.registry.enable_plugin(plugin_id)
        if not success:
            # Rollback database change
            self.repo.disable_plugin(plugin_id)

            # Get error from registry
            error = self.registry.get_plugin_error(plugin_id)
            self._notify_plugin_lifecycle(
                'error', f"Failed to enable plugin '{plugin_id}'", error or 'Unknown error'
            )
            raise ValueError(f"Failed to enable plugin in registry: {error or 'Unknown error'}")

        logger.info(f"Enabled plugin: {plugin_id}")
        self._notify_plugin_lifecycle('success', f"Plugin '{plugin_id}' enabled", "")

        # Declare hook points this plugin provides for others to hook into
        manifest = self.registry.get_plugin(plugin_id)
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
        self.registry.hook_chain.execute(
            PLUGIN_LIFECYCLE_HOOKS.enable,
            initial_data={"plugin_id": plugin_id}
        )

        # A plugin enabled mid-process still needs its per-process init: the
        # boot hook the startup resync fires for plugins already enabled in the
        # database. Order matters - `enable` is the transition, `boot` follows it.
        self.registry.run_boot_hook(plugin_id)

        self._rescan_presets_and_pipes(f"enabling plugin '{plugin_id}'")

        # Refresh plugin from DB and return
        updated_plugin = self.repo.get_plugin_by_id(plugin_id)
        return self._plugin_to_response(updated_plugin)

    def disable_plugin(self, plugin_id: str) -> PluginResponse:
        """
        Disable a plugin and unregister its hooks.

        Args:
            plugin_id: Plugin identifier

        Raises:
            ValueError: If plugin not found or disable fails

        Returns:
            Updated PluginResponse
        """
        plugin = self.repo.get_plugin_by_id(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        # Execute lifecycle disable hooks before unregistering
        self.registry.hook_chain.execute(
            PLUGIN_LIFECYCLE_HOOKS.disable,
            initial_data={"plugin_id": plugin_id}
        )

        # Disable in registry (unregisters hooks)
        success = self.registry.disable_plugin(plugin_id)
        if not success:
            self._notify_plugin_lifecycle(
                'error', f"Failed to disable plugin '{plugin_id}'", "Failed to disable plugin in registry"
            )
            raise ValueError("Failed to disable plugin in registry")

        # Disable in database
        if not self.repo.disable_plugin(plugin_id):
            self._notify_plugin_lifecycle(
                'error', f"Failed to disable plugin '{plugin_id}'", "Failed to disable plugin in database"
            )
            raise ValueError("Failed to disable plugin in database")

        logger.info(f"Disabled plugin: {plugin_id}")
        self._rescan_presets_and_pipes(f"disabling plugin '{plugin_id}'")

        # Refresh plugin from DB and return
        updated_plugin = self.repo.get_plugin_by_id(plugin_id)
        return self._plugin_to_response(updated_plugin)

    def delete_plugin(self, plugin_id: str) -> str:
        """
        Delete a plugin from the database (does not remove files).

        Args:
            plugin_id: Plugin identifier

        Raises:
            ValueError: If plugin not found or delete fails

        Returns:
            Plugin name for confirmation message
        """
        plugin = self.repo.get_plugin_by_id(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        plugin_name = plugin.name

        # Disable in registry first if enabled
        was_enabled = plugin.enabled
        if was_enabled:
            self.registry.disable_plugin(plugin_id)

        # Delete from database (cascades to hooks and settings)
        if not self.repo.delete_plugin(plugin_id):
            raise ValueError("Failed to delete plugin from database")

        if was_enabled:
            self._rescan_presets_and_pipes(f"deleting enabled plugin '{plugin_id}'")

        logger.info(f"Deleted plugin: {plugin_id}")
        return plugin_name

    def scan_plugins(self) -> PluginScanResult:
        """
        Rescan plugin directories to discover new plugins.

        Returns:
            PluginScanResult with new and updated plugins
        """
        # Force plugin discovery in registry
        self.registry.discover_plugins()

        # Get all discovered plugins from registry
        registry_plugins = self.registry.get_all_plugins()

        # Get all plugins from database
        db_plugins = {p.id: p for p in self.repo.get_all_plugins()}

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
                created_plugin = self.repo.create_plugin(plugin)
                new_plugins.append(self._plugin_to_response(created_plugin))

                # Register backend hooks in database
                self._register_plugin_hooks(manifest)

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

                    updated_plugin = self.repo.update_plugin(manifest.id, db_plugin)
                    updated_plugins.append(self._plugin_to_response(updated_plugin))
                    logger.info(f"Updated plugin: {manifest.name} ({manifest.id})")

                # Always refresh hooks for existing plugins
                self._refresh_plugin_hooks(manifest)

                logger.info(f"Refreshed hooks for plugin: {manifest.name} ({manifest.id})")

        return PluginScanResult(
            new_plugins=new_plugins,
            updated_plugins=updated_plugins,
            total_discovered=len(registry_plugins)
        )

    def _register_plugin_hooks(self, manifest) -> None:
        """
        Register hooks and pages for a new plugin.

        Args:
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
            self.repo.register_hook(hook)

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
            self.repo.register_hook(hook)

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
            self.repo.create_plugin_page(page)

    def _refresh_plugin_hooks(self, manifest) -> None:
        """
        Refresh hooks and pages for an existing plugin.

        Args:
            manifest: PluginManifest from registry
        """
        # Clear existing hooks and pages
        self.repo.clear_plugin_hooks(manifest.id)
        self.repo.delete_plugin_pages(manifest.id)

        # Re-register all hooks and pages
        self._register_plugin_hooks(manifest)

    # ========== Settings Operations ==========

    def get_plugin_settings(
        self,
        plugin_id: str,
        user_id: Optional[str] = None
    ) -> List[PluginSettingResponse]:
        """
        Get all settings for a plugin.

        Args:
            plugin_id: Plugin identifier
            user_id: Optional user ID for user-specific settings

        Raises:
            ValueError: If plugin not found

        Returns:
            List of PluginSettingResponse DTOs
        """
        plugin = self.repo.get_plugin_by_id(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        settings = self.repo.get_plugin_settings(plugin_id, user_id)
        return [self._setting_to_response(setting) for setting in settings]

    def _secret_setting_keys(self, plugin_id: str) -> Optional[set]:
        """Setting names the plugin's manifest marks `is_secret`.

        The manifest is the only authority on what is a credential; core does
        not guess from key names and knows no plugin by name.

        Returns None - distinct from an empty set - when the registry holds no
        manifest. "This plugin declares no secrets" and "we cannot find out
        what this plugin's secrets are" are different answers, and collapsing
        them is what let a credential be stored in the clear.
        """
        manifest = self.registry.get_plugin(plugin_id)
        if not manifest:
            return None
        if not manifest.settings:
            return set()
        return {
            spec.name for spec in manifest.settings
            if getattr(spec, 'is_secret', False)
        }

    def update_plugin_settings(
        self,
        plugin_id: str,
        settings: Dict[str, Any],
        user_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_username: Optional[str] = None,
    ) -> List[PluginSettingResponse]:
        """
        Update plugin settings (batch update).

        Args:
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
        plugin = self.repo.get_plugin_by_id(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        secret_keys = self._secret_setting_keys(plugin_id)
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
            setting = self.repo.set_plugin_setting(
                plugin_id=plugin_id,
                setting_key=key,
                setting_value=value_str,
                user_id=user_id,
                is_secret=is_secret
            )
            self.repo.record_setting_change(
                plugin_id=plugin_id,
                setting_key=key,
                action='set',
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                scope_user_id=user_id,
                is_secret=is_secret,
            )
            updated_settings.append(self._setting_to_response(setting))

        logger.info(f"Updated {len(updated_settings)} settings for plugin {plugin_id}")

        return updated_settings

    def encrypt_declared_secrets(self) -> int:
        """Flag and encrypt settings their manifest calls secrets but the row does not.

        Every save used to force ``is_secret=False``, so a credential a manifest
        declared could be sitting unflagged - and therefore unmasked and
        unencrypted. Migration 111 cannot find these: it keys off the flag, and
        the manifests it would need are only loaded once the registry exists.

        Returns the number of settings promoted.
        """
        promoted = 0
        for plugin in self.repo.get_all_plugins():
            # None (no manifest) and an empty set are both "nothing to promote"
            # here - this pass only ever adds the flag a manifest asks for.
            secret_keys = self._secret_setting_keys(plugin.id)
            if not secret_keys:
                continue
            for setting in self.repo.get_plugin_settings(plugin.id):
                if setting.is_secret or setting.setting_key not in secret_keys:
                    continue
                if not setting.setting_value:
                    continue
                self.repo.set_plugin_setting(
                    plugin_id=plugin.id,
                    setting_key=setting.setting_key,
                    setting_value=setting.setting_value,
                    user_id=setting.user_id,
                    is_secret=True,
                )
                promoted += 1
        return promoted

    # ========== Plugin Pages ==========

    def get_active_pages(self) -> List[PluginPageResponse]:
        """
        Get pages from enabled plugins.

        Returns:
            List of PluginPageResponse DTOs sorted by sidebar_order
        """
        pages = self.repo.get_all_active_pages()
        return [
            PluginPageResponse(
                plugin_id=page.plugin_id,
                route=page.route,
                component_path=page.component_path,
                label=page.label,
                icon_svg=page.icon_svg,
                sidebar_order=page.sidebar_order,
                show_in_sidebar=page.show_in_sidebar,
                require_role=getattr(page, 'require_role', None)
            )
            for page in pages
        ]

    def get_sidebar_items(self) -> List[PluginPageResponse]:
        """
        Get sidebar entries from enabled plugins (filtered by show_in_sidebar).

        Returns:
            List of PluginPageResponse DTOs for sidebar display
        """
        pages = self.repo.get_all_active_pages()
        return [
            PluginPageResponse(
                plugin_id=page.plugin_id,
                route=page.route,
                component_path=page.component_path,
                label=page.label,
                icon_svg=page.icon_svg,
                sidebar_order=page.sidebar_order,
                show_in_sidebar=page.show_in_sidebar,
                require_role=getattr(page, 'require_role', None)
            )
            for page in pages
            if page.show_in_sidebar
        ]

    # ========== Quick Actions ==========

    def get_active_quick_actions(self) -> List[Dict[str, Any]]:
        """
        Get quick actions from enabled plugins that have show_quick_actions enabled.

        Returns:
            List of quick action dicts for sidebar display
        """
        actions = []
        enabled_db_plugins = self.repo.get_enabled_plugins()

        for plugin in enabled_db_plugins:
            manifest = self.registry.get_plugin(plugin.id)
            if not manifest or not manifest.quick_actions:
                continue

            # Check show_quick_actions setting (default to True)
            show = True
            db_settings = self.repo.get_plugin_settings(plugin.id)
            for s in db_settings:
                if s.setting_key == "show_quick_actions":
                    show = s.setting_value not in ("false", "False", "0", False)
                    break

            if not show:
                continue

            for action_def in manifest.quick_actions:
                actions.append({
                    "plugin_id": manifest.id,
                    "plugin_name": manifest.name,
                    "action_id": action_def.get("id"),
                    "label": action_def.get("label"),
                    "icon": action_def.get("icon"),
                    "endpoint": action_def.get("endpoint"),
                    "method": action_def.get("method", "POST"),
                    "confirm": action_def.get("confirm"),
                    "require_role": action_def.get("require_role"),
                })

        return actions

    # ========== Sidebar Widgets ==========

    def get_active_sidebar_widgets(self) -> List[Dict[str, Any]]:
        """
        Get sidebar widgets from enabled plugins.

        Returns:
            List of sidebar widget dicts sorted by order
        """
        widgets = []
        enabled_db_plugins = self.repo.get_enabled_plugins()

        for plugin in enabled_db_plugins:
            manifest = self.registry.get_plugin(plugin.id)
            if not manifest or not manifest.sidebar_widgets:
                continue

            for widget_def in manifest.sidebar_widgets:
                widgets.append({
                    "plugin_id": manifest.id,
                    "widget_id": widget_def.get("id"),
                    "position": widget_def.get("position", "bottom"),
                    "component": widget_def.get("component"),
                    "order": widget_def.get("order", 100),
                    "label": widget_def.get("label"),
                })

        widgets.sort(key=lambda w: w["order"])
        return widgets

    # ========== Frontend Extensions (renderers + contributions) ==========

    def get_frontend_extensions(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get manifest-declared `renderers:` and `contributions:` from enabled
        plugins, for the frontend renderer registries (A5) and extension
        slots. Manifest-derived only - no DB tables.

        Returns:
            {"renderers": [...], "contributions": [...]}, each entry
            annotated with its owning `plugin_id`.
        """
        renderers: List[Dict[str, Any]] = []
        contributions: List[Dict[str, Any]] = []
        enabled_db_plugins = self.repo.get_enabled_plugins()

        for plugin in enabled_db_plugins:
            manifest = self.registry.get_plugin(plugin.id)
            if not manifest:
                continue

            for renderer_def in manifest.renderers:
                renderers.append({
                    "plugin_id": manifest.id,
                    "kind": renderer_def.get("kind"),
                    "key": renderer_def.get("key"),
                    "component": renderer_def.get("component"),
                })

            for contribution_def in manifest.contributions:
                contributions.append({
                    "plugin_id": manifest.id,
                    "slot": contribution_def.get("slot"),
                    "component": contribution_def.get("component"),
                    "label": contribution_def.get("label"),
                    "icon": contribution_def.get("icon"),
                    "route": contribution_def.get("route"),
                    "order": contribution_def.get("order", 100),
                    "require_role": contribution_def.get("require_role"),
                })

        contributions.sort(key=lambda c: c["order"])
        return {"renderers": renderers, "contributions": contributions}

    # ========== Frontend Hooks ==========

    def get_grouped_frontend_hooks(self) -> Dict[str, List[PluginHookResponse]]:
        """
        Get all frontend hooks grouped by hook name.

        Returns:
            Dictionary mapping hook names to lists of PluginHookResponse
        """
        hooks = self.repo.get_hooks_by_type("frontend")

        grouped_hooks: Dict[str, List[PluginHookResponse]] = {}
        for hook in hooks:
            # Get plugin info for enrichment
            plugin = self.repo.get_plugin_by_id(hook.plugin_id)
            hook_response = self._hook_to_response(hook, plugin)

            if hook.hook_name not in grouped_hooks:
                grouped_hooks[hook.hook_name] = []

            grouped_hooks[hook.hook_name].append(hook_response)

        return grouped_hooks

    def get_hooks_catalog(self) -> List[Dict[str, Any]]:
        """
        Get the full catalog of declared hook points (core + plugin-provided).

        Returns:
            List of {name, type, description, payload, mutable, use_when, example} dicts.
            Fields with no documentation are present with empty values (stable shape).
        """
        return [
            {
                "name": spec.name,
                "type": spec.type,
                "description": spec.description,
                "payload": dict(spec.payload),
                "mutable": list(spec.mutable),
                "use_when": list(spec.use_when),
                "example": spec.example,
            }
            for spec in sorted(hooks_registry.all(), key=lambda s: s.name)
        ]
