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
from enum import Enum
from typing import Dict, List, Optional

from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: Prefix every plugin API router is expected to live under. Manifests that
#: violate this get a warning (not a hard failure - existing plugins may not
#: comply yet).
PLUGIN_ROUTE_PREFIX_TEMPLATE = "/api/plugins/{plugin_id}"

#: What an enable records as its error when the plugin's router won't mount.
MOUNT_FAILURE_MESSAGE = "Failed to mount plugin API router"


class MountResult(Enum):
    """Outcome of `PluginRouterMounter.mount`.

    DEFERRED is not success: the plugin's routes do not exist yet, because no
    app was attached when the mount was attempted. Whoever holds the plugin's
    lifecycle owes it a second mount once `attach()` has happened
    (`mount_all_enabled`), and must treat a failure there as a failed enable -
    otherwise a plugin sits ENABLED with live hooks and no routes.
    """

    MOUNTED = "mounted"
    DEFERRED = "deferred"
    FAILED = "failed"

    def __bool__(self) -> bool:
        """`if not mounter.mount(...)` still reads as "did it fail?"."""
        return self is not MountResult.FAILED


class PluginRouterMounter:
    """
    Mounts/unmounts plugin-provided FastAPI routers on a live `FastAPI` app.

    Usage:
        router_mounter = PluginRouterMounter()
        router_mounter.attach(app)          # once, at startup
        router_mounter.mount(manifest)      # per plugin, on enable/reload
        router_mounter.unmount(plugin_id)   # per plugin, on disable
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

    def mount(self, manifest, loader=None) -> MountResult:
        """
        Mount a plugin's `api.module` router(s) (`router` / `ws_router`) onto
        the attached app.

        Idempotent: mounting an already-mounted plugin is a no-op that reports
        MOUNTED (matching the plugin registry's enable-is-idempotent
        semantics) rather than double-registering routes.

        Returns MOUNTED when the routes are live or the plugin declares none,
        FAILED on a load/mount error, and DEFERRED when `attach(app)` hasn't
        happened yet - startup enables plugins from the database before the
        FastAPI app exists, and `mount_all_enabled()` mounts them for real once
        the app is attached. DEFERRED is falsy-safe (it is not FAILED) but it
        is not "mounted": see MountResult.
        """
        if self._app is None:
            logger.debug(
                f"PluginRouterMounter not attached to an app yet; deferring mount for {manifest.id}"
            )
            return MountResult.DEFERRED

        plugin_id = manifest.id

        if self.is_mounted(plugin_id):
            logger.debug(f"Plugin router for {plugin_id} already mounted; skipping")
            return MountResult.MOUNTED

        if not manifest.api_routes or not manifest.api_routes.get("module"):
            # No API router declared - nothing to do, not an error.
            self._plugin_routes[plugin_id] = []
            return MountResult.MOUNTED

        loader = loader or self.loader
        if loader is None:
            logger.error(f"Cannot mount plugin router for {plugin_id}: no PluginLoader available")
            return MountResult.FAILED

        module_ref = manifest.api_routes["module"].replace(".py", "").replace("/", ".")

        try:
            module = loader.load_plugin_module(manifest, module_ref)
        except Exception as e:
            logger.error(f"Failed to load API module for plugin {plugin_id}: {e}", exc_info=True)
            return MountResult.FAILED

        if module is None:
            logger.error(f"Failed to load API module for plugin {plugin_id}: module not found")
            return MountResult.FAILED

        self._validate_route_prefix(plugin_id, module)

        # Identity, not equality: Starlette's Route.__eq__ compares path +
        # endpoint + methods, so a plugin route that looks like an existing one
        # would be mistaken for it and left behind on both paths below.
        before_ids = {id(r) for r in self._app.router.routes}
        try:
            if hasattr(module, "router"):
                self._app.include_router(module.router)
                logger.info(f"Mounted plugin API router: {plugin_id}")
            if hasattr(module, "ws_router"):
                self._app.include_router(module.ws_router)
                logger.info(f"Mounted plugin WebSocket router: {plugin_id}")
        except Exception as e:
            logger.error(f"Failed to mount plugin router for {plugin_id}: {e}", exc_info=True)
            # A partial mount (e.g. `router` included, `ws_router` blew up)
            # would otherwise leave live routes nobody owns - `unmount` only
            # removes what a *successful* mount recorded.
            removed = self._discard_routes_added_since(before_ids)
            if removed:
                logger.info(f"Removed {removed} partially mounted route(s) for plugin: {plugin_id}")
            return MountResult.FAILED

        self._plugin_routes[plugin_id] = [
            r for r in self._app.router.routes if id(r) not in before_ids
        ]
        return MountResult.MOUNTED

    def _discard_routes_added_since(self, before_ids: set) -> int:
        """Drop every route not present when `before_ids` was taken."""
        routes = self._app.router.routes
        kept = [r for r in routes if id(r) in before_ids]
        removed = len(routes) - len(kept)
        routes[:] = kept
        return removed

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

    def mount_all_enabled(self, manifests, loader=None) -> Dict[str, MountResult]:
        """
        Mount every already-enabled plugin's API router. Used at startup once
        the app + all controllers are wired up.

        Args:
            manifests: iterable of enabled PluginManifest objects
            loader: PluginLoader to use (defaults to self.loader)

        Returns:
            {plugin_id: MountResult} for every manifest with an `api` section.
            The caller owns what a FAILED entry means for that plugin's
            lifecycle - these plugins are already ENABLED, so a failure here
            has to be pushed back into the registry.
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
