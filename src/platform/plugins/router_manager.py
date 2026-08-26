"""
Dynamic plugin API router mount/unmount.

Mounts and unmounts a plugin's API router on the live app. FastAPI/Starlette
routers can be attached to a live `APIRouter`/`FastAPI` app at any point (not
just at import time) via `include_router` - this manager wraps that so
`src.features.plugins.operations.enable_plugin`/`disable_plugin` and the
reload path can mount/unmount a single plugin's routes at runtime without
restarting the process.

Removal works by diffing `app.router.routes` before/after `include_router`
and recording exactly the routes that call added, so `unmount` can remove
only that plugin's routes (not a blanket clear).
"""

import logging
from typing import Dict, List, Optional

from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: Prefix every plugin API router is expected to live under. Manifests that
#: violate this get a warning (not a hard failure - existing plugins may not
#: comply yet).
PLUGIN_ROUTE_PREFIX_TEMPLATE = "/api/plugins/{plugin_id}"


class PluginRouterManager:
    """
    Mounts/unmounts plugin-provided FastAPI routers on a live `FastAPI` app.

    Usage:
        router_manager = PluginRouterManager()
        router_manager.attach(app)          # once, at startup
        router_manager.mount(manifest)      # per plugin, on enable/reload
        router_manager.unmount(plugin_id)   # per plugin, on disable
    """

    def __init__(self, loader=None):
        self._app: Optional[FastAPI] = None
        # plugin_id -> list of routes (Starlette BaseRoute) added for it
        self._plugin_routes: Dict[str, List] = {}
        # loader is only used to load the plugin's `api.module` - injected so
        # callers (PluginRegistry) can share the same PluginLoader instance
        # and its module cache.
        self.loader = loader

    def attach(self, app: FastAPI) -> None:
        """Bind the manager to the running FastAPI app. Call once at startup."""
        self._app = app

    def is_mounted(self, plugin_id: str) -> bool:
        return plugin_id in self._plugin_routes

    def mount(self, manifest, loader=None) -> bool:
        """
        Mount a plugin's `api.module` router(s) (`router` / `ws_router`) onto
        the attached app.

        Idempotent: mounting an already-mounted plugin is a no-op that
        returns True (matches the plugin registry's enable-is-idempotent
        semantics) rather than double-registering routes.

        Returns True if the plugin had no `api` section, or mounted (or was
        already mounted) successfully. Returns False on a load/mount error.

        If `attach(app)` hasn't been called yet, this is a deferred no-op
        (returns True without recording anything mounted) - startup enables
        plugins from the database before the FastAPI app exists yet;
        `mount_all_enabled()` mounts everything once the app is attached.
        """
        if self._app is None:
            logger.debug(
                f"PluginRouterManager not attached to an app yet; deferring mount for {manifest.id}"
            )
            return True

        plugin_id = manifest.id

        if self.is_mounted(plugin_id):
            logger.debug(f"Plugin router for {plugin_id} already mounted; skipping")
            return True

        if not manifest.api_routes or not manifest.api_routes.get("module"):
            # No API router declared - nothing to do, not an error.
            self._plugin_routes[plugin_id] = []
            return True

        loader = loader or self.loader
        if loader is None:
            logger.error(f"Cannot mount plugin router for {plugin_id}: no PluginLoader available")
            return False

        module_ref = manifest.api_routes["module"].replace(".py", "").replace("/", ".")

        try:
            module = loader.load_plugin_module(manifest, module_ref)
        except Exception as e:
            logger.error(f"Failed to load API module for plugin {plugin_id}: {e}", exc_info=True)
            return False

        if module is None:
            logger.error(f"Failed to load API module for plugin {plugin_id}: module not found")
            return False

        self._validate_route_prefix(plugin_id, module)

        before = list(self._app.router.routes)
        try:
            if hasattr(module, "router"):
                self._app.include_router(module.router)
                logger.info(f"Mounted plugin API router: {plugin_id}")
            if hasattr(module, "ws_router"):
                self._app.include_router(module.ws_router)
                logger.info(f"Mounted plugin WebSocket router: {plugin_id}")
        except Exception as e:
            logger.error(f"Failed to mount plugin router for {plugin_id}: {e}", exc_info=True)
            return False

        after = self._app.router.routes
        added = after[len(before):] if after[:len(before)] == before else [r for r in after if r not in before]
        self._plugin_routes[plugin_id] = added
        return True

    def unmount(self, plugin_id: str) -> bool:
        """
        Remove exactly the routes `mount()` recorded for `plugin_id`.

        Returns True whether or not the plugin was mounted (unmounting a
        never-mounted or already-unmounted plugin is a no-op).
        """
        if self._app is None:
            self._plugin_routes.pop(plugin_id, None)
            return True

        routes = self._plugin_routes.pop(plugin_id, None)
        if not routes:
            return True

        route_ids = {id(r) for r in routes}
        self._app.router.routes[:] = [
            r for r in self._app.router.routes if id(r) not in route_ids
        ]
        logger.info(f"Unmounted {len(routes)} route(s) for plugin: {plugin_id}")
        return True

    def mount_all_enabled(self, manifests, loader=None) -> Dict[str, bool]:
        """
        Mount every already-enabled plugin's API router. Used at startup once
        the app + all controllers are wired up.

        Args:
            manifests: iterable of enabled PluginManifest objects
            loader: PluginLoader to use (defaults to self.loader)

        Returns:
            {plugin_id: mounted_ok} for every manifest with an `api` section.
        """
        results = {}
        for manifest in manifests:
            if manifest.api_routes and manifest.api_routes.get("module"):
                results[manifest.id] = self.mount(manifest, loader=loader)
        return results

    @staticmethod
    def _validate_route_prefix(plugin_id: str, module) -> None:
        """
        Warn (never hard-fail) when a plugin's router doesn't live under
        `/api/plugins/{plugin_id}`. Existing plugins may violate this -
        this is enforcement-by-visibility, not a gate.
        """
        expected_prefix = PLUGIN_ROUTE_PREFIX_TEMPLATE.format(plugin_id=plugin_id)
        for attr in ("router", "ws_router"):
            router = getattr(module, attr, None)
            if router is None:
                continue
            prefix = getattr(router, "prefix", "") or ""
            if prefix and not prefix.startswith(expected_prefix):
                logger.warning(
                    f"Plugin '{plugin_id}' {attr} prefix '{prefix}' does not start with "
                    f"expected '{expected_prefix}' - plugin API routes should be namespaced "
                    f"under /api/plugins/{{plugin_id}}"
                )
