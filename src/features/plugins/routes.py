import logging
from typing import Any, List, Optional, TYPE_CHECKING
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.plugins.dto import (
    PluginDetailResponse,
    PluginPageResponse,
    PluginSettingsUpdateRequest,
)
from src.features.plugins.mappers import hook_to_response, plugin_to_response, setting_to_response
from src.features.plugins import operations
from src.features.plugins.operations import PluginManifestUnavailableError
from src.features.plugins.repository import PluginRepository
from src.platform.plugins.registry import PluginRegistry
from src.features.providers.registry import reset_provider_registry

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class PluginController(BaseController):
    def __init__(
        self,
        plugin_repository: PluginRepository,
        plugin_registry: PluginRegistry,
        preset_loader: Optional[Any] = None,
        pipe_catalog: Optional[Any] = None,
        recipe_catalog: Optional[Any] = None,
    ):
        super().__init__()
        self.repository = plugin_repository
        self.registry = plugin_registry
        # Wired in build_container() so enable/disable/delete rescan presets,
        # plugin-shipped pipes, and setup recipes live - see
        # operations._rescan_presets_and_pipes. Untyped (Any) and duck-typed
        # to their reload()/rescan_plugin_pipes() methods, purely to keep this
        # module decoupled from collaborators it has no other reason to know
        # the full interface of.
        self.preset_loader = preset_loader
        self.pipe_catalog = pipe_catalog
        self.recipe_catalog = recipe_catalog

    # ========== Plugin Pages ==========

    def _get_active_pages(self) -> List[PluginPageResponse]:
        """Pages from enabled plugins, sorted by sidebar_order. Pure DB read."""
        pages = self.repository.get_all_active_pages()
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

    async def get_plugin_pages(self) -> APIResponse:
        """Get all active plugin pages sorted by sidebar_order"""
        try:
            pages = self._get_active_pages()
            return self.success_response(data=[p.model_dump() for p in pages])
        except Exception as e:
            self.logger.error(f"Failed to get plugin pages: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_pages_get_failed",
                message="Failed to get plugin pages"
            )

    async def get_plugin_page_by_route(self, route: str) -> APIResponse:
        """Get page info for a specific route"""
        try:
            pages = self._get_active_pages()
            matching = [p for p in pages if p.route == route]
            if not matching:
                return self.error_response(
                    error="plugin_page_not_found",
                    message=f"No plugin page found for route '{route}'",
                    status_code=404
                )
            return self.success_response(data=matching[0].model_dump())
        except Exception as e:
            self.logger.error(f"Failed to get plugin page for route {route}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_page_get_failed",
                message="Failed to get plugin page"
            )

    async def get_sidebar_items(self) -> APIResponse:
        """Get sidebar items from active plugins"""
        try:
            items = [p for p in self._get_active_pages() if p.show_in_sidebar]
            return self.success_response(data=[i.model_dump() for i in items])
        except Exception as e:
            self.logger.error(f"Failed to get sidebar items: {str(e)}")
            return self.handle_exception(
                e,
                error_code="sidebar_items_get_failed",
                message="Failed to get sidebar items"
            )

    # ========== Quick Actions ==========

    async def get_plugin_quick_actions(self) -> APIResponse:
        """Get quick actions from enabled plugins"""
        try:
            actions = operations.get_active_quick_actions(self.repository, self.registry)
            return self.success_response(data=actions)
        except Exception as e:
            self.logger.error(f"Failed to get plugin quick actions: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_quick_actions_get_failed",
                message="Failed to get plugin quick actions"
            )

    # ========== Sidebar Widgets ==========

    async def get_sidebar_widgets(self) -> APIResponse:
        """Get sidebar widgets from enabled plugins"""
        try:
            widgets = operations.get_active_sidebar_widgets(self.repository, self.registry)
            return self.success_response(data=widgets)
        except Exception as e:
            self.logger.error(f"Failed to get sidebar widgets: {str(e)}")
            return self.handle_exception(
                e,
                error_code="sidebar_widgets_get_failed",
                message="Failed to get sidebar widgets"
            )

    # ========== Plugin Management ==========

    async def list_plugins(self) -> APIResponse:
        """List all plugins with their status"""
        try:
            plugins = self.repository.get_all_plugins()
            responses = [plugin_to_response(p, self.registry) for p in plugins]
            return self.success_response(data=[p.model_dump() for p in responses])
        except Exception as e:
            self.logger.error(f"Failed to list plugins: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_list_failed",
                message="Failed to list plugins"
            )

    async def get_plugin(self, plugin_id: str) -> APIResponse:
        """Get single plugin details"""
        try:
            plugin = self.repository.get_plugin_by_id(plugin_id)
            if not plugin:
                raise ValueError(f"Plugin '{plugin_id}' not found")

            base = plugin_to_response(plugin, self.registry)

            hooks = self.repository.get_plugin_hooks(plugin_id)
            hook_responses = [hook_to_response(hook) for hook in hooks]

            manifest = self.registry.get_plugin(plugin_id)
            settings_schema = manifest.settings if manifest and manifest.settings else []

            settings = self.repository.get_plugin_settings(plugin_id)
            settings_values = {
                setting.setting_key: ('***' if setting.is_secret else setting.setting_value)
                for setting in settings
            }

            detail = PluginDetailResponse(
                **base.model_dump(),
                hooks=hook_responses,
                settings_schema=settings_schema,
                settings_values=settings_values
            )
            return self.success_response(data=detail.model_dump())
        except ValueError as e:
            return self.error_response(
                error="plugin_not_found",
                message=str(e),
                status_code=404
            )
        except Exception as e:
            self.logger.error(f"Failed to get plugin {plugin_id}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_get_failed",
                message="Failed to get plugin"
            )

    async def enable_plugin(self, plugin_id: str) -> APIResponse:
        """Enable a plugin"""
        try:
            plugin = operations.enable_plugin(
                self.repository,
                self.registry,
                plugin_id,
                preset_loader=self.preset_loader,
                pipe_catalog=self.pipe_catalog,
                recipe_catalog=self.recipe_catalog,
            )

            # Reset provider service to re-discover providers from newly enabled plugin
            reset_provider_registry()

            return self.success_response(
                message=f"Plugin '{plugin.name}' enabled successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_response(
                    error="plugin_not_found",
                    message=error_msg,
                    status_code=404
                )
            return self.error_response(
                error="plugin_enable_failed",
                message=error_msg
            )
        except Exception as e:
            self.logger.error(f"Failed to enable plugin {plugin_id}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_enable_failed",
                message="Failed to enable plugin"
            )

    async def disable_plugin(self, plugin_id: str) -> APIResponse:
        """Disable a plugin"""
        try:
            plugin = operations.disable_plugin(
                self.repository,
                self.registry,
                plugin_id,
                preset_loader=self.preset_loader,
                pipe_catalog=self.pipe_catalog,
                recipe_catalog=self.recipe_catalog,
            )

            # Reset provider service to remove providers from disabled plugin
            reset_provider_registry()

            return self.success_response(
                message=f"Plugin '{plugin.name}' disabled successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_response(
                    error="plugin_not_found",
                    message=error_msg,
                    status_code=404
                )
            return self.error_response(
                error="plugin_disable_failed",
                message=error_msg
            )
        except Exception as e:
            self.logger.error(f"Failed to disable plugin {plugin_id}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_disable_failed",
                message="Failed to disable plugin"
            )

    async def delete_plugin(self, plugin_id: str) -> APIResponse:
        """Delete a plugin from the database (does not remove files)"""
        try:
            plugin_name = operations.delete_plugin(
                self.repository,
                self.registry,
                plugin_id,
                preset_loader=self.preset_loader,
                pipe_catalog=self.pipe_catalog,
                recipe_catalog=self.recipe_catalog,
            )
            return self.success_response(
                message=f"Plugin '{plugin_name}' deleted successfully. Re-scan to rediscover."
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_response(
                    error="plugin_not_found",
                    message=error_msg
                )
            return self.error_response(
                error="plugin_delete_failed",
                message=error_msg
            )
        except Exception as e:
            self.logger.error(f"Failed to delete plugin {plugin_id}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_delete_failed",
                message="Failed to delete plugin"
            )

    async def scan_plugins(self) -> APIResponse:
        """Rescan plugin directories to discover new plugins"""
        try:
            result = operations.scan_plugins(self.repository, self.registry)
            return self.success_response(
                data={
                    "new_plugins": [p.model_dump() for p in result.new_plugins],
                    "updated_plugins": [p.model_dump() for p in result.updated_plugins],
                    "total_discovered": result.total_discovered
                },
                message=f"Scan complete: {len(result.new_plugins)} new, {len(result.updated_plugins)} updated"
            )
        except Exception as e:
            self.logger.error(f"Failed to scan plugins: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_scan_failed",
                message="Failed to scan plugins"
            )

    # ========== Plugin Settings ==========

    async def get_plugin_settings(
        self,
        plugin_id: str,
        user_id: Optional[str] = None
    ) -> APIResponse:
        """Get all settings for a plugin"""
        try:
            plugin = self.repository.get_plugin_by_id(plugin_id)
            if not plugin:
                raise ValueError(f"Plugin '{plugin_id}' not found")

            settings = self.repository.get_plugin_settings(plugin_id, user_id)
            responses = [setting_to_response(s) for s in settings]
            return self.success_response(data=[s.model_dump() for s in responses])
        except ValueError as e:
            return self.error_response(
                error="plugin_not_found",
                message=str(e),
                status_code=404
            )
        except Exception as e:
            self.logger.error(f"Failed to get plugin settings {plugin_id}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_settings_get_failed",
                message="Failed to get plugin settings"
            )

    async def get_setting_audit(
        self,
        plugin_id: Optional[str] = None,
        limit: int = 200
    ) -> APIResponse:
        """Read the plugin settings audit trail"""
        try:
            entries = self.repository.get_setting_audit(plugin_id, limit)
            return self.success_response(data=entries)
        except Exception as e:
            self.logger.error(f"Failed to read plugin settings audit: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_settings_audit_failed",
                message="Failed to read plugin settings audit trail"
            )

    async def update_plugin_settings(
        self,
        plugin_id: str,
        settings_data: PluginSettingsUpdateRequest,
        user_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_username: Optional[str] = None
    ) -> APIResponse:
        """Update plugin settings (batch update)"""
        try:
            updated_settings = operations.update_plugin_settings(
                self.repository,
                self.registry,
                plugin_id,
                settings_data.settings,
                user_id,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
            )
            return self.success_response(
                data=[s.model_dump() for s in updated_settings],
                message=f"Updated {len(updated_settings)} settings"
            )
        except PluginManifestUnavailableError as e:
            # 409, not 404 or 500: the plugin is there, the request was
            # well-formed, and retrying after a reload is the fix.
            self.logger.warning(str(e))
            return self.error_response(
                error="plugin_manifest_unavailable",
                message=str(e),
                status_code=409
            )
        except ValueError as e:
            return self.error_response(
                error="plugin_not_found",
                message=str(e),
                status_code=404
            )
        except Exception as e:
            self.logger.error(f"Failed to update plugin settings {plugin_id}: {str(e)}")
            return self.handle_exception(
                e,
                error_code="plugin_settings_update_failed",
                message="Failed to update plugin settings"
            )

    # ========== Frontend Extensions ==========

    async def get_frontend_extensions(self) -> APIResponse:
        """Get manifest-declared renderers + extension slot contributions from enabled plugins."""
        try:
            extensions = operations.get_frontend_extensions(self.repository, self.registry)
            return self.success_response(data=extensions)
        except Exception as e:
            self.logger.error(f"Failed to get frontend extensions: {str(e)}")
            return self.handle_exception(
                e,
                error_code="frontend_extensions_get_failed",
                message="Failed to get frontend extensions"
            )

    # ========== Frontend Hooks ==========

    async def get_frontend_hooks(self) -> APIResponse:
        """Get all frontend hooks (for frontend component loading)"""
        try:
            hooks = self.repository.get_hooks_by_type("frontend")

            grouped_hooks: dict = {}
            for hook in hooks:
                plugin = self.repository.get_plugin_by_id(hook.plugin_id)
                hook_response = hook_to_response(hook, plugin)
                grouped_hooks.setdefault(hook.hook_name, []).append(hook_response)

            # Convert hook responses to dicts
            result = {
                hook_name: [h.model_dump() for h in hooks]
                for hook_name, hooks in grouped_hooks.items()
            }
            return self.success_response(data=result)
        except Exception as e:
            self.logger.error(f"Failed to get frontend hooks: {str(e)}")
            return self.handle_exception(
                e,
                error_code="frontend_hooks_get_failed",
                message="Failed to get frontend hooks"
            )

    async def get_hooks_catalog(self) -> APIResponse:
        """Get the full catalog of declared hook points (core + plugin-provided)."""
        try:
            catalog = operations.get_hooks_catalog()
            return self.success_response(data=catalog)
        except Exception as e:
            self.logger.error(f"Failed to get hooks catalog: {str(e)}")
            return self.handle_exception(
                e,
                error_code="hooks_catalog_get_failed",
                message="Failed to get hooks catalog"
            )

    # ========== Plugin Assets ==========

    async def get_plugin_asset(self, plugin_id: str, file_path: str) -> FileResponse:
        """
        Serve static assets (JS, CSS) from plugin's frontend/dist directory.

        Args:
            plugin_id: Plugin identifier
            file_path: Relative path to asset file

        Returns:
            FileResponse with the requested asset

        Raises:
            HTTPException: If plugin not found or file doesn't exist
        """
        try:
            # Get plugin manifest from registry to find plugin directory
            plugin_manifest = self.registry.get_plugin(plugin_id)
            if not plugin_manifest:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plugin '{plugin_id}' not found"
                )

            # Construct path to asset: {plugin_dir}/frontend/dist/{file_path}
            asset_path = plugin_manifest.plugin_dir / "frontend" / "dist" / file_path

            # Validate path to prevent directory traversal
            try:
                asset_path = asset_path.resolve()
                allowed_dir = (plugin_manifest.plugin_dir / "frontend" / "dist").resolve()

                # `is_relative_to` on the resolved paths, not `str.startswith`:
                # the latter compares strings, so a sibling directory whose name
                # merely extends the base (".../dist-evil" for base ".../dist")
                # passes containment. Same check as
                # FilePathResolver.validate_path_security.
                if not asset_path.is_relative_to(allowed_dir):
                    self.logger.warning(
                        f"Directory traversal attempt blocked: {file_path} for plugin {plugin_id}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied"
                    )
            except (ValueError, OSError) as e:
                self.logger.error(f"Path validation error: {e}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid file path"
                )

            # Check if file exists
            if not asset_path.exists() or not asset_path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail=f"Asset not found: {file_path}"
                )

            # Determine MIME type based on extension
            mime_types = {
                '.js': 'application/javascript',
                '.mjs': 'application/javascript',
                '.css': 'text/css',
                '.json': 'application/json',
                '.svg': 'image/svg+xml',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
            }

            file_extension = asset_path.suffix.lower()
            media_type = mime_types.get(file_extension, 'application/octet-stream')

            self.logger.debug(
                f"Serving plugin asset: {plugin_id}/{file_path} ({media_type})"
            )

            # Plugin bundles are loaded by dynamic `import()` and rebuilt in
            # place; without an explicit policy browsers apply heuristic
            # freshness to a module URL and keep serving a stale bundle for
            # hours after `build-plugins.mjs` wrote a new one. `no-cache`
            # forces a conditional request each time, answered 304 by the
            # ETag/Last-Modified FileResponse already sends.
            return FileResponse(
                path=str(asset_path),
                media_type=media_type,
                filename=asset_path.name,
                headers={"Cache-Control": "no-cache"},
            )

        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.plugin_controller

    router = APIRouter(prefix="/api/plugins", tags=["Plugins"])

    @router.get("", response_model=APIResponse, summary="List All Plugins")
    async def list_plugins(current_user = Depends(get_current_admin_user)):
        """List all plugins with their status (admin only - plugin management view)."""
        return await controller.list_plugins()

    @router.get("/pages", response_model=APIResponse, summary="Get All Plugin Pages")
    async def get_plugin_pages(current_user = Depends(get_current_active_user)):
        """Get all active plugin pages sorted by sidebar_order."""
        return await controller.get_plugin_pages()

    @router.get("/pages/{route:path}", response_model=APIResponse, summary="Get Plugin Page by Route")
    async def get_plugin_page_by_route(route: str, current_user = Depends(get_current_active_user)):
        """Get page info for a specific route."""
        return await controller.get_plugin_page_by_route(route)

    @router.get("/sidebar", response_model=APIResponse, summary="Get Sidebar Items")
    async def get_sidebar_items(current_user = Depends(get_current_active_user)):
        """Get sidebar items from active plugins."""
        return await controller.get_sidebar_items()

    @router.get("/quick-actions", response_model=APIResponse, summary="Get Plugin Quick Actions")
    async def get_plugin_quick_actions(current_user = Depends(get_current_active_user)):
        """Get quick actions from enabled plugins."""
        return await controller.get_plugin_quick_actions()

    @router.get("/frontend-extensions", response_model=APIResponse, summary="Get Frontend Extensions")
    async def get_frontend_extensions(current_user = Depends(get_current_active_user)):
        """Get manifest-declared renderers + extension slot contributions from enabled plugins."""
        return await controller.get_frontend_extensions()

    @router.get("/sidebar-widgets", response_model=APIResponse, summary="Get Sidebar Widgets")
    async def get_sidebar_widgets(current_user = Depends(get_current_active_user)):
        """Get sidebar widgets from enabled plugins."""
        return await controller.get_sidebar_widgets()

    @router.get("/{plugin_id}", response_model=APIResponse, summary="Get Plugin Details")
    async def get_plugin(plugin_id: str, current_user = Depends(get_current_admin_user)):
        """Get details for a specific plugin (admin only - plugin management view)."""
        return await controller.get_plugin(plugin_id)

    @router.post("/{plugin_id}/enable", response_model=APIResponse, summary="Enable Plugin")
    async def enable_plugin(plugin_id: str, current_user = Depends(get_current_admin_user)):
        """Enable a plugin and register its hooks (admin only)."""
        return await controller.enable_plugin(plugin_id)

    @router.post("/{plugin_id}/disable", response_model=APIResponse, summary="Disable Plugin")
    async def disable_plugin(plugin_id: str, current_user = Depends(get_current_admin_user)):
        """Disable a plugin and unregister its hooks (admin only)."""
        return await controller.disable_plugin(plugin_id)

    @router.delete("/{plugin_id}", response_model=APIResponse, summary="Delete Plugin")
    async def delete_plugin(plugin_id: str, current_user = Depends(get_current_admin_user)):
        """Delete a plugin from the database (does not remove files). Re-scan to rediscover (admin only)."""
        return await controller.delete_plugin(plugin_id)

    @router.post("/scan", response_model=APIResponse, summary="Scan for Plugins")
    async def scan_plugins(current_user = Depends(get_current_admin_user)):
        """Rescan plugin directories to discover new or updated plugins (admin only)."""
        return await controller.scan_plugins()

    @router.get("/{plugin_id}/settings", response_model=APIResponse, summary="Get Plugin Settings")
    async def get_plugin_settings(
        plugin_id: str,
        current_user = Depends(get_current_admin_user)
    ):
        """Get a plugin's global settings (admin only).

        Plugin settings can hold credentials, so the caller can no longer name an
        arbitrary `user_id`: this returns the plugin's global (system) settings,
        with any secret values masked by the manager.
        """
        return await controller.get_plugin_settings(plugin_id, user_id=None)

    @router.put("/{plugin_id}/settings", response_model=APIResponse, summary="Update Plugin Settings")
    async def update_plugin_settings(
        plugin_id: str,
        settings_data: PluginSettingsUpdateRequest,
        current_user = Depends(get_current_admin_user)
    ):
        """Update a plugin's global settings (admin only).

        `user_id` is intentionally not accepted from the request - a logged-in
        user could otherwise read or overwrite another user's plugin settings.
        """
        return await controller.update_plugin_settings(
            plugin_id,
            settings_data,
            user_id=None,
            actor_user_id=getattr(current_user, 'id', None),
            actor_username=getattr(current_user, 'username', None),
        )

    @router.get("/settings/audit", response_model=APIResponse, summary="Get Plugin Settings Audit Trail")
    async def get_plugin_settings_audit(
        plugin_id: Optional[str] = None,
        limit: int = 200,
        current_user = Depends(get_current_admin_user)
    ):
        """Who changed which plugin setting and when (admin only).

        Values are never recorded, so this endpoint cannot leak a credential.
        """
        return await controller.get_setting_audit(plugin_id, limit)

    @router.get("/hooks/frontend", response_model=APIResponse, summary="Get Frontend Hooks")
    async def get_frontend_hooks(current_user = Depends(get_current_active_user)):
        """Get all frontend hooks grouped by hook name for frontend component loading."""
        return await controller.get_frontend_hooks()

    @router.get("/hooks/catalog", response_model=APIResponse, summary="Get Hooks Catalog")
    async def get_hooks_catalog(current_user = Depends(get_current_active_user)):
        """Get the full catalog of declared hook points (core + plugin-provided)."""
        return await controller.get_hooks_catalog()

    @router.get("/{plugin_id}/assets/{file_path:path}", summary="Get Plugin Asset")
    async def get_plugin_asset(
        plugin_id: str,
        file_path: str
    ):
        """
        Serve static assets (JS, CSS) from plugin's frontend/dist directory.

        This endpoint serves compiled frontend components and assets for plugins.
        The file_path is relative to the plugin's frontend/dist directory.

        Note: This endpoint is public (no auth required) since plugin assets are
        static files that need to be loaded via dynamic import().
        """
        return await controller.get_plugin_asset(plugin_id, file_path)

    return router
