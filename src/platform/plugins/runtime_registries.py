"""Process-wide runtime singletons for plugins and late-bound lookups.

Populated during composition (``src.bootstrap.container.build_container``) and
read by plugin backends and by core call sites that resolve their dependencies
lazily — they cannot import the composition root at module scope without
creating an import cycle.
"""

from src.platform.plugins.registry import PluginRegistry

# Global registry references (set during container construction)
_global_plugin_registry: PluginRegistry = None
_global_tool_registry = None
_global_notification_manager = None
# The live AppContainer, set by create_app(). Plugin backends resolve their
# singletons off this rather than importing the composition root.
_container = None


def get_global_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry singleton used by the application.

    Falls back to constructing a fresh registry when the container has not
    populated the global yet. In a real process `build_container()` always
    sets it first, but provider/backends test paths (and any headless caller
    that never runs `create_app()`) reach this accessor directly and rely on
    the fallback rather than a hard failure.
    """
    global _global_plugin_registry
    if _global_plugin_registry is None:
        _global_plugin_registry = PluginRegistry(
            marketplace_dir="content/plugins/marketplace",
            local_dir="content/plugins/local",
        )
    return _global_plugin_registry


def get_global_tool_registry():
    """Get the global tool registry singleton used by the application."""
    return _global_tool_registry


def get_global_notification_manager():
    """
    Get the global notify callable used by the application.

    A bound callable (`functools.partial(operations.notify, collaborators)`,
    see `src.bootstrap.container`), not a class instance - call it directly,
    duck-typed as `get_global_notification_manager()(...)`.

    Raises:
        RuntimeError: If accessed before the container has been built
            (e.g. during early startup ordering). Callers such as plugin
            lifecycle handlers should catch this and no-op.
    """
    global _global_notification_manager
    if _global_notification_manager is None:
        raise RuntimeError("notify callable not initialized yet")
    return _global_notification_manager


def get_container():
    """Get the live AppContainer built by create_app().

    Raises:
        RuntimeError: If accessed before the application has been composed.
    """
    global _container
    if _container is None:
        raise RuntimeError("AppContainer not initialized yet")
    return _container
